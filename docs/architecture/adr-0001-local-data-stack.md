# ADR 0001: Local-First Analytical Data Stack

- Status: Accepted
- Date: 2026-07-23
- Scope: P1 and P2
- Amended by: ADR 0002 for multi-node deployment and PostgreSQL serving topology

## Context

Quanthus must replay large daily A-share datasets on one OCI environment while retaining
immutable data revisions, SQL access, typed transformations, and portable evidence. A shared
transactional platform would add operations and failure modes before collaboration requires it.

## Decision

- Python 3.10+ is the implementation language.
- Raw and canonical bulk data use immutable, partitioned Parquet.
- DuckDB is the local query and snapshot materialization engine.
- Polars/PyArrow are the transformation and schema boundary.
- JSON stores frozen policy, release gates, and small manifests during bootstrap.
- Production transformations live under `src/quanthus`; notebooks only call those modules.

## Consequences

P1/P2 run without a network database and artifacts remain portable. Atomic publication and
concurrent-writer behavior must be implemented explicitly. A future shared catalog may replace
local metadata without changing canonical schemas or snapshot ids.
