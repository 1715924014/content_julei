from __future__ import annotations

import math
from typing import Iterable

from src.text_processing import normalize_text


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

HIGH_URGENCY_KEYWORDS = ["危险", "隐患", "受伤", "事故", "粉尘", "漏电", "火", "病", "不舒服"]
MEDIUM_URGENCY_KEYWORDS = ["坏了", "没人管", "很久", "影响", "不够", "太大", "太冷", "太热"]
ACTION_KEYWORDS = ["建议", "希望", "能不能", "请", "增加", "减少", "改善", "安排", "解释", "简单"]
EMOTION_KEYWORDS = ["太差", "受不了", "烦", "气", "骂", "没人管", "不舒服"]


def all_category_keywords() -> list[str]:
    return [keyword for rule in CATEGORY_RULES for keyword in rule["keywords"]]


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
