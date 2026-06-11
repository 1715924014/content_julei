from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.classification import classify_suggestion, detect_quality_type, detect_urgency
from src.domain import INPUT_FIELDS, Suggestion
from src.storage import Storage
from src.text_processing import content_hash, normalize_text, validate_suggestion


@dataclass
class BatchResult:
    batch_id: int
    rows_read: int
    rows_created: int
    rows_skipped: int
    rows_failed: int


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = [field for field in INPUT_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"输入文件缺少字段：{', '.join(missing)}")
        return list(reader)


def source_row_from_csv(row: dict[str, str]) -> dict[str, str]:
    return {
        "source_suggestion_id": row["suggestion_id"],
        "submit_date": row.get("submit_date", ""),
        "created_at": row.get("submit_date", ""),
        "raw_text": row.get("raw_text", ""),
        "department": row.get("department", ""),
        "job_group": row.get("job_group", ""),
        "work_location": row.get("work_location", ""),
        "scenario": row.get("scenario", ""),
        "status": row.get("status", ""),
    }


def run_csv_import_batch(storage: Storage, input_path: Path) -> BatchResult:
    rows = read_csv(input_path)
    batch_id = storage.start_import_batch("csv", cursor_start="0")
    seen_hashes: set[str] = set()
    rows_created = 0
    rows_skipped = 0
    rows_failed = 0
    cursor_end = "0"
    error_summary: str | None = None

    for row in rows:
        try:
            source_row = source_row_from_csv(row)
            source_suggestion_id = source_row["source_suggestion_id"]
            created = storage.upsert_source_suggestion(source_row, import_batch_id=batch_id)
            if created:
                rows_created += 1
            else:
                rows_skipped += 1

            suggestion = Suggestion({field: row.get(field, "").strip() for field in INPUT_FIELDS})
            flags = validate_suggestion(suggestion, seen_hashes)
            primary, secondary, owner, confidence = classify_suggestion(
                suggestion.raw_text,
                suggestion.fields.get("scenario", ""),
            )
            quality = detect_quality_type(suggestion.raw_text, flags)
            urgency = detect_urgency(suggestion.raw_text, primary)
            review_required = "是" if flags or confidence < 0.6 or quality in {"信息不足", "情绪表达"} else "否"

            storage.upsert_suggestion_analysis(
                {
                    "source_suggestion_id": source_suggestion_id,
                    "batch_id": batch_id,
                    "normalized_text": normalize_text(suggestion.raw_text),
                    "content_hash": content_hash(suggestion.raw_text),
                    "primary_category": primary,
                    "secondary_category": secondary,
                    "owner_department": suggestion.fields.get("owner_department", "") or owner,
                    "quality_type": quality,
                    "urgency_level": urgency,
                    "classification_confidence": confidence,
                    "embedding_status": "pending",
                    "embedding_model": None,
                    "embedding_ref": None,
                    "review_required": review_required,
                    "analysis_status": "classified",
                }
            )
            cursor_end = source_suggestion_id
        except Exception as exc:
            rows_failed += 1
            error_summary = str(exc)

    storage.finish_import_batch(
        batch_id,
        cursor_end,
        rows_read=len(rows),
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        rows_failed=rows_failed,
        error_summary=error_summary,
    )
    return BatchResult(batch_id, len(rows), rows_created, rows_skipped, rows_failed)
