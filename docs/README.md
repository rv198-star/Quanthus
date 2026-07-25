# Documentation Map

## Product and design baseline

- `product/prd.md`: product background, goals, requirements, releases, and success criteria.
- `design/hld.md`: approved system-level architecture and cross-module contracts.
- `design/lld/`: implementation-level design for Chapters 01 through 10.
- `work-items/index.md`: stable work-item ids used by GitHub issues.

## Architecture decisions

- `architecture/adr-0001-local-data-stack.md`: file-first analytical storage and execution.
- `architecture/adr-0002-hermes-oci-data-platform.md`: Hermes/OCI ownership, PostgreSQL
  publication and replication topology, security boundaries, and disaster recovery.
- `architecture/adr-0003-t-close-plus-5h-pipelines.md`: recurring acquisition lanes, the
  post-close processing SLA, immutable daily DWD partitions, and fail-closed publication.
- `architecture/adr-0004-hermes-ods-scheduler-v1.md`: five-lane Hermes acquisition schedule,
  correction semantics, publication state machine, and controlled legacy cutover.
- `operations/t-close-plus-5h-runbook.md`: Hermes handoff, OCI deployment, rehearsal, incident,
  and migration procedures for the daily pipeline.
- `operations/hermes-ods-scheduler-runbook.md`: Hermes shadow deployment, cutover gates, daily
  operations, rollback, and scheduler migration procedures.

## Decision precedence

When documents disagree, use this order:

1. `config/p0-policy.v1.json` and its contract tests.
2. Approved repository PRD/HLD/LLD documents.
3. The Notion P0 decision page dated 2026-07-23.
4. Earlier Notion HLD/LLD text.

This order matters because the original Notion Chapter 01 still mentions forward-adjusted
prices and a current-sector-membership fallback. Both were superseded by P0 decisions.

## Change discipline

Every behavior-changing pull request must identify:

- the requirement or work-item id;
- the policy, feature, label, or rule version affected;
- the acceptance evidence added or updated;
- whether arbitration or blind data was accessed.
