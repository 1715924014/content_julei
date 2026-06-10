import unittest

from src.embeddings import HashEmbeddingProvider, cosine_similarity
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


if __name__ == "__main__":
    unittest.main()
