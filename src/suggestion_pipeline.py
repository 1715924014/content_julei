from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Iterable

from src.batch import run_csv_import_batch
from src.classification import all_category_keywords, classify_suggestion, detect_quality_type, detect_urgency
from src.doctor import run_doctor_checks
from src.domain import ANALYSIS_FIELDS, INPUT_FIELDS, STATUS_TO_ANALYZE, Cluster, Suggestion
from src.import_jobs import import_mysql_batch, run_daily_mysql_job
from src.reporting import action_item_rows, build_weekly_report, cluster_output_rows, suggestion_output_rows
from src.storage import Storage, connect_analysis_db
from src.text_processing import text_features, validate_suggestion


REVIEW_TASK_EXPORT_FIELDS = [
    "review_task_id",
    "source_suggestion_id",
    "candidate_cluster_id",
    "candidate_cluster_name",
    "task_type",
    "priority",
    "status",
    "raw_text",
    "department",
    "job_group",
    "work_location",
    "scenario",
    "owner_department",
    "evidence_json",
    "review_result",
    "target_cluster_id",
    "reviewed_by",
    "created_at",
]

IMPORT_FAILURE_EXPORT_FIELDS = [
    "import_failure_id",
    "batch_id",
    "source_suggestion_id",
    "source_cursor",
    "row_number",
    "error_message",
    "raw_row_json",
    "created_at",
]

PERSISTED_SUGGESTION_EXPORT_FIELDS = ["source_suggestion_id"] + INPUT_FIELDS + ANALYSIS_FIELDS

PERSISTED_CLUSTER_EXPORT_FIELDS = [
    "cluster_id",
    "cluster_name",
    "cluster_summary",
    "primary_category",
    "secondary_category",
    "suggestion_count",
    "department_count",
    "departments",
    "owner_department",
    "urgency_level",
    "review_required_count",
    "representative_raw_text",
]

PERSISTED_ACTION_ITEM_FIELDS = [
    "action_id",
    "cluster_id",
    "action_title",
    "owner_department",
    "urgency_level",
    "status",
    "suggestion_count",
    "related_suggestion_ids",
    "next_step",
]


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cluster_suggestions(suggestions: list[Suggestion], threshold: float = 0.14) -> list[Cluster]:
    clusters: list[Cluster] = []
    cluster_features: dict[str, set[str]] = {}

    for suggestion in suggestions:
        if suggestion.analysis["quality_type"] == "信息不足":
            cluster_id = "C_INFO_INSUFFICIENT"
            existing = next((cluster for cluster in clusters if cluster.cluster_id == cluster_id), None)
            if existing is None:
                existing = Cluster(cluster_id)
                clusters.append(existing)
                cluster_features[cluster_id] = set()
            existing.suggestions.append(suggestion)
            continue

        features = text_features(
            " ".join(
                [
                    suggestion.raw_text,
                    suggestion.fields.get("scenario", ""),
                    suggestion.analysis["secondary_category"],
                ]
            ),
            keywords=all_category_keywords(),
        )
        best_cluster: Cluster | None = None
        best_score = 0.0
        for cluster in clusters:
            if cluster.cluster_id == "C_INFO_INSUFFICIENT":
                continue
            if cluster.representative.analysis["secondary_category"] != suggestion.analysis["secondary_category"]:
                continue
            score = jaccard(features, cluster_features[cluster.cluster_id])
            if score > best_score:
                best_score = score
                best_cluster = cluster

        if best_cluster is None or best_score < threshold:
            cluster_id = f"C{len([item for item in clusters if item.cluster_id != 'C_INFO_INSUFFICIENT']) + 1:03d}"
            cluster = Cluster(cluster_id, [suggestion])
            clusters.append(cluster)
            cluster_features[cluster_id] = set(features)
        else:
            best_cluster.suggestions.append(suggestion)
            cluster_features[best_cluster.cluster_id].update(features)

    return clusters


def make_cluster_name(cluster: Cluster) -> str:
    representative = cluster.representative
    secondary = representative.analysis["secondary_category"]
    location_terms = Counter(
        item.fields.get("scenario", "").strip()
        for item in cluster.suggestions
        if item.fields.get("scenario", "").strip()
    )
    scenario = location_terms.most_common(1)[0][0] if location_terms else secondary
    if cluster.cluster_id == "C_INFO_INSUFFICIENT":
        return "信息不足建议待补充"
    if scenario in secondary or secondary in scenario:
        return f"{secondary}问题"
    return f"{scenario}{secondary}问题"


def make_cluster_summary(cluster: Cluster) -> str:
    representative = cluster.representative
    departments = sorted({item.fields.get("department", "") for item in cluster.suggestions if item.fields.get("department", "")})
    dept_text = "、".join(departments[:3]) if departments else "未填写部门"
    if len(departments) > 3:
        dept_text += f"等{len(departments)}个部门"
    return f"{len(cluster.suggestions)}条建议集中反映“{representative.analysis['secondary_category']}”，涉及{dept_text}。代表原文：{representative.raw_text}"


def assign_review_required(suggestion: Suggestion, cluster_size: int, department_count: int) -> str:
    if suggestion.analysis["urgency_level"] == "高":
        return "是"
    if cluster_size >= 3:
        return "是"
    if department_count >= 2:
        return "是"
    if float(suggestion.analysis["confidence"]) < 0.6:
        return "是"
    if suggestion.analysis["quality_type"] in {"信息不足", "情绪表达"}:
        return "是"
    if suggestion.analysis["validation_flags"]:
        return "是"
    return "否"


def analyze_rows(rows: list[dict[str, str]]) -> tuple[list[Suggestion], list[Cluster]]:
    seen_text_hashes: set[str] = set()
    suggestions = [Suggestion({field: row.get(field, "").strip() for field in INPUT_FIELDS}) for row in rows]

    for suggestion in suggestions:
        flags = validate_suggestion(suggestion, seen_text_hashes)
        primary, secondary, owner, confidence = classify_suggestion(
            suggestion.raw_text,
            suggestion.fields.get("scenario", ""),
        )
        quality = detect_quality_type(suggestion.raw_text, flags)
        urgency = detect_urgency(suggestion.raw_text, primary)
        status = suggestion.fields.get("status", "").strip()
        suggestion.analysis.update(
            {
                "primary_category": primary,
                "secondary_category": secondary,
                "quality_type": quality,
                "urgency_level": urgency,
                "owner_department": suggestion.fields.get("owner_department", "") or owner,
                "confidence": f"{confidence:.2f}",
                "validation_flags": "；".join(flags),
                "review_required": "待定",
            }
        )
        if status in STATUS_TO_ANALYZE:
            suggestion.fields["status"] = "待复核" if quality in {"信息不足", "情绪表达"} else "待派单"

    clusters = cluster_suggestions(suggestions)
    for cluster in clusters:
        cluster_name = make_cluster_name(cluster)
        cluster_summary = make_cluster_summary(cluster)
        department_count = len({item.fields.get("department", "") for item in cluster.suggestions if item.fields.get("department", "")})
        for suggestion in cluster.suggestions:
            suggestion.analysis["cluster_id"] = cluster.cluster_id
            suggestion.analysis["cluster_name"] = cluster_name
            suggestion.analysis["cluster_summary"] = cluster_summary
            suggestion.analysis["review_required"] = assign_review_required(suggestion, len(cluster.suggestions), department_count)

    return suggestions, clusters


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = [field for field in INPUT_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"输入文件缺少字段：{', '.join(missing)}")
        return list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def create_template(output: Path) -> None:
    write_csv(output, INPUT_FIELDS, [])


def analyze_file(input_path: Path, output_dir: Path) -> None:
    rows = read_csv(input_path)
    suggestions, clusters = analyze_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_csv(output_dir / "suggestions_analyzed.csv", INPUT_FIELDS + ANALYSIS_FIELDS, suggestion_output_rows(suggestions))
    write_csv(
        output_dir / "clusters.csv",
        [
            "cluster_id",
            "cluster_name",
            "cluster_summary",
            "primary_category",
            "secondary_category",
            "suggestion_count",
            "department_count",
            "departments",
            "owner_department",
            "urgency_level",
            "review_required_count",
            "representative_raw_text",
        ],
        cluster_output_rows(clusters),
    )
    write_csv(
        output_dir / "action_items.csv",
        [
            "action_id",
            "cluster_id",
            "action_title",
            "owner_department",
            "urgency_level",
            "status",
            "suggestion_count",
            "related_suggestion_ids",
            "next_step",
        ],
        action_item_rows(clusters),
    )
    (output_dir / "weekly_report.md").write_text(build_weekly_report(suggestions, clusters), encoding="utf-8-sig")


def export_review_tasks(db_path: Path, output: Path) -> int:
    with closing(connect_analysis_db(db_path)) as connection:
        storage = Storage(connection)
        storage.initialize_schema()
        tasks = storage.list_pending_review_tasks()
    rows = [
        {
            field: "" if task.get(field) is None else str(task.get(field, ""))
            for field in REVIEW_TASK_EXPORT_FIELDS
        }
        for task in tasks
    ]
    write_csv(output, REVIEW_TASK_EXPORT_FIELDS, rows)
    return len(rows)


def export_import_failures(db_path: Path, batch_id: int, output: Path) -> int:
    with closing(connect_analysis_db(db_path)) as connection:
        storage = Storage(connection)
        storage.initialize_schema()
        failures = storage.list_import_failures(batch_id)
    rows = stringify_rows(failures, IMPORT_FAILURE_EXPORT_FIELDS)
    write_csv(output, IMPORT_FAILURE_EXPORT_FIELDS, rows)
    return len(rows)


def stringify_rows(rows: Iterable[dict[str, object]], fieldnames: list[str]) -> list[dict[str, str]]:
    return [
        {
            field: "" if row.get(field) is None else str(row.get(field, ""))
            for field in fieldnames
        }
        for row in rows
    ]


def persisted_action_item_rows(cluster_rows: list[dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cluster in cluster_rows:
        cluster_id = str(cluster.get("cluster_id", ""))
        review_count = int(cluster.get("review_required_count") or 0)
        suggestion_count = int(cluster.get("suggestion_count") or 0)
        urgency = str(cluster.get("urgency_level", ""))
        if review_count:
            status = "pending_review"
            next_step = "Review uncertain suggestions before dispatching corrective action."
        elif suggestion_count >= 3 or urgency == "high":
            status = "pending_dispatch"
            next_step = "Dispatch to owner department and track corrective action."
        else:
            status = "watchlist"
            next_step = "Keep monitoring during the next daily import cycle."
        rows.append(
            {
                "action_id": f"A-{cluster_id}",
                "cluster_id": cluster_id,
                "action_title": str(cluster.get("cluster_name", "")),
                "owner_department": str(cluster.get("owner_department", "")),
                "urgency_level": urgency,
                "status": status,
                "suggestion_count": str(suggestion_count),
                "related_suggestion_ids": "",
                "next_step": next_step,
            }
        )
    return rows


def build_persisted_weekly_report(
    suggestion_rows: list[dict[str, object]],
    cluster_rows: list[dict[str, object]],
) -> str:
    category_counts = Counter(str(row.get("primary_category", "")) or "Unclassified" for row in suggestion_rows)
    review_count = sum(1 for row in suggestion_rows if str(row.get("review_required", "")).lower() in {"yes", "y", "true", "是"})
    top_clusters = cluster_rows[:5]
    lines = [
        "# Persisted Analysis Report",
        "",
        "## Overview",
        f"- Suggestions: {len(suggestion_rows)}",
        f"- Active clusters: {len(cluster_rows)}",
        f"- Suggestions requiring review: {review_count}",
        "",
        "## Category Distribution",
    ]
    for category, count in category_counts.most_common():
        lines.append(f"- {category}: {count}")
    lines.extend(["", "## Top Clusters"])
    for cluster in top_clusters:
        lines.append(
            "- "
            f"{cluster.get('cluster_name', '')}: "
            f"{cluster.get('suggestion_count', 0)} suggestions, "
            f"owner {cluster.get('owner_department', '')}"
        )
    return "\n".join(lines) + "\n"


def export_db_results(db_path: Path, output_dir: Path) -> dict[str, int]:
    with closing(connect_analysis_db(db_path)) as connection:
        storage = Storage(connection)
        storage.initialize_schema()
        suggestion_rows = storage.list_persisted_suggestion_export_rows()
        cluster_rows = storage.list_persisted_cluster_export_rows()
        action_rows = storage.list_persisted_action_item_export_rows()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "suggestions_analyzed.csv",
        PERSISTED_SUGGESTION_EXPORT_FIELDS,
        stringify_rows(suggestion_rows, PERSISTED_SUGGESTION_EXPORT_FIELDS),
    )
    write_csv(
        output_dir / "clusters.csv",
        PERSISTED_CLUSTER_EXPORT_FIELDS,
        stringify_rows(cluster_rows, PERSISTED_CLUSTER_EXPORT_FIELDS),
    )
    if not action_rows:
        action_rows = persisted_action_item_rows(cluster_rows)
    write_csv(output_dir / "action_items.csv", PERSISTED_ACTION_ITEM_FIELDS, action_rows)
    (output_dir / "weekly_report.md").write_text(
        build_persisted_weekly_report(suggestion_rows, cluster_rows),
        encoding="utf-8-sig",
    )
    return {
        "suggestions": len(suggestion_rows),
        "clusters": len(cluster_rows),
        "action_items": len(action_rows),
    }


def import_review_results(db_path: Path, input_path: Path) -> dict[str, int]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required_fields = {"review_task_id", "review_result"}
        missing = sorted(required_fields - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"review result file missing fields: {', '.join(missing)}")
        rows = list(reader)

    summary = {"applied": 0, "skipped": 0, "failed": 0}
    with closing(connect_analysis_db(db_path)) as connection:
        storage = Storage(connection)
        storage.initialize_schema()
        for row in rows:
            review_result = (row.get("review_result") or "").strip()
            if not review_result:
                summary["skipped"] += 1
                continue
            try:
                storage.apply_review_task_result(
                    review_task_id=int(row.get("review_task_id") or "0"),
                    review_result=review_result,
                    reviewed_by=(row.get("reviewed_by") or "").strip(),
                    target_cluster_id=(row.get("target_cluster_id") or "").strip() or None,
                )
            except (KeyError, TypeError, ValueError):
                summary["failed"] += 1
            else:
                summary["applied"] += 1
    return summary


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="员工建议分类聚类与整改闭环工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser("template", help="生成员工建议收集模板")
    template_parser.add_argument("--output", required=True, type=Path, help="模板 CSV 输出路径")

    analyze_parser = subparsers.add_parser("analyze", help="分析员工建议 CSV")
    analyze_parser.add_argument("--input", required=True, type=Path, help="员工建议 CSV 输入路径")
    analyze_parser.add_argument("--output-dir", required=True, type=Path, help="分析结果输出目录")
    init_db_parser = subparsers.add_parser("init-db", help="Initialize incremental import database")
    init_db_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")

    doctor_parser = subparsers.add_parser("doctor", help="Run local deployment preflight checks")
    doctor_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    doctor_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    doctor_parser.add_argument("--backup-root", type=Path, default=None, help="Optional backup root to check for write access")

    status_parser = subparsers.add_parser("status", help="Print import status summary as JSON")
    status_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    status_parser.add_argument("--source", default="mysql", help="Import source name")
    status_parser.add_argument("--daily-limit", type=positive_int, default=None, help="Expected daily import row limit")
    status_parser.add_argument(
        "--max-duration-seconds",
        type=positive_int,
        default=None,
        help="Maximum acceptable latest import duration in seconds",
    )
    status_parser.add_argument(
        "--min-throughput-rows-per-second",
        type=positive_float,
        default=None,
        help="Minimum acceptable latest import throughput in rows per second",
    )
    status_parser.add_argument(
        "--fail-on-unhealthy",
        action="store_true",
        help="Return exit code 1 when health.status is not ok",
    )

    export_db_parser = subparsers.add_parser("export-db-results", help="Export persisted analysis results to CSV files")
    export_db_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    export_db_parser.add_argument("--output-dir", required=True, type=Path, help="Persisted report output directory")

    export_review_parser = subparsers.add_parser("export-review-tasks", help="Export pending review tasks to CSV")
    export_review_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    export_review_parser.add_argument("--output", required=True, type=Path, help="Pending review tasks CSV output path")

    export_failure_parser = subparsers.add_parser("export-import-failures", help="Export failed import rows to CSV")
    export_failure_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    export_failure_parser.add_argument("--batch-id", required=True, type=int, help="Import batch id to inspect")
    export_failure_parser.add_argument("--output", required=True, type=Path, help="Failed import rows CSV output path")

    import_review_parser = subparsers.add_parser("import-review-results", help="Import reviewed task decisions from CSV")
    import_review_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    import_review_parser.add_argument("--input", required=True, type=Path, help="Reviewed task decisions CSV input path")

    import_csv_parser = subparsers.add_parser("import-csv", help="Import suggestions CSV incrementally")
    import_csv_parser.add_argument("--input", required=True, type=Path, help="Suggestions CSV input path")
    import_csv_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")

    import_mysql_parser = subparsers.add_parser("import-mysql", help="Import suggestions from a MySQL source")
    import_mysql_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    import_mysql_parser.add_argument("--db", required=True, type=Path, help="SQLite analysis database path")
    import_mysql_parser.add_argument(
        "--cursor",
        default=None,
        help="Last imported source cursor value. Defaults to the latest successful mysql batch cursor.",
    )
    import_mysql_parser.add_argument("--limit", type=positive_int, default=None, help="Maximum source rows to import")

    daily_mysql_parser = subparsers.add_parser("run-daily-mysql", help="Run daily MySQL import and write a job log")
    daily_mysql_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    daily_mysql_parser.add_argument("--db", required=True, type=Path, help="SQLite analysis database path")
    daily_mysql_parser.add_argument("--log-dir", required=True, type=Path, help="Directory for daily job JSON logs")
    daily_mysql_parser.add_argument(
        "--cursor",
        default=None,
        help="Override the latest successful mysql batch cursor for backfill or recovery.",
    )
    daily_mysql_parser.add_argument("--limit", type=positive_int, default=None, help="Maximum source rows to import")
    daily_mysql_parser.add_argument(
        "--min-throughput-rows-per-second",
        type=positive_float,
        default=None,
        help="Minimum acceptable latest import throughput in rows per second",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "template":
        create_template(args.output)
        print(f"已生成模板：{args.output}")
        return 0
    if args.command == "analyze":
        analyze_file(args.input, args.output_dir)
        print(f"已生成分析结果目录：{args.output_dir}")
        return 0
    if args.command == "init-db":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_analysis_db(args.db)) as connection:
            Storage(connection).initialize_schema()
        print(f"Initialized database: {args.db}")
        return 0
    if args.command == "doctor":
        report = run_doctor_checks(config_path=args.config, db_path=args.db, backup_root=args.backup_root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "success" else 1
    if args.command == "status":
        with closing(connect_analysis_db(args.db)) as connection:
            storage = Storage(connection)
            storage.initialize_schema()
            summary = storage.get_import_status_summary(
                args.source,
                daily_limit=args.daily_limit,
                max_duration_seconds=args.max_duration_seconds,
                min_throughput_rows_per_second=args.min_throughput_rows_per_second,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.fail_on_unhealthy and summary["health"]["status"] != "ok":
            return 1
        return 0
    if args.command == "export-db-results":
        summary = export_db_results(args.db, args.output_dir)
        print(
            f"Exported persisted results to {args.output_dir}: "
            f"suggestions={summary['suggestions']}, "
            f"clusters={summary['clusters']}, "
            f"action_items={summary['action_items']}"
        )
        return 0
    if args.command == "export-review-tasks":
        exported = export_review_tasks(args.db, args.output)
        print(f"Exported pending review tasks: {exported} -> {args.output}")
        return 0
    if args.command == "export-import-failures":
        exported = export_import_failures(args.db, args.batch_id, args.output)
        print(f"Exported import failures: {exported} -> {args.output}")
        return 0
    if args.command == "import-review-results":
        summary = import_review_results(args.db, args.input)
        print(
            "Imported review results: "
            f"applied={summary['applied']}, skipped={summary['skipped']}, failed={summary['failed']}"
        )
        return 0 if summary["failed"] == 0 else 1
    if args.command == "import-csv":
        with closing(connect_analysis_db(args.db)) as connection:
            storage = Storage(connection)
            storage.initialize_schema()
            result = run_csv_import_batch(storage, args.input)
        print(
            f"Imported batch {result.batch_id}: read={result.rows_read}, "
            f"created={result.rows_created}, skipped={result.rows_skipped}, failed={result.rows_failed}"
        )
        return 0
    if args.command == "import-mysql":
        result = import_mysql_batch(
            config_path=args.config,
            db_path=args.db,
            cursor_override=args.cursor,
            limit=args.limit,
        )
        print(
            f"Imported MySQL batch {result.batch_id}: read={result.rows_read}, "
            f"created={result.rows_created}, skipped={result.rows_skipped}, failed={result.rows_failed}"
        )
        return 0
    if args.command == "run-daily-mysql":
        return run_daily_mysql_job(
            config_path=args.config,
            db_path=args.db,
            log_dir=args.log_dir,
            cursor_override=args.cursor,
            limit=args.limit,
            min_throughput_rows_per_second=args.min_throughput_rows_per_second,
        )
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
