from __future__ import annotations

import os
from typing import Any

from src.config import MySQLSourceConfig
from src.domain import INPUT_FIELDS


def quote_identifier(identifier: str) -> str:
    if not identifier or not identifier.replace("_", "").isalnum():
        raise ValueError(f"unsafe SQL identifier: {identifier}")
    return f"`{identifier}`"


def build_incremental_query(
    config: MySQLSourceConfig,
    *,
    cursor_value: str | None = None,
    limit: int | None = None,
) -> tuple[str, list[Any]]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")
    source_columns = sorted(set(config.field_mapping.values()) | {config.cursor_field})
    selected_columns = ", ".join(quote_identifier(column) for column in source_columns)
    query = f"SELECT {selected_columns} FROM {quote_identifier(config.table)}"
    params: list[Any] = []
    if cursor_value not in (None, ""):
        query += f" WHERE {quote_identifier(config.cursor_field)} > %s"
        params.append(cursor_value)
    query += f" ORDER BY {quote_identifier(config.cursor_field)} ASC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    return query, params


def map_mysql_row(row: dict[str, Any], config: MySQLSourceConfig) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for field in INPUT_FIELDS:
        source_column = config.field_mapping.get(field)
        value = row.get(source_column, "") if source_column else ""
        mapped[field] = "" if value is None else str(value)
    cursor_value = row.get(config.cursor_field, "")
    mapped["_source_cursor"] = "" if cursor_value is None else str(cursor_value)
    return mapped


def connect_mysql(config: MySQLSourceConfig):
    password = os.environ.get(config.password_env, "")
    if not password:
        raise RuntimeError(f"MySQL password environment variable is not set: {config.password_env}")
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError(
            "MySQL import requires optional dependency 'pymysql'. "
            "Install it in the runtime environment before using import-mysql."
        ) from exc

    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=password,
        database=config.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_incremental_rows(
    connection,
    config: MySQLSourceConfig,
    *,
    cursor_value: str | None = None,
    limit: int | None = None,
) -> list[dict[str, str]]:
    query, params = build_incremental_query(config, cursor_value=cursor_value, limit=limit)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return [map_mysql_row(dict(row), config) for row in rows]
