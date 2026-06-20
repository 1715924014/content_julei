import unittest

from src.embeddings import HashEmbeddingProvider, cosine_similarity
from src.matching import MatchEvidence, decide_cluster_match
from src.vector_index import ClusterVector, InMemoryVectorIndex


class VectorRetrievalTests(unittest.TestCase):
    def test_hash_embedding_provider_embeddings_are_deterministic(self):
        provider = HashEmbeddingProvider()

        first = provider.embed("canteen hot meal request")
        second = provider.embed("canteen hot meal request")

        self.assertEqual(first, second)
        self.assertEqual(len(first), provider.dimensions)

    def test_cosine_similarity_orders_related_text_higher_than_unrelated_text(self):
        provider = HashEmbeddingProvider()
        query = provider.embed("canteen hot meal request")
        related = provider.embed("canteen meal needs hot food")
        unrelated = provider.embed("salary bonus explanation")

        self.assertGreater(cosine_similarity(query, related), cosine_similarity(query, unrelated))

    def test_in_memory_vector_index_filters_and_sorts_candidate_clusters(self):
        provider = HashEmbeddingProvider()
        index = InMemoryVectorIndex(
            [
                ClusterVector(
                    cluster_id="C001",
                    text="canteen hot meal request",
                    vector=provider.embed("canteen hot meal request"),
                    primary_category="logistics",
                    secondary_category="canteen",
                    owner_department="admin",
                    active=True,
                ),
                ClusterVector(
                    cluster_id="C002",
                    text="canteen cold rice feedback",
                    vector=provider.embed("canteen cold rice feedback"),
                    primary_category="logistics",
                    secondary_category="canteen",
                    owner_department="admin",
                    active=True,
                ),
                ClusterVector(
                    cluster_id="C003",
                    text="equipment repair request",
                    vector=provider.embed("equipment repair request"),
                    primary_category="equipment",
                    secondary_category="repair",
                    owner_department="maintenance",
                    active=True,
                ),
                ClusterVector(
                    cluster_id="C004",
                    text="inactive canteen meal cluster",
                    vector=provider.embed("inactive canteen meal cluster"),
                    primary_category="logistics",
                    secondary_category="canteen",
                    owner_department="admin",
                    active=False,
                ),
            ]
        )

        candidates = index.search(
            provider.embed("canteen hot food"),
            primary_category="logistics",
            secondary_category="canteen",
            owner_department="admin",
            active=True,
            top_k=2,
        )

        self.assertEqual([candidate.cluster.cluster_id for candidate in candidates], ["C001", "C002"])
        self.assertGreaterEqual(candidates[0].vector_score, candidates[1].vector_score)


class MatchDecisionTests(unittest.TestCase):
    def test_auto_merges_high_score_without_conflicts(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C001",
                vector_score=0.9,
                keyword_score=0.85,
                same_scenario=True,
                same_owner_department=True,
                category_confidence=0.9,
                conflict_flags=[],
            )
        )

        self.assertEqual(decision.decision_type, "auto_merge")
        self.assertEqual(decision.cluster_id, "C001")
        self.assertGreaterEqual(decision.final_score, 0.86)

    def test_sends_medium_score_to_manual_review(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C002",
                vector_score=0.79,
                keyword_score=0.7,
                same_scenario=False,
                same_owner_department=False,
                category_confidence=0.8,
                conflict_flags=[],
            )
        )

        self.assertEqual(decision.decision_type, "manual_review")
        self.assertEqual(decision.cluster_id, "C002")
        self.assertGreaterEqual(decision.final_score, 0.72)
        self.assertLess(decision.final_score, 0.86)

    def test_conflict_flags_force_manual_review_even_when_score_high(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C003",
                vector_score=0.95,
                keyword_score=0.95,
                same_scenario=True,
                same_owner_department=True,
                category_confidence=0.95,
                conflict_flags=["different_owner_department"],
            )
        )

        self.assertEqual(decision.decision_type, "manual_review")
        self.assertEqual(decision.cluster_id, "C003")
        self.assertGreaterEqual(decision.final_score, 0.86)

    def test_reviewer_rejected_similarity_creates_new_cluster(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C003",
                vector_score=0.95,
                keyword_score=0.95,
                same_scenario=True,
                same_owner_department=True,
                category_confidence=0.95,
                conflict_flags=["review_rejected_similar_pair"],
            )
        )

        self.assertEqual(decision.decision_type, "create_new_cluster")
        self.assertIsNone(decision.cluster_id)
        self.assertEqual(decision.decision_reason, "review_rejected_similar_pair")

    def test_reviewer_approved_similarity_auto_merges_manual_band_score(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C005",
                vector_score=0.79,
                keyword_score=0.7,
                same_scenario=False,
                same_owner_department=False,
                category_confidence=0.8,
                conflict_flags=["review_approved_similar_pair"],
            )
        )

        self.assertEqual(decision.decision_type, "auto_merge")
        self.assertEqual(decision.cluster_id, "C005")
        self.assertEqual(decision.decision_reason, "review_approved_similar_pair")

    def test_low_score_creates_new_cluster(self):
        decision = decide_cluster_match(
            MatchEvidence(
                candidate_cluster_id="C004",
                vector_score=0.5,
                keyword_score=0.4,
                same_scenario=False,
                same_owner_department=False,
                category_confidence=0.55,
                conflict_flags=[],
            )
        )

        self.assertEqual(decision.decision_type, "create_new_cluster")
        self.assertIsNone(decision.cluster_id)
        self.assertLess(decision.final_score, 0.72)


if __name__ == "__main__":
    unittest.main()
