from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from src.batch import BatchResult, run_rows_import_batch
from src.config import load_app_config
from src.mysql_source import connect_mysql, fetch_incremental_rows
from src.storage import Storage, utc_now


def import_mysql_batch(
    *,
    config_path: Path,
    db_path: Path,
    cursor_override: str | None = None,
    limit: int | None = None,
) -> BatchResult:
    config = load_app_config(config_path)
    with closing(sqlite3.connect(db_path)) as connection:
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
        return run_rows_import_batch(
            storage,
            rows,
            source_name="mysql",
            cursor_start=cursor_start or "0",
            cursor_field="_source_cursor",
        )


def run_daily_mysql_job(
    *,
    config_path: Path,
    db_path: Path,
    log_dir: Path,
    limit: int | None = None,
    cursor_override: str | None = None,
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
    }
    lock_path = log_dir / "daily-mysql.lock"
    lock_acquired = False
    try:
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            payload.update(
                {
                    "status": "failed",
                    "error": "another daily MySQL job is already running",
                    "error_summary": "another daily MySQL job is already running",
                }
            )
            exit_code = 1
        else:
            with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
                lock_file.write(started_at)
            lock_acquired = True
            try:
                batch = import_mysql_batch(
                    config_path=config_path,
                    db_path=db_path,
                    cursor_override=cursor_override,
                    limit=limit,
                )
                has_failed_rows = batch.rows_failed > 0
                payload.update(
                    {
                        "status": "partial" if has_failed_rows else "success",
                        "batch_id": batch.batch_id,
                        "rows_read": batch.rows_read,
                        "rows_created": batch.rows_created,
                        "rows_skipped": batch.rows_skipped,
                        "rows_failed": batch.rows_failed,
                        "cursor_start": getattr(batch, "cursor_start", ""),
                        "cursor_end": getattr(batch, "cursor_end", ""),
                        "error_summary": getattr(batch, "error_summary", ""),
                    }
                )
                exit_code = 1 if has_failed_rows else 0
            except Exception as exc:
                payload.update(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "error_summary": str(exc),
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
