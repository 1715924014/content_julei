from dataclasses import dataclass
from typing import Sequence


AUTO_MERGE_THRESHOLD = 0.86
MANUAL_REVIEW_THRESHOLD = 0.72


@dataclass(frozen=True)
class MatchEvidence:
    candidate_cluster_id: str
    vector_score: float
    keyword_score: float
    same_scenario: bool
    same_owner_department: bool
    category_confidence: float
    conflict_flags: Sequence[str]


@dataclass(frozen=True)
class MatchDecision:
    decision_type: str
    cluster_id: str | None
    final_score: float
    decision_reason: str


def final_match_score(evidence: MatchEvidence) -> float:
    score = (
        evidence.vector_score * 0.55
        + evidence.keyword_score * 0.25
        + evidence.category_confidence * 0.14
        + (0.03 if evidence.same_scenario else 0.0)
        + (0.03 if evidence.same_owner_department else 0.0)
    )
    return round(min(score, 1.0), 4)


def decide_cluster_match(evidence: MatchEvidence) -> MatchDecision:
    score = final_match_score(evidence)

    if evidence.conflict_flags:
        return MatchDecision(
            decision_type="manual_review",
            cluster_id=evidence.candidate_cluster_id,
            final_score=score,
            decision_reason="conflict_flags_present",
        )

    if score >= AUTO_MERGE_THRESHOLD:
        return MatchDecision(
            decision_type="auto_merge",
            cluster_id=evidence.candidate_cluster_id,
            final_score=score,
            decision_reason="score_above_auto_merge_threshold",
        )

    if score >= MANUAL_REVIEW_THRESHOLD:
        return MatchDecision(
            decision_type="manual_review",
            cluster_id=evidence.candidate_cluster_id,
            final_score=score,
            decision_reason="score_above_manual_review_threshold",
        )

    return MatchDecision(
        decision_type="create_new_cluster",
        cluster_id=None,
        final_score=score,
        decision_reason="score_below_manual_review_threshold",
    )
