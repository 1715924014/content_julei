from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from src.domain import Suggestion


SENSITIVE_PATTERNS = [
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{15}(\d{2}[0-9Xx])?\b"),
]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def text_features(text: str, keywords: Iterable[str] | None = None) -> set[str]:
    normalized = normalize_text(text)
    features: set[str] = set()
    if keywords is not None:
        for keyword in keywords:
            keyword = normalize_text(keyword)
            if keyword:
                if keyword in normalized:
                    features.add(keyword)
    for size in (2, 3):
        for index in range(max(0, len(normalized) - size + 1)):
            features.add(normalized[index : index + size])
    return features


def validate_suggestion(suggestion: Suggestion, seen_text_hashes: set[str]) -> list[str]:
    flags: list[str] = []
    text = suggestion.raw_text.strip()
    if not text:
        flags.append("空文本")
    elif len(normalize_text(text)) < 4:
        flags.append("文本过短")

    text_hash = content_hash(text)
    if text_hash in seen_text_hashes:
        flags.append("疑似重复")
    seen_text_hashes.add(text_hash)

    if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
        flags.append("疑似包含敏感身份信息")
    return flags
