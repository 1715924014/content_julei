from __future__ import annotations

import hashlib
import math
from typing import Protocol, Sequence

from src.text_processing import text_features


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, text: str) -> list[float]:
        ...


class HashEmbeddingProvider:
    def __init__(self, model_name: str = "local-hash-ngram-v1", dimensions: int = 128) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        if self.dimensions <= 0:
            return vector

        for feature in text_features(text):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0.0:
            return vector
        return [value / magnitude for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    left_magnitude = math.sqrt(sum(value * value for value in left))
    right_magnitude = math.sqrt(sum(value * value for value in right))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0

    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right))
    return dot_product / (left_magnitude * right_magnitude)
