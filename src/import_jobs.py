from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from src.batch import BatchResult, run_rows_import_batch
from src.config import load_app_config
from src.mysql_source import connect_mysql, fetch_incremental_count, fetch_incremental_rows
from src.storage import Storage, connect_analysis_db, utc_now


STALE_DAILY_LOCK_SECONDS = 6 * 60 * 60


def import_mysql_batch(
    *,
    config_path: Path,
    db_path: Path,
    cursor_override: str | None = None,
    limit: int | None = None,
) -> BatchResult:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")
    config = load_app_config(config_path)
    with closing(connect_analysis_db(db_path)) as connection:
        storage = Storage(connection)
        storage.initialize_schema()
        cursor_start = cursor_override
        if cursor_start is None:
            cursor_start = storage.get_latest_successful_cursor("mysql")
        with connect_mysql(config.mysql_source) as source_connection:
            rows = fetch_incremental_rows(
                source_connection,
                config.mysql_source,
                cursor_value=cursor_start,
                limit=limit,
            )
            result = run_rows_import_batch(
                storage,
                rows,
                source_name="mysql",
                cursor_start=cursor_start or "0",
                cursor_field="_source_cursor",
            )
            try:
                result.source_pending_after_batch = fetch_incremental_count(
                    source_connection,
                    config.mysql_source,
                    cursor_value=result.cursor_end,
                )
            except Exception as exc:
                result.source_pending_after_batch = None
                result.source_pending_error_summary = str(exc)
            return result


def is_stale_daily_lock(lock_path: Path, now_iso: str) -> bool:
    try:
        locked_at_text = lock_path.read_text(encoding="utf-8").strip()
        locked_at = datetime.fromisoformat(locked_at_text)
        now = datetime.fromisoformat(now_iso)
    except (OSError, ValueError):
        return False
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - locked_at).total_seconds() > STALE_DAILY_LOCK_SECONDS


def run_daily_mysql_job(
    *,
    config_path: Path,
    db_path: Path,
    log_dir: Path,
    limit: int | None = None,
    cursor_override: str | None = None,
    recommendation_output_dir: Path = Path("data"),
    max_duration_seconds: int | None = None,
    min_throughput_rows_per_second: float | None = None,
) -> int:
    started_monotonic = time.perf_counter()
    started_at = utc_now()
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_timestamp = started_at.replace("+00:00", "Z").replace(":", "")
    log_path = log_dir / f"daily-mysql-{safe_timestamp}.json"
    payload: dict[str, object] = {
        "job": "daily-mysql",
        "status": "running",
        "started_at": started_at,
        "config_path": str(config_path),
        "db_path": str(db_path),
        "limit": limit,
        "cursor_override": cursor_override,
        "recommendation_output_dir": str(recommendation_output_dir),
        "max_duration_seconds": max_duration_seconds,
        "min_throughput_rows_per_second": min_throughput_rows_per_second,
    }
    lock_path = log_dir / "daily-mysql.lock"
    payload["lock_path"] = str(lock_path)
    lock_acquired = False
    stale_lock_replaced = False
    try:
        while True:
            try:
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if is_stale_daily_lock(lock_path, started_at):
                    try:
                        payload["stale_lock_started_at"] = lock_path.read_text(encoding="utf-8").strip()
                    except OSError:
                        pass
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    stale_lock_replaced = True
                    continue
                try:
                    payload["lock_started_at"] = lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                recommended_actions = ["inspect_running_import_or_lock"]
                payload.update(
                    {
                        "status": "failed",
                        "error": "another daily MySQL job is already running",
                        "error_summary": "another daily MySQL job is already running",
                        "recommended_actions": recommended_actions,
                        "recommended_commands": Storage.build_import_recommended_commands(
                            recommended_actions,
                            db_path=str(db_path),
                            config_path=str(config_path),
                            log_dir=str(log_dir),
                            output_dir=str(recommendation_output_dir),
                            limit=limit,
                            max_duration_seconds=max_duration_seconds,
                            min_throughput_rows_per_second=min_throughput_rows_per_second,
                        ),
                    }
                )
                exit_code = 1
                lock_fd = None
                break
        if lock_fd is not None:
            with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
                lock_file.write(started_at)
            lock_acquired = True
            payload["stale_lock_replaced"] = stale_lock_replaced
            try:
                batch = import_mysql_batch(
                    config_path=config_path,
                    db_path=db_path,
                    cursor_override=cursor_override,
                    limit=limit,
                )
                has_failed_rows = batch.rows_failed > 0
                limit_reached = limit is not None and batch.rows_read >= limit
                warnings = ["limit_reached"] if limit_reached else []
                source_pending_after_batch = getattr(batch, "source_pending_after_batch", None)
                if type(source_pending_after_batch) is not int:
                    source_pending_after_batch = None
                source_pending_error_summary = getattr(batch, "source_pending_error_summary", "")
                if type(source_pending_error_summary) is not str:
                    source_pending_error_summary = ""
                recommended_actions: list[str] = []
                if source_pending_after_batch is not None and source_pending_after_batch > 0:
                    warnings.append("source_backlog_remaining")
                    recommended_actions.append("run_additional_import_or_increase_limit")
                if source_pending_error_summary:
                    warnings.append("source_pending_count_unavailable")
                    recommended_actions.append("inspect_source_pending_count")
                payload.update(
                    {
                        "status": "partial" if has_failed_rows else "success",
                        "limit_reached": limit_reached,
                        "warnings": warnings,
                        "recommended_actions": recommended_actions,
                        "batch_id": batch.batch_id,
                        "rows_read": batch.rows_read,
                        "rows_created": batch.rows_created,
                        "rows_skipped": batch.rows_skipped,
                        "rows_failed": batch.rows_failed,
                        "cursor_start": getattr(batch, "cursor_start", ""),
                        "cursor_end": getattr(batch, "cursor_end", ""),
                        "source_pending_after_batch": source_pending_after_batch,
                        "source_pending_error_summary": source_pending_error_summary,
                        "error_summary": getattr(batch, "error_summary", ""),
                    }
                )
                try:
                    with closing(connect_analysis_db(db_path)) as connection:
                        summary = Storage(connection).get_import_status_summary(
                            "mysql",
                            daily_limit=limit,
                            max_duration_seconds=max_duration_seconds,
                            min_throughput_rows_per_second=min_throughput_rows_per_second,
                            command_db_path=str(db_path),
                            command_output_dir=str(recommendation_output_dir),
                        )
                    recommended_actions = list(summary["recommended_actions"]) + recommended_actions
                    payload.update(
                        {
                            "health": summary["health"],
                            "pending_review_tasks": summary["pending_review_tasks"],
                            "latest_successful_cursor": summary["latest_successful_cursor"],
                            "latest_batch_limit_reached": summary["latest_batch_limit_reached"],
                            "latest_batch_duration_seconds": summary["latest_batch_duration_seconds"],
                            "latest_batch_rows_per_second": summary["latest_batch_rows_per_second"],
                            "latest_batch_duration_exceeded": summary["latest_batch_duration_exceeded"],
                            "latest_batch_throughput_below_minimum": summary[
                                "latest_batch_throughput_below_minimum"
                            ],
                        }
                    )
                except Exception as exc:
                    payload["health_summary_error"] = str(exc)
                    payload["health_summary_error_type"] = type(exc).__name__
                recommended_actions = list(dict.fromkeys(recommended_actions))
                payload["recommended_actions"] = recommended_actions
                payload["recommended_commands"] = Storage.build_import_recommended_commands(
                    recommended_actions,
                    db_path=str(db_path),
                    config_path=str(config_path),
                    log_dir=str(log_dir),
                    output_dir=str(recommendation_output_dir),
                    limit=limit,
                    max_duration_seconds=max_duration_seconds,
                    min_throughput_rows_per_second=min_throughput_rows_per_second,
                )
                exit_code = 1 if has_failed_rows else 0
            except Exception as exc:
                recommended_actions = ["run_deployment_doctor"]
                payload.update(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "error_summary": str(exc),
                        "error_type": type(exc).__name__,
                        "warnings": ["daily_import_failed"],
                        "recommended_actions": recommended_actions,
                        "recommended_commands": Storage.build_import_recommended_commands(
                            recommended_actions,
                            db_path=str(db_path),
                            config_path=str(config_path),
                            log_dir=str(log_dir),
                            output_dir=str(recommendation_output_dir),
                            limit=limit,
                            max_duration_seconds=max_duration_seconds,
                            min_throughput_rows_per_second=min_throughput_rows_per_second,
                        ),
                    }
                )
                exit_code = 1
    finally:
        if lock_acquired:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
    payload["finished_at"] = utc_now()
    payload["duration_seconds"] = round(time.perf_counter() - started_monotonic, 3)
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Daily MySQL job log: {log_path}")
    return exit_code
