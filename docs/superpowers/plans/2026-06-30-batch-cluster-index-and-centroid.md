# Batch Cluster Index And Centroid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep 7,000-10,000-row daily imports fast by reusing cluster indexes within a batch, while keeping cluster centroids consistent with accepted members.

**Architecture:** Add mutable upsert support to the existing exact vector index and a batch-scoped matcher that lazily loads each `(primary_category, secondary_category, owner_department)` partition once. Automatic merges update centroids online; review and reassignment paths rebuild affected centroids from accepted memberships.

**Tech Stack:** Python 3.11+, `sqlite3`, deterministic hash embeddings, `unittest`.

---

## Invariants

- Hard category and owner filters remain unchanged.
- Each encountered partition is loaded from SQLite at most once per batch.
- A cluster created during a batch is searchable by later rows in that batch.
- Pending and rejected memberships never alter centroids.
- A newly accepted membership changes its centroid exactly once.
- Review, reassignment, rejection, and changed-source removal rebuild affected centroids.
- Existing CLI commands and persisted schemas remain compatible.

### Task 1: Mutable Vector Index

**Files:** `src/vector_index.py`, `tests/test_matching.py`

- [ ] Write `test_in_memory_vector_index_upsert_adds_and_replaces_by_cluster_id`: start empty, upsert `C001`, replace it, then assert search returns one result with replacement content.
- [ ] Run the focused test; expect `AttributeError` for missing `upsert`.
- [ ] Implement `_positions: dict[str, int]`; append unknown IDs and replace known positions.

```python
def upsert(self, cluster: ClusterVector) -> None:
    position = self._positions.get(cluster.cluster_id)
    if position is None:
        self._positions[cluster.cluster_id] = len(self._clusters)
        self._clusters.append(cluster)
        return
    self._clusters[position] = cluster
```

- [ ] Run `python -m unittest tests.test_matching -v`.
- [ ] Commit `feat: support mutable cluster vector index`.

### Task 2: Batch-Scoped Partition Cache

**Files:** create `src/cluster_matching.py`; modify `src/batch.py`; test `tests/test_batch.py`.

- [ ] Add a failing `CountingStorage` test: import three same-partition rows, assert three memberships and one call to `list_active_cluster_vectors`.
- [ ] Run the focused test and verify it fails because the current code loads once per row.
- [ ] Implement `ClusterMatchSession`, move decision persistence behind `persist_decision`, and lazily cache indexes by the hard-filter tuple.
- [ ] Upsert every newly created cluster into the current partition. Keep `persist_cluster_decision(storage, ...)` as a one-shot compatibility wrapper.
- [ ] Construct one `ClusterMatchSession(storage)` before the row loop in `run_rows_import_batch`.
- [ ] Run `python -m unittest tests.test_batch -v`.
- [ ] Commit `perf: reuse cluster indexes within import batch`.

### Task 3: Accepted-Member Centroid Maintenance

**Files:** `src/storage.py`, `src/cluster_matching.py`, `tests/test_batch.py`, `tests/test_storage.py`.

- [ ] Add a failing auto-merge test: start with centroid `[1.0, 0.0]`, accept `[0.0, 1.0]`, and require persisted and cached centroids `[0.5, 0.5]` with count `2`.
- [ ] Run the focused test and verify the centroid assertion fails.
- [ ] Make `Storage.add_cluster_member()` return `True` only when a previously absent accepted membership increments an existing cluster.
- [ ] Add `blend_issue_cluster_centroid(cluster_id, member_embedding)`; read the incremented count, average by membership counts, and skip malformed or dimension-mismatched vectors.
- [ ] Blend only when membership insertion returns `True`, then refresh the cached vector.
- [ ] Add failing review approval, reassignment, and changed-source removal tests with explicit two-dimensional embeddings.
- [ ] Replace `_recalculate_cluster_suggestion_count` with `_recalculate_cluster_aggregate`: average valid equal-dimension embeddings joined through accepted members and update count, centroid, and timestamps.
- [ ] Run `python -m unittest tests.test_storage tests.test_batch tests.test_matching -v`.
- [ ] Commit `feat: maintain issue cluster centroids`.

### Task 4: Repeatable 10,000-Row Benchmark

**Files:** create `scripts/benchmark_incremental.py`, create `tests/test_incremental_benchmark.py`, modify `README.md`.

- [ ] Add a failing smoke test for `run_benchmark(rows=20, seed=7, db_path=...)`; require `rows`, `elapsed_seconds`, `rows_per_second`, `clusters`, `partitions`, and `partition_loads <= partitions`.
- [ ] Run the smoke test and verify module import fails.
- [ ] Generate deterministic rows with `random.Random(seed)`, time `run_rows_import_batch`, print JSON, and support `--rows` (default 10000), `--seed`, `--db`, and optional `--min-throughput`.
- [ ] Run the smoke test and full unit suite.
- [ ] Run `python scripts/benchmark_incremental.py --rows 10000 --seed 7 --db output_run_check/benchmark-10000.db`; require zero failed rows, positive finite throughput, and bounded partition loads.
- [ ] Document the command and clarify that external embedding/vector services need a production benchmark.
- [ ] Run the full suite and `git diff --check`.
- [ ] Commit `perf: add incremental import benchmark`, push `master`, and verify local/remote SHAs.

## Completion Evidence

- Full unit suite passes.
- The 10,000-row benchmark finishes with zero failed rows.
- Query-count instrumentation proves one storage load per partition per batch.
- Tests prove automatic merge, review/reassignment, and changed-source removal keep centroids consistent.
- GitHub `master` and local `HEAD` resolve to the same SHA.
