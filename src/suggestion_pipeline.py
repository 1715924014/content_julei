from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable

from src.domain import ANALYSIS_FIELDS, INPUT_FIELDS, STATUS_TO_ANALYZE, Cluster, Suggestion
from src.text_processing import CATEGORY_RULES, normalize_text, text_features, validate_suggestion

HIGH_URGENCY_KEYWORDS = ["危险", "隐患", "受伤", "事故", "粉尘", "漏电", "火", "病", "不舒服"]
MEDIUM_URGENCY_KEYWORDS = ["坏了", "没人管", "很久", "影响", "不够", "太大", "太冷", "太热"]
ACTION_KEYWORDS = ["建议", "希望", "能不能", "请", "增加", "减少", "改善", "安排", "解释", "简单"]
EMOTION_KEYWORDS = ["太差", "受不了", "烦", "气", "骂", "没人管", "不舒服"]


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def classify_suggestion(text: str, scenario: str) -> tuple[str, str, str, float]:
    normalized = normalize_text(text + scenario)
    scored_rules: list[tuple[int, int, dict[str, object]]] = []
    for rule in CATEGORY_RULES:
        matched = [keyword for keyword in rule["keywords"] if keyword in normalized]
        if matched:
            scored_rules.append((len(matched), sum(len(item) for item in matched), rule))

    if not scored_rules:
        return "其他", "待人工识别", "综合管理部", 0.35

    scored_rules.sort(key=lambda item: (item[0], item[1]), reverse=True)
    match_count, match_weight, rule = scored_rules[0]
    confidence = min(0.95, 0.55 + match_count * 0.12 + math.log1p(match_weight) * 0.04)
    return str(rule["primary"]), str(rule["secondary"]), str(rule["owner"]), round(confidence, 2)


def detect_quality_type(text: str, flags: Iterable[str]) -> str:
    normalized = normalize_text(text)
    flag_set = set(flags)
    if "疑似重复" in flag_set:
        return "重复问题"
    if "空文本" in flag_set or "文本过短" in flag_set or normalized in {"没有", "无", "不知道", "没啥"}:
        return "信息不足"
    has_action = any(keyword in normalized for keyword in ACTION_KEYWORDS)
    has_problem = len(normalized) >= 6
    has_emotion = any(keyword in normalized for keyword in EMOTION_KEYWORDS)
    if has_action and has_problem:
        return "具体可执行"
    if has_emotion and not has_action:
        return "情绪表达"
    return "问题反馈"


def detect_urgency(text: str, primary_category: str) -> str:
    normalized = normalize_text(text)
    if primary_category == "安全生产" or any(keyword in normalized for keyword in HIGH_URGENCY_KEYWORDS):
        return "高"
    if any(keyword in normalized for keyword in MEDIUM_URGENCY_KEYWORDS):
        return "中"
    return "低"


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
            )
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


def suggestion_output_rows(suggestions: list[Suggestion]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for suggestion in suggestions:
        row = dict(suggestion.fields)
        row["owner_department"] = suggestion.analysis["owner_department"]
        for field_name in ANALYSIS_FIELDS:
            row[field_name] = suggestion.analysis.get(field_name, "")
        rows.append(row)
    return rows


def cluster_output_rows(clusters: list[Cluster]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cluster in clusters:
        representative = cluster.representative
        departments = sorted({item.fields.get("department", "") for item in cluster.suggestions if item.fields.get("department", "")})
        rows.append(
            {
                "cluster_id": cluster.cluster_id,
                "cluster_name": representative.analysis["cluster_name"],
                "cluster_summary": representative.analysis["cluster_summary"],
                "primary_category": representative.analysis["primary_category"],
                "secondary_category": representative.analysis["secondary_category"],
                "suggestion_count": str(len(cluster.suggestions)),
                "department_count": str(len(departments)),
                "departments": "；".join(departments),
                "owner_department": representative.analysis["owner_department"],
                "urgency_level": max((item.analysis["urgency_level"] for item in cluster.suggestions), key={"低": 1, "中": 2, "高": 3}.get),
                "review_required_count": str(sum(1 for item in cluster.suggestions if item.analysis["review_required"] == "是")),
                "representative_raw_text": representative.raw_text,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["suggestion_count"]), row["cluster_id"]))


def action_item_rows(clusters: list[Cluster]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cluster in clusters:
        representative = cluster.representative
        if cluster.cluster_id == "C_INFO_INSUFFICIENT":
            next_step = "补充信息或人工判断是否归档"
        elif representative.analysis["urgency_level"] == "高":
            next_step = "优先派单，要求责任部门确认风险和临时措施"
        elif len(cluster.suggestions) >= 3:
            next_step = "按高频问题派单整改"
        else:
            next_step = "纳入责任部门待办并跟踪反馈"
        rows.append(
            {
                "action_id": f"A-{cluster.cluster_id}",
                "cluster_id": cluster.cluster_id,
                "action_title": representative.analysis["cluster_name"],
                "owner_department": representative.analysis["owner_department"],
                "urgency_level": representative.analysis["urgency_level"],
                "status": "待复核" if representative.analysis["review_required"] == "是" else "待派单",
                "suggestion_count": str(len(cluster.suggestions)),
                "related_suggestion_ids": "；".join(item.suggestion_id for item in cluster.suggestions),
                "next_step": next_step,
            }
        )
    return sorted(rows, key=lambda row: ({"高": 0, "中": 1, "低": 2}[row["urgency_level"]], -int(row["suggestion_count"])))


def build_weekly_report(suggestions: list[Suggestion], clusters: list[Cluster]) -> str:
    today = date.today().isoformat()
    category_counts = Counter(item.analysis["primary_category"] for item in suggestions)
    status_counts = Counter(item.fields.get("status", "") for item in suggestions)
    review_count = sum(1 for item in suggestions if item.analysis["review_required"] == "是")
    high_urgency = [item for item in suggestions if item.analysis["urgency_level"] == "高"]
    top_clusters = cluster_output_rows(clusters)[:5]

    lines = [
        f"# 员工建议整改闭环周报（{today}）",
        "",
        "## 概览",
        f"- 本期建议数：{len(suggestions)}",
        f"- 问题簇数量：{len(clusters)}",
        f"- 需重点复核：{review_count}",
        f"- 高紧急度建议：{len(high_urgency)}",
        "",
        "## 一级分类分布",
    ]
    for category, count in category_counts.most_common():
        lines.append(f"- {category}：{count}")

    lines.extend(["", "## 状态分布"])
    for status, count in status_counts.most_common():
        lines.append(f"- {status or '未填写'}：{count}")

    lines.extend(["", "## 高频问题簇"])
    for row in top_clusters:
        lines.append(
            f"- {row['cluster_name']}：{row['suggestion_count']}条，责任部门：{row['owner_department']}，紧急度：{row['urgency_level']}"
        )

    lines.extend(["", "## 注意事项", "- 报告仅展示部门、岗位、场景等统计信息，不展示员工姓名或工号。", "- 代表原文仅用于帮助理解问题，不替代原始建议。"])
    return "\n".join(lines) + "\n"


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="员工建议分类聚类与整改闭环工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser("template", help="生成员工建议收集模板")
    template_parser.add_argument("--output", required=True, type=Path, help="模板 CSV 输出路径")

    analyze_parser = subparsers.add_parser("analyze", help="分析员工建议 CSV")
    analyze_parser.add_argument("--input", required=True, type=Path, help="员工建议 CSV 输入路径")
    analyze_parser.add_argument("--output-dir", required=True, type=Path, help="分析结果输出目录")
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
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
