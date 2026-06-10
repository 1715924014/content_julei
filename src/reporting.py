from __future__ import annotations

from collections import Counter
from datetime import date

from src.domain import ANALYSIS_FIELDS, Cluster, Suggestion


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
