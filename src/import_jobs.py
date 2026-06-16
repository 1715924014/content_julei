from __future__ import annotations

import json
import sqlite3
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
    with sqlite3.connect(db_path) as connection:
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
    try:
        batch = import_mysql_batch(
            config_path=config_path,
            db_path=db_path,
            cursor_override=cursor_override,
            limit=limit,
        )
        payload.update(
            {
                "status": "success",
                "batch_id": batch.batch_id,
                "rows_read": batch.rows_read,
                "rows_created": batch.rows_created,
                "rows_skipped": batch.rows_skipped,
                "rows_failed": batch.rows_failed,
            }
        )
        exit_code = 0
    except Exception as exc:
        payload.update(
            {
                "status": "failed",
                "error": str(exc),
            }
        )
        exit_code = 1
    payload["finished_at"] = utc_now()
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Daily MySQL job log: {log_path}")
    return exit_code
