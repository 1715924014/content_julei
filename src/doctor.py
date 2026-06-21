from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from typing import Any

from src.config import load_app_config
from src.domain import INPUT_FIELDS
from src.storage import Storage, connect_analysis_db


def run_doctor_checks(*, config_path: Path, db_path: Path) -> dict[str, Any]:
    checks = {
        "config_loaded": False,
        "field_mapping_complete": False,
        "password_env_present": False,
        "database_initialized": False,
    }
    issues: list[str] = []

    try:
        config = load_app_config(config_path)
        checks["config_loaded"] = True
    except Exception as exc:
        issues.append(f"config load failed: {exc}")
        return {"status": "failed", "checks": checks, "issues": issues}

    missing_fields = [
        field
        for field in INPUT_FIELDS
        if field not in config.mysql_source.field_mapping
    ]
    if missing_fields:
        issues.append(f"field_mapping missing required fields: {', '.join(missing_fields)}")
    else:
        checks["field_mapping_complete"] = True

    password_env = config.mysql_source.password_env
    if os.environ.get(password_env):
        checks["password_env_present"] = True
    else:
        issues.append(f"{password_env} is missing")

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_analysis_db(db_path)) as connection:
            Storage(connection).initialize_schema()
        checks["database_initialized"] = True
    except Exception as exc:
        issues.append(f"database initialization failed: {exc}")

    status = "success" if all(checks.values()) else "failed"
    return {"status": status, "checks": checks, "issues": issues}
