from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from src.classification import classify_suggestion, detect_quality_type, detect_urgency
from src.domain import INPUT_FIELDS, Suggestion
from src.embeddings import HashEmbeddingProvider
from src.matching import MatchEvidence, decide_cluster_match
from src.storage import Storage
from src.text_processing import content_hash, normalize_text, text_features, validate_suggestion
from src.vector_index import InMemoryVectorIndex


@dataclass
class BatchResult:
    batch_id: int
    rows_read: int
    rows_created: int
    rows_skipped: int
    rows_failed: int
    cursor_start: str
    cursor_end: str
    error_summary: str


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
        "owner_department": row.get("owner_department", ""),
    }


def keyword_overlap_score(left: str, right: str) -> float:
    left_features = text_features(left)
    right_features = text_features(right)
    if not left_features or not right_features:
        return 0.0
    return round(len(left_features & right_features) / len(left_features | right_features), 4)


def persist_cluster_decision(
    storage: Storage,
    *,
    source_suggestion_id: str,
    normalized_text: str,
    scenario_key: str,
    primary_category: str,
    secondary_category: str,
    owner_department: str,
    category_confidence: float,
    embedding: list[float],
) -> None:
    index = InMemoryVectorIndex(
        storage.list_active_cluster_vectors(
            primary_category=primary_category,
            secondary_category=secondary_category,
            owner_department=owner_department,
        )
    )
    candidates = index.search(
        embedding,
        primary_category=primary_category,
        secondary_category=secondary_category,
        owner_department=owner_department,
        active=True,
        top_k=1,
    )

    if not candidates:
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id=source_suggestion_id,
            normalized_text=normalized_text,
            primary_category=primary_category,
            secondary_category=secondary_category,
            owner_department=owner_department,
            scenario_key=scenario_key,
            centroid_embedding=embedding,
        )
        storage.add_cluster_member(
            cluster_id=cluster_id,
            source_suggestion_id=source_suggestion_id,
            decision_type="create_new_cluster",
            vector_score=0.0,
            keyword_score=0.0,
            final_score=0.0,
            decision_status="accepted",
            decision_reason="no_candidate_cluster",
        )
        storage.upsert_action_item_for_cluster(cluster_id)
        return

    candidate = candidates[0]
    cluster = storage.get_issue_cluster(candidate.cluster.cluster_id)
    keyword_score = keyword_overlap_score(normalized_text, candidate.cluster.text)
    conflict_flags: list[str] = []
    for rejected_text in storage.list_rejected_member_texts(candidate.cluster.cluster_id):
        if keyword_overlap_score(normalized_text, rejected_text) >= 0.8:
            conflict_flags.append("review_rejected_similar_pair")
            break
    if not conflict_flags:
        for approved_text in storage.list_review_approved_member_texts(candidate.cluster.cluster_id):
            if keyword_overlap_score(normalized_text, approved_text) >= 0.8:
                conflict_flags.append("review_approved_similar_pair")
                break
    evidence = MatchEvidence(
        candidate_cluster_id=candidate.cluster.cluster_id,
        vector_score=candidate.vector_score,
        keyword_score=keyword_score,
        same_scenario=(cluster["scenario_key"] or "") == (scenario_key or ""),
        same_owner_department=candidate.cluster.owner_department == owner_department,
        category_confidence=category_confidence,
        conflict_flags=conflict_flags,
    )
    decision = decide_cluster_match(evidence)

    if decision.decision_type == "create_new_cluster":
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id=source_suggestion_id,
            normalized_text=normalized_text,
            primary_category=primary_category,
            secondary_category=secondary_category,
            owner_department=owner_department,
            scenario_key=scenario_key,
            centroid_embedding=embedding,
        )
        storage.add_cluster_member(
            cluster_id=cluster_id,
            source_suggestion_id=source_suggestion_id,
            decision_type=decision.decision_type,
            vector_score=candidate.vector_score,
            keyword_score=keyword_score,
            final_score=decision.final_score,
            decision_status="accepted",
            decision_reason=decision.decision_reason,
        )
        storage.upsert_action_item_for_cluster(cluster_id)
        return

    cluster_id = decision.cluster_id or candidate.cluster.cluster_id
    decision_status = "accepted" if decision.decision_type == "auto_merge" else "pending"
    storage.add_cluster_member(
        cluster_id=cluster_id,
        source_suggestion_id=source_suggestion_id,
        decision_type=decision.decision_type,
        vector_score=candidate.vector_score,
        keyword_score=keyword_score,
        final_score=decision.final_score,
        decision_status=decision_status,
        decision_reason=decision.decision_reason,
    )
    if decision.decision_type == "manual_review":
        storage.create_review_task(
            source_suggestion_id=source_suggestion_id,
            candidate_cluster_id=decision.cluster_id,
            task_type="cluster_match",
            priority=1,
            evidence={
                "vector_score": round(candidate.vector_score, 4),
                "keyword_score": keyword_score,
                "final_score": decision.final_score,
                "decision_reason": decision.decision_reason,
            },
        )
    storage.upsert_action_item_for_cluster(cluster_id)


def run_rows_import_batch(
    storage: Storage,
    rows: list[dict[str, str]],
    *,
    source_name: str,
    cursor_start: str = "0",
    cursor_field: str = "suggestion_id",
    embedding_provider: object | None = None,
) -> BatchResult:
    with storage.defer_commits():
        batch_id = storage.start_import_batch(source_name, cursor_start=cursor_start)
        if embedding_provider is None:
            embedding_provider = HashEmbeddingProvider()
        seen_hashes: set[str] = set()
        rows_created = 0
        rows_skipped = 0
        rows_failed = 0
        cursor_end = cursor_start
        error_summary: str | None = None

        for row_number, row in enumerate(rows, start=1):
            source_suggestion_id = str(row.get("suggestion_id", "")).strip()
            row_cursor = str(row.get(cursor_field) or source_suggestion_id)
            try:
                source_row = source_row_from_csv(row)
                source_suggestion_id = source_row["source_suggestion_id"]
                row_cursor = str(row.get(cursor_field) or source_suggestion_id)
                created = storage.upsert_source_suggestion(source_row, import_batch_id=batch_id)
                if created:
                    rows_created += 1
                    storage.clear_cluster_members_for_source(source_suggestion_id)
                    storage.clear_review_tasks_for_source(source_suggestion_id)
                else:
                    rows_skipped += 1
                    cursor_end = row_cursor
                    continue

                suggestion = Suggestion({field: row.get(field, "").strip() for field in INPUT_FIELDS})
                flags = validate_suggestion(suggestion, seen_hashes)
                primary, secondary, owner, confidence = classify_suggestion(
                    suggestion.raw_text,
                    suggestion.fields.get("scenario", ""),
                )
                quality = detect_quality_type(suggestion.raw_text, flags)
                urgency = detect_urgency(suggestion.raw_text, primary)
                review_required = "是" if flags or confidence < 0.6 or quality in {"信息不足", "情绪表达"} else "否"

                normalized_text = normalize_text(suggestion.raw_text)
                row_content_hash = content_hash(suggestion.raw_text)
                cached_embedding = storage.get_cached_embedding_by_content_hash(row_content_hash)
                if cached_embedding is None:
                    embedding = embedding_provider.embed(normalized_text)
                    embedding_status = "embedded"
                else:
                    embedding = cached_embedding
                    embedding_status = "cached"
                analysis_row = {
                    "source_suggestion_id": source_suggestion_id,
                    "batch_id": batch_id,
                    "normalized_text": normalized_text,
                    "content_hash": row_content_hash,
                    "primary_category": primary,
                    "secondary_category": secondary,
                    "owner_department": suggestion.fields.get("owner_department", "") or owner,
                    "quality_type": quality,
                    "urgency_level": urgency,
                    "classification_confidence": confidence,
                    "embedding_status": embedding_status,
                    "embedding_model": embedding_provider.model_name,
                    "embedding_ref": json.dumps(embedding),
                    "review_required": review_required,
                    "analysis_status": "classified",
                }

                storage.upsert_suggestion_analysis(analysis_row)
                persist_cluster_decision(
                    storage,
                    source_suggestion_id=source_suggestion_id,
                    normalized_text=normalized_text,
                    scenario_key=suggestion.fields.get("scenario", ""),
                    primary_category=primary,
                    secondary_category=secondary,
                    owner_department=analysis_row["owner_department"],
                    category_confidence=confidence,
                    embedding=embedding,
                )
                cursor_end = row_cursor
            except Exception as exc:
                rows_failed += 1
                error_summary = str(exc)
                storage.record_import_failure(
                    batch_id=batch_id,
                    source_suggestion_id=source_suggestion_id,
                    source_cursor=row_cursor,
                    row_number=row_number,
                    error_message=str(exc),
                    raw_row=row,
                )

        storage.finish_import_batch(
            batch_id,
            cursor_end,
            rows_read=len(rows),
            rows_created=rows_created,
            rows_skipped=rows_skipped,
            rows_failed=rows_failed,
            error_summary=error_summary,
        )
        return BatchResult(
            batch_id,
            len(rows),
            rows_created,
            rows_skipped,
            rows_failed,
            cursor_start,
            cursor_end,
            error_summary or "",
        )


def run_csv_import_batch(storage: Storage, input_path: Path) -> BatchResult:
    rows = read_csv(input_path)
    return run_rows_import_batch(storage, rows, source_name="csv", cursor_start="0")
