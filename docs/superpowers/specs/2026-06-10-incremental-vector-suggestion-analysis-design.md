# Incremental Vector Suggestion Analysis Design

## Background

The current project is a local CSV-based employee suggestion analysis script. It can classify, cluster, create action items, and generate a weekly report for small batches, but it does full in-memory processing and full clustering on each run.

The target system connects to a mini-program MySQL database. The mini-program is responsible for suggestion submission. This project should become an engineering-grade analysis service that imports new suggestions every day, classifies them accurately, clusters similar issues conservatively, and remains stable as data grows by 7,000 to 10,000 suggestions per day.

## Goals

- Import daily incremental suggestions from MySQL.
- Avoid full historical reprocessing during normal daily runs.
- Use vector similarity to find related historical issue clusters quickly.
- Keep clustering conservative enough to avoid incorrect remediation grouping.
- Persist analysis results, cluster membership, review decisions, action items, and import history.
- Support manual review for uncertain cases.
- Keep the system maintainable with clear modules, tests, logs, and operational visibility.

## Non-Goals

- The first version will not replace the mini-program submission flow.
- The first version will not rely on a large language model to make all final clustering decisions.
- The first version will not attempt fully automatic, no-human-review clustering for all cases.
- The first version will not rebuild a full BI platform; it will produce durable analysis tables and exportable reports.

## Recommended Architecture

Use a batch-oriented analysis service:

1. Read new raw suggestions from the mini-program MySQL database.
2. Normalize and validate each suggestion.
3. Classify each suggestion into category, subcategory, owner department, urgency, and quality type.
4. Generate and persist a text embedding for each suggestion.
5. Search only compatible historical issue clusters using vector similarity.
6. Apply conservative merge rules and thresholds.
7. Create review tasks for uncertain matches.
8. Update issue clusters and action items.
9. Write batch metrics and report outputs.

The main rule is: vectors retrieve candidates; they do not alone decide the final merge.

## Components

### Importer

The importer reads suggestions from MySQL using an incremental cursor. The cursor can be based on `created_at`, an auto-incrementing source ID, or both. Each run creates an import batch record.

Responsibilities:

- Read only new or changed source records.
- Deduplicate by source suggestion ID.
- Store batch status, row counts, started time, finished time, and errors.
- Allow safe retry without duplicating analysis rows.

### Normalizer

The normalizer prepares text for classification, embeddings, and duplicate detection.

Responsibilities:

- Preserve original raw text unchanged.
- Produce normalized text for analysis.
- Detect empty, too-short, duplicate, and sensitive-information cases.
- Produce a stable content hash for duplicate checks.

### Classifier

The classifier assigns structured labels before clustering. This step is a hard safety boundary for clustering.

Responsibilities:

- Assign primary category.
- Assign secondary category.
- Assign owner department.
- Assign urgency level.
- Assign quality type.
- Assign confidence and review flags.

The first implementation can combine the existing rule system with configurable dictionaries. Later versions can add model-assisted classification, but the database schema should not depend on one specific model provider.

### Embedding Service

The embedding service generates vectors for normalized suggestion text and cluster representative text.

Responsibilities:

- Generate embeddings for new suggestions.
- Cache embeddings by content hash.
- Record model name and embedding dimension.
- Mark failed embedding jobs as retryable.

The design should support either a local embedding model or an external embedding API. The application should hide provider details behind an interface.

### Vector Index

The vector index stores and searches embeddings. Recommended first implementation options:

- MySQL-compatible vector extension if the deployed MySQL environment supports it.
- A local or service vector store such as FAISS, Milvus, Qdrant, or Elasticsearch vector search.
- A fallback exact search over active cluster vectors for early low-volume deployment.

The index must support filtering by category, owner department, and active cluster status before or during nearest-neighbor search.

### Cluster Matcher

The matcher decides whether a new suggestion joins an existing issue cluster or starts a new one.

Responsibilities:

- Search only compatible candidate clusters.
- Score candidates using vector similarity, keyword overlap, same scenario, same owner department, time recency, and historical review feedback.
- Apply merge thresholds.
- Produce a decision: auto-merge, manual-review, or create-new-cluster.
- Save all decision evidence for audit.

### Review Workflow

Manual review handles uncertain matches and improves future behavior.

Responsibilities:

- Show the new suggestion, proposed cluster, representative examples, scores, and conflict flags.
- Allow approve merge, reject merge, create new cluster, merge clusters, or split membership.
- Save reviewer decision and reason.
- Feed approved and rejected pairs into future threshold tuning.

### Action Item Generator

Action items are generated from issue clusters, not directly from isolated suggestions.

Responsibilities:

- Create or update remediation action items for active clusters.
- Keep owner department, urgency, suggestion count, related suggestion IDs, and status.
- Avoid recreating duplicate action items for the same active cluster.

### Reporting

Reports are generated from persisted tables, not from transient CSV-only analysis.

Responsibilities:

- Daily import report.
- Weekly management report.
- Category distribution.
- High-frequency issue clusters.
- High-urgency issues.
- Review backlog and auto-merge rate.

## Data Model

### source_suggestions

This may be the existing mini-program table or a synced local copy.

Key fields:

- `source_suggestion_id`
- `submit_date`
- `created_at`
- `raw_text`
- `department`
- `job_group`
- `work_location`
- `scenario`
- `status`

### import_batches

Tracks each import run.

Key fields:

- `batch_id`
- `source_name`
- `cursor_start`
- `cursor_end`
- `started_at`
- `finished_at`
- `status`
- `rows_read`
- `rows_created`
- `rows_skipped`
- `rows_failed`
- `error_summary`

### suggestion_analysis

Stores one analysis row per source suggestion.

Key fields:

- `analysis_id`
- `source_suggestion_id`
- `batch_id`
- `normalized_text`
- `content_hash`
- `primary_category`
- `secondary_category`
- `owner_department`
- `quality_type`
- `urgency_level`
- `classification_confidence`
- `embedding_status`
- `embedding_model`
- `embedding_ref`
- `review_required`
- `analysis_status`
- `created_at`
- `updated_at`

### issue_clusters

Stores durable problem clusters.

Key fields:

- `cluster_id`
- `cluster_name`
- `cluster_summary`
- `primary_category`
- `secondary_category`
- `owner_department`
- `scenario_key`
- `status`
- `suggestion_count`
- `representative_suggestion_id`
- `centroid_embedding_ref`
- `last_seen_at`
- `created_at`
- `updated_at`

### cluster_members

Stores suggestion-to-cluster membership.

Key fields:

- `cluster_member_id`
- `cluster_id`
- `source_suggestion_id`
- `decision_type`
- `vector_score`
- `keyword_score`
- `final_score`
- `decision_status`
- `decision_reason`
- `reviewed_by`
- `reviewed_at`
- `created_at`

### review_tasks

Stores manual review queue items.

Key fields:

- `review_task_id`
- `source_suggestion_id`
- `candidate_cluster_id`
- `task_type`
- `priority`
- `evidence_json`
- `status`
- `review_result`
- `reviewed_by`
- `reviewed_at`
- `created_at`

### action_items

Stores remediation tasks.

Key fields:

- `action_id`
- `cluster_id`
- `action_title`
- `owner_department`
- `urgency_level`
- `status`
- `suggestion_count`
- `first_seen_at`
- `last_seen_at`
- `next_step`
- `created_at`
- `updated_at`

## Incremental Processing Flow

1. Scheduler starts a daily batch.
2. Importer reads suggestions after the last successful cursor.
3. New records are inserted or updated idempotently.
4. Normalizer preserves raw text and computes normalized text.
5. Classifier labels each suggestion.
6. Embedding service generates vectors for suggestions that need embeddings.
7. Matcher filters candidate clusters by hard constraints:
   - same or compatible primary category
   - same secondary category when confidence is high
   - same owner department unless cross-department rules allow otherwise
   - active cluster status
8. Vector index returns top K candidate clusters.
9. Matcher computes final score and conflict flags.
10. Decision is persisted:
    - high confidence: auto-merge
    - medium confidence: create review task
    - low confidence: create new cluster
11. Cluster summary and centroid are updated for accepted members.
12. Action item is created or refreshed.
13. Batch metrics and reports are written.

## Clustering Accuracy Strategy

### Hard Constraints

Hard constraints prevent obviously wrong merges before vector search.

Examples:

- Canteen food issues cannot merge with dormitory hygiene issues.
- Equipment repair issues cannot merge with salary explanation issues.
- Safety hazards should not merge into general environment complaints unless explicitly configured.

### Candidate Retrieval

Vector search retrieves only top K compatible clusters. A recommended starting value is K = 10 per suggestion after filters.

### Final Score

The final merge score should combine:

- vector similarity
- keyword and phrase overlap
- same scenario or location
- same owner department
- category confidence
- previous reviewer-approved similar pairs
- negative signals from reviewer-rejected similar pairs

### Thresholds

Recommended starting thresholds:

- auto-merge: final score >= 0.86 and no conflict flags
- manual review: 0.72 <= final score < 0.86
- new cluster: final score < 0.72

These values are starting points and should be tuned from review outcomes.

### Conflict Flags

Conflict flags force manual review even when vector similarity is high.

Examples:

- different secondary categories
- different owner departments without an allowed cross-owner rule
- one record is safety-related and the other is not
- one record is low-quality or too short
- sensitive information detected

## Performance Strategy

Daily processing should be incremental. The normal path must not compare new suggestions with every historical suggestion.

Performance controls:

- Process only new or changed suggestions.
- Search against active cluster centroids instead of every historical suggestion.
- Partition candidate search by category and owner department.
- Use vector index top K retrieval.
- Batch embedding calls.
- Cache embeddings by content hash.
- Persist intermediate status so failed batches can resume.
- Keep old closed clusters searchable only when a new issue is close enough or within configured time windows.

Expected daily volume of 7,000 to 10,000 suggestions is acceptable if each suggestion searches a small filtered candidate set rather than the full history.

## Reliability And Operations

The batch runner should be idempotent and observable.

Required behavior:

- A failed run can be retried safely.
- Import cursor advances only after successful persistence.
- Failed rows are recorded with reasons.
- Embedding failures are retryable.
- Review tasks are not duplicated on retry.
- Logs include batch ID, source cursor, row counts, duration, and error summaries.
- Metrics include import count, classification count, embedding count, auto-merge count, review count, new cluster count, failed count, and processing duration.

## Testing Strategy

Unit tests:

- normalization
- validation flags
- classification rules
- duplicate detection
- threshold decisions
- conflict flag decisions
- action item generation

Integration tests:

- MySQL incremental import with a test database
- idempotent retry behavior
- vector candidate retrieval with filters
- end-to-end daily batch

Performance tests:

- 10,000 daily records
- 100,000 accumulated records
- 1,000,000 accumulated records represented by active clusters
- high-duplicate and high-new-topic scenarios

Quality tests:

- known similar suggestions merge correctly
- known different suggestions do not merge
- medium-confidence cases enter review
- reviewer decisions affect later decisions

## Migration Plan

Phase 1: Engineering foundation

- Split the current script into modules.
- Add configuration management.
- Add MySQL connection support.
- Add durable analysis tables.
- Preserve current CSV report generation as an output option.

Phase 2: Incremental import

- Implement import batches and cursor tracking.
- Make daily processing idempotent.
- Add basic operational logs and metrics.

Phase 3: Vector matching

- Add embedding provider interface.
- Add vector storage or vector index.
- Implement candidate retrieval and conservative matching.
- Persist cluster membership evidence.

Phase 4: Review workflow

- Add review task table and CLI or lightweight admin interface.
- Support approve, reject, create new cluster, and manual reassignment.
- Use reviewer outcomes to tune thresholds.

Phase 5: Reporting and optimization

- Generate reports from persisted tables.
- Add performance tests.
- Tune vector index, thresholds, and active cluster policies.

## Open Decisions

The design intentionally leaves these deployment choices configurable:

- Embedding provider: local model or external API.
- Vector store: MySQL vector support, FAISS, Milvus, Qdrant, Elasticsearch, or another service.
- Scheduler: cron, Windows Task Scheduler, Airflow, or another existing operations tool.
- Review UI: CLI first, lightweight web admin, or integration with an existing backend.

The application interfaces should hide these choices so the system can start simple and evolve without changing the core business logic.
