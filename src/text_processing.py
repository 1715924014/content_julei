from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from src.domain import Suggestion


CATEGORY_RULES = [
    {
        "primary": "后勤保障",
        "secondary": "食堂饭菜",
        "owner": "后勤部",
        "keywords": ["食堂", "饭", "菜", "热饭", "冷饭", "饭菜", "加热", "夜班饭"],
    },
    {
        "primary": "后勤保障",
        "secondary": "宿舍卫生",
        "owner": "后勤部",
        "keywords": ["宿舍", "厕所", "卫生间", "洗澡", "热水", "味道", "异味"],
    },
    {
        "primary": "设备设施",
        "secondary": "设备维修",
        "owner": "设备部",
        "keywords": ["设备", "维修", "坏了", "修", "空调", "灯", "电梯", "机器"],
    },
    {
        "primary": "安全生产",
        "secondary": "劳保用品",
        "owner": "安全环保部",
        "keywords": ["安全", "粉尘", "口罩", "手套", "劳保", "防护", "危险", "隐患"],
    },
    {
        "primary": "薪酬福利",
        "secondary": "薪酬说明",
        "owner": "人力资源部",
        "keywords": ["工资", "薪资", "奖金", "补贴", "工资条", "社保", "福利"],
    },
    {
        "primary": "流程制度",
        "secondary": "流程效率",
        "owner": "综合管理部",
        "keywords": ["流程", "审批", "报销", "签字", "手续", "制度", "系统"],
    },
    {
        "primary": "管理沟通",
        "secondary": "沟通方式",
        "owner": "人力资源部",
        "keywords": ["领导", "主管", "班长", "沟通", "态度", "骂", "说话", "管理"],
    },
    {
        "primary": "培训发展",
        "secondary": "培训安排",
        "owner": "人力资源部",
        "keywords": ["培训", "学习", "晋升", "技能", "发展", "师傅"],
    },
    {
        "primary": "工作环境",
        "secondary": "环境改善",
        "owner": "行政部",
        "keywords": ["环境", "噪音", "太热", "太冷", "通风", "卫生", "工位"],
    },
]

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
    if keywords is None:
        for rule in CATEGORY_RULES:
            for keyword in rule["keywords"]:
                if keyword in normalized:
                    features.add(keyword)
    else:
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
