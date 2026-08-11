# ADR 0008: DWD Historical-Knowledge Remediation

- Status: Accepted and completed
- Date: 2026-08-06
- Completion date: 2026-08-11
- Decision id: `META-DATA-PIT-001`
- Result: DWD Base V2.1 Build202607 Release

## Problem

The original `dwd-base-202607-v2` was physically valid but did not provide sufficient evidence for
all historical point-in-time claims. Issue #78 audited all 33 DWD objects and found bounded
semantic, temporal, source-coverage, and lineage defects. Mutating V2 in place would have destroyed
the evidence needed to understand and reproduce those defects.

## Decision

Create an immutable successor rather than rewrite V2:

- retain `ods-base-202607-v2` and `dwd-base-202607-v2` unchanged;
- freeze the 31-item findings ledger and bounded V2.1 remediation scope;
- rebuild all nine domains into a new pointerless package;
- verify package/schema partitions, semantic contracts, domain invariants, coverage/exclusions,
  and lineage authority independently;
- release only a candidate for which all five audits are sealed `verified`;
- retain V2 as the immediate rollback package.

The detailed pre-rebuild reasoning, findings, dispositions, and original execution proposal remain
in `docs/acceptance/issue-78-dwd-base-v2-pre-rebuild-audit-closure-20260807/`. They are immutable
historical evidence, not current host or approval requirements.

## Result

Run `dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18` produced
`dwd-base-202607-v2.1` through 2026-07-31:

- manifest SHA-256:
  `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`;
- content digest:
  `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9`;
- 33 tables, 455,817,962 rows, and 14,447,400,032 payload bytes;
- all five independent candidate audits verified.

ADR 0009 governs the node-independent rebuild, audit, and atomic promotion mechanics. The formal
release identity is `config/releases/v2-build202607.json`.
