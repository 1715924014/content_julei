from __future__ import annotations

from dataclasses import dataclass, field


INPUT_FIELDS = [
    "suggestion_id",
    "submit_date",
    "raw_text",
    "department",
    "job_group",
    "work_location",
    "scenario",
    "is_anonymous_for_report",
    "status",
    "owner_department",
    "resolution_note",
    "closed_date",
]

ANALYSIS_FIELDS = [
    "primary_category",
    "secondary_category",
    "quality_type",
    "urgency_level",
    "cluster_id",
    "cluster_name",
    "cluster_summary",
    "confidence",
    "review_required",
    "validation_flags",
]

STATUS_TO_ANALYZE = {"", "待识别", "待复核"}


@dataclass
class Suggestion:
    fields: dict[str, str]
    analysis: dict[str, str] = field(default_factory=dict)

    @property
    def suggestion_id(self) -> str:
        return self.fields.get("suggestion_id", "")

    @property
    def raw_text(self) -> str:
        return self.fields.get("raw_text", "")


@dataclass
class Cluster:
    cluster_id: str
    suggestions: list[Suggestion] = field(default_factory=list)

    @property
    def representative(self) -> Suggestion:
        return max(self.suggestions, key=lambda item: len(item.raw_text))
