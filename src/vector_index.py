from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.embeddings import cosine_similarity


@dataclass(frozen=True)
class ClusterVector:
    cluster_id: str
    text: str
    vector: list[float]
    primary_category: str
    secondary_category: str
    owner_department: str
    active: bool = True


@dataclass(frozen=True)
class CandidateCluster:
    cluster: ClusterVector
    vector_score: float


class InMemoryVectorIndex:
    def __init__(self, clusters: Sequence[ClusterVector]) -> None:
        self._clusters = list(clusters)

    def search(
        self,
        query_vector: Sequence[float],
        *,
        primary_category: str | None = None,
        secondary_category: str | None = None,
        owner_department: str | None = None,
        active: bool | None = True,
        top_k: int = 5,
    ) -> list[CandidateCluster]:
        if top_k <= 0:
            return []

        candidates: list[CandidateCluster] = []
        for cluster in self._clusters:
            if primary_category is not None and cluster.primary_category != primary_category:
                continue
            if secondary_category is not None and cluster.secondary_category != secondary_category:
                continue
            if owner_department is not None and cluster.owner_department != owner_department:
                continue
            if active is not None and cluster.active != active:
                continue

            candidates.append(CandidateCluster(cluster, cosine_similarity(query_vector, cluster.vector)))

        candidates.sort(key=lambda item: item.vector_score, reverse=True)
        return candidates[:top_k]
