# Incremental Vector Suggestion Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current CSV script into a maintainable incremental batch analysis project that can import daily suggestions, persist analysis state, and conservatively cluster similar issues with vector retrieval.

**Architecture:** Keep the existing CSV workflow working while introducing focused modules: domain models, text normalization, classification, embedding, vector search, storage, import batches, and matching. Use SQLite for local development and automated tests, with repository boundaries designed so a MySQL adapter can be added without changing business logic.

**Tech Stack:** Python 3.12, standard library `sqlite3`, `csv`, `argparse`, `unittest`; optional future MySQL adapter via `pymysql`; deterministic local hash embeddings for testable first-version vector behavior.

---

## Scope

This plan implements the first production-shaped milestone:

- Modularize the current pipeline without losing existing CLI behavior.
- Add durable local storage that mirrors the planned MySQL tables.
- Add idempotent incremental import from CSV into the storage layer.
- Add embedding and vector retrieval interfaces with a deterministic local provider.
- Add conservative cluster matching with auto-merge, review, and new-cluster decisions.
- Add tests for incremental import, vector candidate filtering, threshold decisions, and backward compatibility.

This plan does not build a web review UI, deploy a scheduler, or connect to the real mini-program MySQL instance yet. Those become follow-up plans once the local batch core is stable.

## File Structure

- Modify: `src/suggestion_pipeline.py`
  - Keep the public CLI entry point.
  - Delegate legacy CSV analysis to smaller modules.
  - Add new commands after the storage and batch modules exist.
- Create: `src/domain.py`
  - Dataclasses and constants shared across modules.
- Create: `src/text_processing.py`
  - Normalization, features, duplicate hash, validation flags.
- Create: `src/classification.py`
  - Category rules, quality type, urgency, owner assignment.
- Create: `src/reporting.py`
  - CSV row building and weekly Markdown report generation.
- Create: `src/storage.py`
  - SQLite schema, repository methods, idempotent upserts, batch records.
- Create: `src/embeddings.py`
  - Embedding provider protocol, deterministic hash embedding provider, cosine similarity.
- Create: `src/vector_index.py`
  - Active cluster candidate retrieval with hard filters and top-K scoring.
- Create: `src/matching.py`
  - Merge scoring, conflict flags, decision thresholds.
- Create: `src/batch.py`
  - Incremental CSV batch import and analysis orchestration.
- Create: `tests/test_text_processing.py`
  - Focused text and validation tests.
- Create: `tests/test_classification.py`
  - Focused classification tests.
- Create: `tests/test_storage.py`
  - Schema, import batch, and idempotency tests.
- Create: `tests/test_matching.py`
  - Vector candidate and conservative decision tests.
- Modify: `tests/test_suggestion_pipeline.py`
  - Keep existing backward-compatibility tests.
- Modify: `README.md`
  - Add new engineering commands while preserving current quick-start commands.

## Task 1: Extract Domain And Text Processing

**Files:**
- Create: `src/domain.py`
- Create: `src/text_processing.py`
- Modify: `src/suggestion_pipeline.py`
- Create: `tests/test_text_processing.py`
- Modify: `tests/test_suggestion_pipeline.py`

- [ ] **Step 1: Write text processing tests**

Create `tests/test_text_processing.py`:

```python
import unittest

from src.domain import INPUT_FIELDS, Suggestion
from src.text_processing import (
    content_hash,
    normalize_text,
    text_features,
    validate_suggestion,
)


class TextProcessingTests(unittest.TestCase):
    def test_normalize_text_removes_whitespace_and_lowercases(self):
        self.assertEqual(normalize_text(" 夜班 A  "), "夜班a")

    def test_content_hash_is_stable_for_equivalent_spacing(self):
        self.assertEqual(content_hash("夜班 没热饭"), content_hash(" 夜班没热饭 "))

    def test_text_features_include_known_keywords_and_ngrams(self):
        features = text_features("夜班食堂没热饭")
        self.assertIn("食堂", features)
        self.assertIn("夜班", features)

    def test_validate_suggestion_keeps_raw_text_and_detects_duplicate(self):
        first = Suggestion({field: "" for field in INPUT_FIELDS})
        first.fields.update({"suggestion_id": "S001", "raw_text": "夜班没热饭"})
        second = Suggestion({field: "" for field in INPUT_FIELDS})
        second.fields.update({"suggestion_id": "S002", "raw_text": " 夜班 没热饭 "})

        seen = set()
        self.assertEqual(validate_suggestion(first, seen), [])
        self.assertIn("疑似重复", validate_suggestion(second, seen))
        self.assertEqual(first.raw_text, "夜班没热饭")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
python -m unittest tests.test_text_processing -v
```

Expected: FAIL with import errors for `src.domain` or `src.text_processing`.

- [ ] **Step 3: Create domain models**

Create `src/domain.py`:

```python
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
```

- [ ] **Step 4: Create text processing module**

Create `src/text_processing.py`:

```python
from __future__ import annotations

import hashlib
import re
from typing import Iterable

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
    for keyword in keywords or ():
        if keyword in normalized:
            features.add(keyword)
    for keyword in ("食堂", "热饭", "冷饭", "宿舍", "厕所", "粉尘", "口罩", "空调", "报销", "审批", "班长"):
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

    fingerprint = content_hash(text)
    if fingerprint in seen_text_hashes:
        flags.append("疑似重复")
    seen_text_hashes.add(fingerprint)

    if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
        flags.append("疑似包含敏感身份信息")
    return flags
```

- [ ] **Step 5: Re-export imports from the legacy CLI module**

Modify `src/suggestion_pipeline.py` so existing external imports continue to work:

```python
from src.domain import ANALYSIS_FIELDS, INPUT_FIELDS, STATUS_TO_ANALYZE, Cluster, Suggestion
from src.text_processing import normalize_text, text_features, validate_suggestion
```

Remove duplicated definitions only after all existing tests still pass.

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: all existing tests plus `tests.test_text_processing` pass.

- [ ] **Step 7: Commit**

```powershell
git add src/domain.py src/text_processing.py src/suggestion_pipeline.py tests/test_text_processing.py tests/test_suggestion_pipeline.py
git commit -m "refactor: extract suggestion domain and text processing"
```

## Task 2: Extract Classification And Reporting

**Files:**
- Create: `src/classification.py`
- Create: `src/reporting.py`
- Modify: `src/suggestion_pipeline.py`
- Create: `tests/test_classification.py`

- [ ] **Step 1: Write classification tests**

Create `tests/test_classification.py`:

```python
import unittest

from src.classification import classify_suggestion, detect_quality_type, detect_urgency


class ClassificationTests(unittest.TestCase):
    def test_classifies_canteen_feedback(self):
        primary, secondary, owner, confidence = classify_suggestion("夜班食堂没有热饭", "食堂")

        self.assertEqual(primary, "后勤保障")
        self.assertEqual(secondary, "食堂饭菜")
        self.assertEqual(owner, "后勤部")
        self.assertGreaterEqual(confidence, 0.6)

    def test_classifies_safety_feedback_as_high_urgency(self):
        primary, _, _, _ = classify_suggestion("车间粉尘太大，口罩不够用，有隐患", "安全")

        self.assertEqual(primary, "安全生产")
        self.assertEqual(detect_urgency("车间粉尘太大，口罩不够用，有隐患", primary), "高")

    def test_short_feedback_is_information_insufficient(self):
        self.assertEqual(detect_quality_type("没有", ["文本过短"]), "信息不足")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
python -m unittest tests.test_classification -v
```

Expected: FAIL with import error for `src.classification`.

- [ ] **Step 3: Create classification module**

Create `src/classification.py` by moving the current category rules and classification functions from `src/suggestion_pipeline.py`:

```python
from __future__ import annotations

import math

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
ACTION_KEYWORDS = ["建议", "希望", "能不能", "请", "增加", "减少", "改善", "安排", "解释", "简化"]
EMOTION_KEYWORDS = ["太差", "受不了", "烦", "气", "骂", "没人管", "不舒服"]


def all_category_keywords() -> list[str]:
    keywords: list[str] = []
    for rule in CATEGORY_RULES:
        keywords.extend(str(keyword) for keyword in rule["keywords"])
    return keywords


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


def detect_quality_type(text: str, flags: list[str]) -> str:
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
```

- [ ] **Step 4: Create reporting module**

Create `src/reporting.py` and move these four complete functions from `src/suggestion_pipeline.py` into it without changing their bodies:

- `suggestion_output_rows`
- `cluster_output_rows`
- `action_item_rows`
- `build_weekly_report`

The top of `src/reporting.py` must be:

```python
from __future__ import annotations

from collections import Counter
from datetime import date

from src.domain import ANALYSIS_FIELDS, Cluster, Suggestion
```

After the move, remove the four original function definitions from `src/suggestion_pipeline.py` and import them from `src.reporting` in Step 5. This keeps current CSV output behavior identical while making reporting independently testable.

- [ ] **Step 5: Update legacy pipeline imports**

Modify `src/suggestion_pipeline.py` to import from `src.classification` and `src.reporting`:

```python
from src.classification import (
    all_category_keywords,
    classify_suggestion,
    detect_quality_type,
    detect_urgency,
)
from src.reporting import (
    action_item_rows,
    build_weekly_report,
    cluster_output_rows,
    suggestion_output_rows,
)
```

Update `cluster_suggestions` so it calls:

```python
features = text_features(
    " ".join(
        [
            suggestion.raw_text,
            suggestion.fields.get("scenario", ""),
            suggestion.analysis["secondary_category"],
        ]
    ),
    keywords=all_category_keywords(),
)
```

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/classification.py src/reporting.py src/suggestion_pipeline.py tests/test_classification.py
git commit -m "refactor: extract classification and reporting"
```

## Task 3: Add Durable Storage Schema

**Files:**
- Create: `src/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write storage tests**

Create `tests/test_storage.py`:

```python
import sqlite3
import unittest

from src.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.storage = Storage(self.connection)
        self.storage.initialize_schema()

    def test_import_batch_lifecycle(self):
        batch_id = self.storage.start_import_batch("csv", "0")
        self.storage.finish_import_batch(batch_id, "100", rows_read=3, rows_created=2, rows_skipped=1, rows_failed=0)

        batch = self.storage.get_import_batch(batch_id)
        self.assertEqual(batch["status"], "success")
        self.assertEqual(batch["cursor_end"], "100")

    def test_upsert_source_suggestion_is_idempotent(self):
        row = {
            "source_suggestion_id": "S001",
            "submit_date": "2026-06-10",
            "created_at": "2026-06-10T08:00:00",
            "raw_text": "夜班没热饭",
            "department": "生产一部",
            "job_group": "一线",
            "work_location": "A厂区",
            "scenario": "食堂",
            "status": "待识别",
        }

        self.assertTrue(self.storage.upsert_source_suggestion(row))
        self.assertFalse(self.storage.upsert_source_suggestion(row))
        self.assertEqual(self.storage.count_table("source_suggestions"), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m unittest tests.test_storage -v
```

Expected: FAIL with import error for `src.storage`.

- [ ] **Step 3: Implement storage schema and repository**

Create `src/storage.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Storage:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def initialize_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_suggestions (
                source_suggestion_id TEXT PRIMARY KEY,
                submit_date TEXT,
                created_at TEXT,
                raw_text TEXT NOT NULL,
                department TEXT,
                job_group TEXT,
                work_location TEXT,
                scenario TEXT,
                status TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_batches (
                batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                cursor_start TEXT NOT NULL,
                cursor_end TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                rows_read INTEGER NOT NULL DEFAULT 0,
                rows_created INTEGER NOT NULL DEFAULT 0,
                rows_skipped INTEGER NOT NULL DEFAULT 0,
                rows_failed INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT
            );

            CREATE TABLE IF NOT EXISTS suggestion_analysis (
                analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_suggestion_id TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                normalized_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                primary_category TEXT NOT NULL,
                secondary_category TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                quality_type TEXT NOT NULL,
                urgency_level TEXT NOT NULL,
                classification_confidence REAL NOT NULL,
                embedding_status TEXT NOT NULL,
                embedding_model TEXT,
                embedding_ref TEXT,
                review_required TEXT NOT NULL,
                analysis_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS issue_clusters (
                cluster_id TEXT PRIMARY KEY,
                cluster_name TEXT NOT NULL,
                cluster_summary TEXT NOT NULL,
                primary_category TEXT NOT NULL,
                secondary_category TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                scenario_key TEXT,
                status TEXT NOT NULL,
                suggestion_count INTEGER NOT NULL,
                representative_suggestion_id TEXT NOT NULL,
                centroid_embedding_ref TEXT,
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cluster_members (
                cluster_member_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT NOT NULL,
                source_suggestion_id TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                vector_score REAL NOT NULL,
                keyword_score REAL NOT NULL,
                final_score REAL NOT NULL,
                decision_status TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(cluster_id, source_suggestion_id)
            );

            CREATE TABLE IF NOT EXISTS review_tasks (
                review_task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_suggestion_id TEXT NOT NULL,
                candidate_cluster_id TEXT,
                task_type TEXT NOT NULL,
                priority INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                review_result TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(source_suggestion_id, candidate_cluster_id, task_type)
            );

            CREATE TABLE IF NOT EXISTS action_items (
                action_id TEXT PRIMARY KEY,
                cluster_id TEXT NOT NULL UNIQUE,
                action_title TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                urgency_level TEXT NOT NULL,
                status TEXT NOT NULL,
                suggestion_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                next_step TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def start_import_batch(self, source_name: str, cursor_start: str) -> int:
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO import_batches (source_name, cursor_start, started_at, status)
            VALUES (?, ?, ?, ?)
            """,
            (source_name, cursor_start, now, "running"),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_import_batch(
        self,
        batch_id: int,
        cursor_end: str,
        *,
        rows_read: int,
        rows_created: int,
        rows_skipped: int,
        rows_failed: int,
        error_summary: str | None = None,
    ) -> None:
        status = "success" if rows_failed == 0 else "partial"
        self.connection.execute(
            """
            UPDATE import_batches
            SET cursor_end = ?, finished_at = ?, status = ?, rows_read = ?,
                rows_created = ?, rows_skipped = ?, rows_failed = ?, error_summary = ?
            WHERE batch_id = ?
            """,
            (cursor_end, utc_now(), status, rows_read, rows_created, rows_skipped, rows_failed, error_summary, batch_id),
        )
        self.connection.commit()

    def get_import_batch(self, batch_id: int) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM import_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown import batch: {batch_id}")
        return row

    def upsert_source_suggestion(self, row: dict[str, Any]) -> bool:
        now = utc_now()
        existing = self.connection.execute(
            "SELECT raw_text, status FROM source_suggestions WHERE source_suggestion_id = ?",
            (row["source_suggestion_id"],),
        ).fetchone()
        if existing and existing["raw_text"] == row["raw_text"] and existing["status"] == row.get("status", ""):
            return False
        self.connection.execute(
            """
            INSERT INTO source_suggestions (
                source_suggestion_id, submit_date, created_at, raw_text, department,
                job_group, work_location, scenario, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_suggestion_id) DO UPDATE SET
                submit_date = excluded.submit_date,
                created_at = excluded.created_at,
                raw_text = excluded.raw_text,
                department = excluded.department,
                job_group = excluded.job_group,
                work_location = excluded.work_location,
                scenario = excluded.scenario,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                row["source_suggestion_id"],
                row.get("submit_date", ""),
                row.get("created_at", ""),
                row["raw_text"],
                row.get("department", ""),
                row.get("job_group", ""),
                row.get("work_location", ""),
                row.get("scenario", ""),
                row.get("status", ""),
                now,
            ),
        )
        self.connection.commit()
        return True

    def count_table(self, table_name: str) -> int:
        allowed = {
            "source_suggestions",
            "import_batches",
            "suggestion_analysis",
            "issue_clusters",
            "cluster_members",
            "review_tasks",
            "action_items",
        }
        if table_name not in allowed:
            raise ValueError(f"unsupported table: {table_name}")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
```

- [ ] **Step 4: Run storage tests**

Run:

```powershell
python -m unittest tests.test_storage -v
```

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/storage.py tests/test_storage.py
git commit -m "feat: add durable batch storage schema"
```

## Task 4: Add Embeddings And Vector Candidate Retrieval

**Files:**
- Create: `src/embeddings.py`
- Create: `src/vector_index.py`
- Create: `tests/test_matching.py`

- [ ] **Step 1: Write vector retrieval tests**

Create the first half of `tests/test_matching.py`:

```python
import unittest

from src.embeddings import HashEmbeddingProvider, cosine_similarity
from src.vector_index import ClusterVector, InMemoryVectorIndex


class VectorRetrievalTests(unittest.TestCase):
    def test_hash_embeddings_are_deterministic(self):
        provider = HashEmbeddingProvider(dimensions=32)
        self.assertEqual(provider.embed("夜班没热饭"), provider.embed("夜班没热饭"))

    def test_cosine_similarity_orders_related_text_higher(self):
        provider = HashEmbeddingProvider(dimensions=64)
        query = provider.embed("夜班食堂没有热饭")
        related = provider.embed("下晚班食堂饭菜是冷的")
        unrelated = provider.embed("工资条看不懂")

        self.assertGreater(cosine_similarity(query, related), cosine_similarity(query, unrelated))

    def test_vector_index_filters_by_category_and_owner(self):
        provider = HashEmbeddingProvider(dimensions=64)
        index = InMemoryVectorIndex(
            [
                ClusterVector(
                    cluster_id="C001",
                    primary_category="后勤保障",
                    secondary_category="食堂饭菜",
                    owner_department="后勤部",
                    status="active",
                    embedding=provider.embed("夜班食堂没有热饭"),
                ),
                ClusterVector(
                    cluster_id="C002",
                    primary_category="薪酬福利",
                    secondary_category="薪酬说明",
                    owner_department="人力资源部",
                    status="active",
                    embedding=provider.embed("工资条不清楚"),
                ),
            ]
        )

        results = index.search(
            embedding=provider.embed("晚班饭菜太冷"),
            primary_category="后勤保障",
            secondary_category="食堂饭菜",
            owner_department="后勤部",
            top_k=5,
        )

        self.assertEqual([item.cluster_id for item in results], ["C001"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m unittest tests.test_matching -v
```

Expected: FAIL with import errors for `src.embeddings` or `src.vector_index`.

- [ ] **Step 3: Implement deterministic embeddings**

Create `src/embeddings.py`:

```python
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from src.text_processing import text_features


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class HashEmbeddingProvider:
    model_name = "local-hash-ngram-v1"

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for feature in text_features(text):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))
```

- [ ] **Step 4: Implement in-memory vector index**

Create `src/vector_index.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from src.embeddings import cosine_similarity


@dataclass
class ClusterVector:
    cluster_id: str
    primary_category: str
    secondary_category: str
    owner_department: str
    status: str
    embedding: list[float]


@dataclass
class CandidateCluster:
    cluster_id: str
    vector_score: float


class InMemoryVectorIndex:
    def __init__(self, clusters: list[ClusterVector]):
        self.clusters = clusters

    def search(
        self,
        *,
        embedding: list[float],
        primary_category: str,
        secondary_category: str,
        owner_department: str,
        top_k: int,
    ) -> list[CandidateCluster]:
        candidates: list[CandidateCluster] = []
        for cluster in self.clusters:
            if cluster.status != "active":
                continue
            if cluster.primary_category != primary_category:
                continue
            if cluster.secondary_category != secondary_category:
                continue
            if cluster.owner_department != owner_department:
                continue
            candidates.append(CandidateCluster(cluster.cluster_id, cosine_similarity(embedding, cluster.embedding)))
        return sorted(candidates, key=lambda item: item.vector_score, reverse=True)[:top_k]
```

- [ ] **Step 5: Run vector tests**

Run:

```powershell
python -m unittest tests.test_matching.VectorRetrievalTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/embeddings.py src/vector_index.py tests/test_matching.py
git commit -m "feat: add deterministic embeddings and vector retrieval"
```

## Task 5: Add Conservative Match Decisions

**Files:**
- Create: `src/matching.py`
- Modify: `tests/test_matching.py`

- [ ] **Step 1: Add matching decision tests**

Append to `tests/test_matching.py`:

```python
from src.matching import MatchEvidence, decide_cluster_match


class MatchDecisionTests(unittest.TestCase):
    def test_auto_merges_high_score_without_conflicts(self):
        evidence = MatchEvidence(
            candidate_cluster_id="C001",
            vector_score=0.91,
            keyword_score=0.85,
            same_scenario=True,
            same_owner_department=True,
            category_confidence=0.9,
            conflict_flags=[],
        )

        decision = decide_cluster_match(evidence)

        self.assertEqual(decision.decision_type, "auto_merge")
        self.assertEqual(decision.cluster_id, "C001")

    def test_sends_medium_score_to_review(self):
        evidence = MatchEvidence(
            candidate_cluster_id="C001",
            vector_score=0.78,
            keyword_score=0.6,
            same_scenario=True,
            same_owner_department=True,
            category_confidence=0.8,
            conflict_flags=[],
        )

        decision = decide_cluster_match(evidence)

        self.assertEqual(decision.decision_type, "manual_review")

    def test_conflict_forces_review_even_with_high_score(self):
        evidence = MatchEvidence(
            candidate_cluster_id="C001",
            vector_score=0.94,
            keyword_score=0.9,
            same_scenario=True,
            same_owner_department=False,
            category_confidence=0.95,
            conflict_flags=["owner_department_mismatch"],
        )

        decision = decide_cluster_match(evidence)

        self.assertEqual(decision.decision_type, "manual_review")

    def test_low_score_creates_new_cluster(self):
        evidence = MatchEvidence(
            candidate_cluster_id="C001",
            vector_score=0.4,
            keyword_score=0.2,
            same_scenario=False,
            same_owner_department=True,
            category_confidence=0.7,
            conflict_flags=[],
        )

        decision = decide_cluster_match(evidence)

        self.assertEqual(decision.decision_type, "create_new_cluster")
        self.assertIsNone(decision.cluster_id)
```

- [ ] **Step 2: Run decision tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_matching.MatchDecisionTests -v
```

Expected: FAIL with import error for `src.matching`.

- [ ] **Step 3: Implement matching**

Create `src/matching.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


AUTO_MERGE_THRESHOLD = 0.86
MANUAL_REVIEW_THRESHOLD = 0.72


@dataclass
class MatchEvidence:
    candidate_cluster_id: str
    vector_score: float
    keyword_score: float
    same_scenario: bool
    same_owner_department: bool
    category_confidence: float
    conflict_flags: list[str]


@dataclass
class MatchDecision:
    decision_type: str
    cluster_id: str | None
    final_score: float
    decision_reason: str


def final_match_score(evidence: MatchEvidence) -> float:
    scenario_bonus = 0.03 if evidence.same_scenario else 0.0
    owner_bonus = 0.03 if evidence.same_owner_department else 0.0
    score = (
        evidence.vector_score * 0.55
        + evidence.keyword_score * 0.25
        + evidence.category_confidence * 0.14
        + scenario_bonus
        + owner_bonus
    )
    return round(min(score, 1.0), 4)


def decide_cluster_match(evidence: MatchEvidence) -> MatchDecision:
    score = final_match_score(evidence)
    if evidence.conflict_flags:
        return MatchDecision(
            decision_type="manual_review",
            cluster_id=evidence.candidate_cluster_id,
            final_score=score,
            decision_reason="conflict_flags:" + ",".join(evidence.conflict_flags),
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
            decision_reason="score_in_manual_review_band",
        )
    return MatchDecision(
        decision_type="create_new_cluster",
        cluster_id=None,
        final_score=score,
        decision_reason="score_below_manual_review_threshold",
    )
```

- [ ] **Step 4: Run matching tests**

Run:

```powershell
python -m unittest tests.test_matching -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/matching.py tests/test_matching.py
git commit -m "feat: add conservative cluster match decisions"
```

## Task 6: Add Incremental CSV Batch Runner

**Files:**
- Create: `src/batch.py`
- Modify: `src/storage.py`
- Modify: `src/suggestion_pipeline.py`
- Create: `tests/test_batch.py`

- [ ] **Step 1: Write batch tests**

Create `tests/test_batch.py`:

```python
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.batch import run_csv_import_batch
from src.storage import Storage


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.storage = Storage(self.connection)
        self.storage.initialize_schema()

    def write_csv(self, path: Path):
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "suggestion_id": "S001",
                    "submit_date": "2026-06-10",
                    "raw_text": "夜班食堂没有热饭",
                    "department": "生产一部",
                    "job_group": "一线",
                    "work_location": "A厂区",
                    "scenario": "食堂",
                    "is_anonymous_for_report": "是",
                    "status": "待识别",
                    "owner_department": "",
                    "resolution_note": "",
                    "closed_date": "",
                }
            )

    def test_run_csv_import_batch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "suggestions.csv"
            self.write_csv(input_path)

            first = run_csv_import_batch(self.storage, input_path)
            second = run_csv_import_batch(self.storage, input_path)

        self.assertEqual(first.rows_created, 1)
        self.assertEqual(second.rows_created, 0)
        self.assertEqual(second.rows_skipped, 1)
        self.assertEqual(self.storage.count_table("source_suggestions"), 1)
        self.assertEqual(self.storage.count_table("suggestion_analysis"), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run batch test and verify it fails**

Run:

```powershell
python -m unittest tests.test_batch -v
```

Expected: FAIL with import error for `src.batch` or missing storage methods.

- [ ] **Step 3: Add storage methods for analysis upsert**

Modify `src/storage.py` to add:

```python
    def upsert_suggestion_analysis(self, row: dict[str, Any]) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO suggestion_analysis (
                source_suggestion_id, batch_id, normalized_text, content_hash,
                primary_category, secondary_category, owner_department,
                quality_type, urgency_level, classification_confidence,
                embedding_status, embedding_model, embedding_ref,
                review_required, analysis_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_suggestion_id) DO UPDATE SET
                batch_id = excluded.batch_id,
                normalized_text = excluded.normalized_text,
                content_hash = excluded.content_hash,
                primary_category = excluded.primary_category,
                secondary_category = excluded.secondary_category,
                owner_department = excluded.owner_department,
                quality_type = excluded.quality_type,
                urgency_level = excluded.urgency_level,
                classification_confidence = excluded.classification_confidence,
                embedding_status = excluded.embedding_status,
                embedding_model = excluded.embedding_model,
                embedding_ref = excluded.embedding_ref,
                review_required = excluded.review_required,
                analysis_status = excluded.analysis_status,
                updated_at = excluded.updated_at
            """,
            (
                row["source_suggestion_id"],
                row["batch_id"],
                row["normalized_text"],
                row["content_hash"],
                row["primary_category"],
                row["secondary_category"],
                row["owner_department"],
                row["quality_type"],
                row["urgency_level"],
                row["classification_confidence"],
                row["embedding_status"],
                row.get("embedding_model"),
                row.get("embedding_ref"),
                row["review_required"],
                row["analysis_status"],
                now,
                now,
            ),
        )
        self.connection.commit()
```

- [ ] **Step 4: Implement batch runner**

Create `src/batch.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.classification import classify_suggestion, detect_quality_type, detect_urgency
from src.domain import INPUT_FIELDS, Suggestion
from src.storage import Storage
from src.suggestion_pipeline import read_csv
from src.text_processing import content_hash, normalize_text, validate_suggestion


@dataclass
class BatchResult:
    batch_id: int
    rows_read: int
    rows_created: int
    rows_skipped: int
    rows_failed: int


def source_row_from_csv(row: dict[str, str]) -> dict[str, str]:
    return {
        "source_suggestion_id": row["suggestion_id"],
        "submit_date": row.get("submit_date", ""),
        "created_at": row.get("submit_date", ""),
        "raw_text": row.get("raw_text", ""),
        "department": row.get("department", ""),
        "job_group": row.get("job_group", ""),
        "work_location": row.get("work_location", ""),
        "scenario": row.get("scenario", ""),
        "status": row.get("status", ""),
    }


def run_csv_import_batch(storage: Storage, input_path: Path) -> BatchResult:
    rows = read_csv(input_path)
    batch_id = storage.start_import_batch("csv", "0")
    seen_hashes: set[str] = set()
    rows_created = 0
    rows_skipped = 0
    rows_failed = 0
    cursor_end = "0"

    for row in rows:
        try:
            source_row = source_row_from_csv(row)
            created = storage.upsert_source_suggestion(source_row)
            if created:
                rows_created += 1
            else:
                rows_skipped += 1

            suggestion = Suggestion({field: row.get(field, "").strip() for field in INPUT_FIELDS})
            flags = validate_suggestion(suggestion, seen_hashes)
            primary, secondary, owner, confidence = classify_suggestion(
                suggestion.raw_text,
                suggestion.fields.get("scenario", ""),
            )
            quality = detect_quality_type(suggestion.raw_text, flags)
            urgency = detect_urgency(suggestion.raw_text, primary)
            review_required = "是" if flags or confidence < 0.6 or quality in {"信息不足", "情绪表达"} else "否"

            storage.upsert_suggestion_analysis(
                {
                    "source_suggestion_id": suggestion.suggestion_id,
                    "batch_id": batch_id,
                    "normalized_text": normalize_text(suggestion.raw_text),
                    "content_hash": content_hash(suggestion.raw_text),
                    "primary_category": primary,
                    "secondary_category": secondary,
                    "owner_department": suggestion.fields.get("owner_department", "") or owner,
                    "quality_type": quality,
                    "urgency_level": urgency,
                    "classification_confidence": confidence,
                    "embedding_status": "pending",
                    "embedding_model": None,
                    "embedding_ref": None,
                    "review_required": review_required,
                    "analysis_status": "classified",
                }
            )
            cursor_end = suggestion.suggestion_id
        except Exception:
            rows_failed += 1

    storage.finish_import_batch(
        batch_id,
        cursor_end,
        rows_read=len(rows),
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        rows_failed=rows_failed,
    )
    return BatchResult(batch_id, len(rows), rows_created, rows_skipped, rows_failed)
```

- [ ] **Step 5: Add CLI commands**

Modify `src/suggestion_pipeline.py`:

```python
import sqlite3
from src.batch import run_csv_import_batch
from src.storage import Storage
```

Add parser commands:

```python
    init_db_parser = subparsers.add_parser("init-db", help="初始化本地分析数据库")
    init_db_parser.add_argument("--db", required=True, type=Path, help="SQLite 数据库路径")

    import_csv_parser = subparsers.add_parser("import-csv", help="增量导入 CSV 到本地分析数据库")
    import_csv_parser.add_argument("--input", required=True, type=Path, help="CSV 输入路径")
    import_csv_parser.add_argument("--db", required=True, type=Path, help="SQLite 数据库路径")
```

Handle commands in `main`:

```python
    if args.command == "init-db":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(args.db)
        storage = Storage(connection)
        storage.initialize_schema()
        print(f"已初始化数据库：{args.db}")
        return 0
    if args.command == "import-csv":
        connection = sqlite3.connect(args.db)
        storage = Storage(connection)
        storage.initialize_schema()
        result = run_csv_import_batch(storage, args.input)
        print(
            f"批次 {result.batch_id} 完成：读取 {result.rows_read}，"
            f"新增 {result.rows_created}，跳过 {result.rows_skipped}，失败 {result.rows_failed}"
        )
        return 0
```

- [ ] **Step 6: Run batch tests**

Run:

```powershell
python -m unittest tests.test_batch -v
```

Expected: PASS.

- [ ] **Step 7: Run CLI smoke test**

Run:

```powershell
python -m src.suggestion_pipeline init-db --db output_run_check\analysis.db
python -m src.suggestion_pipeline import-csv --input examples\sample_suggestions.csv --db output_run_check\analysis.db
```

Expected:

```text
已初始化数据库：output_run_check\analysis.db
批次 1 完成：读取 10，新增 10，跳过 0，失败 0
```

- [ ] **Step 8: Run all tests**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add src/batch.py src/storage.py src/suggestion_pipeline.py tests/test_batch.py
git commit -m "feat: add incremental csv import batch"
```

## Task 7: Update Documentation And Push

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Ensure local database artifacts are ignored**

Modify `.gitignore`:

```gitignore
*.db
*.sqlite
*.sqlite3
output_run_check/
```

- [ ] **Step 2: Update README quick start**

Add this section to `README.md`:

```markdown
## 工程化增量处理

初始化本地分析数据库：

```powershell
python -m src.suggestion_pipeline init-db --db output_run_check/analysis.db
```

导入 CSV 到分析数据库：

```powershell
python -m src.suggestion_pipeline import-csv --input examples/sample_suggestions.csv --db output_run_check/analysis.db
```

当前版本使用 SQLite 作为本地开发和测试数据库。生产环境对接小程序 MySQL 时，应通过同一套 storage/repository 接口接入，避免改动分类、向量匹配和聚类业务逻辑。
```
```

- [ ] **Step 3: Run final verification**

Run:

```powershell
python -m unittest discover -s tests
python -m src.suggestion_pipeline analyze --input examples\sample_suggestions.csv --output-dir output_run_check
python -m src.suggestion_pipeline init-db --db output_run_check\analysis.db
python -m src.suggestion_pipeline import-csv --input examples\sample_suggestions.csv --db output_run_check\analysis.db
```

Expected:

- Unit tests pass.
- Legacy CSV output command succeeds.
- Database init command succeeds.
- Incremental CSV import command reports 10 rows read.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md .gitignore
git commit -m "docs: document incremental analysis workflow"
```

- [ ] **Step 5: Push branch**

Run:

```powershell
git push
```

Expected: push succeeds to `origin/master`.

## Self-Review Notes

- Spec coverage: this milestone covers engineering foundation, incremental import, durable tables, embedding provider interface, vector candidate retrieval, conservative match decisions, and tests. Full MySQL production adapter, scheduler, review UI, and external vector store remain follow-up plans after this local core is stable.
- Placeholder scan: no task leaves unfinished implementation text. The reporting extraction step names exact existing functions to move because those bodies already exist in the repository and must remain behavior-compatible.
- Type consistency: `Suggestion`, `Cluster`, `Storage`, `HashEmbeddingProvider`, `InMemoryVectorIndex`, `MatchEvidence`, and `BatchResult` are introduced before later tasks reference them.
