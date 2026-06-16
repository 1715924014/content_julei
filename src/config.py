from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MySQLSourceConfig:
    host: str
    port: int
    database: str
    user: str
    password_env: str
    table: str
    cursor_field: str
    field_mapping: dict[str, str]


@dataclass(frozen=True)
class AppConfig:
    mysql_source: MySQLSourceConfig


def load_app_config(path: Path) -> AppConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mysql_source = payload["mysql_source"]
    return AppConfig(
        mysql_source=MySQLSourceConfig(
            host=str(mysql_source["host"]),
            port=int(mysql_source.get("port", 3306)),
            database=str(mysql_source["database"]),
            user=str(mysql_source["user"]),
            password_env=str(mysql_source["password_env"]),
            table=str(mysql_source["table"]),
            cursor_field=str(mysql_source["cursor_field"]),
            field_mapping={str(key): str(value) for key, value in mysql_source["field_mapping"].items()},
        )
    )
