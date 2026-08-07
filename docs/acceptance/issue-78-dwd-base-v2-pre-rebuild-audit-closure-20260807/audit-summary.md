# DWD Base V2 Pre-Rebuild Audit Closure (Working)

- Package: `dwd-base-202607-v2`
- Manifest: `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f`
- Content: `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9`
- Findings: `31`; severities: `{'high': 30, 'medium': 1}`
- Builder gate: **blocked**; open audit scope: `1`

This packet is read-only working evidence. It does not authorize a V2.1 builder, candidate assembly, publication, or pointer movement.

## Evidence

- Schema report: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-b-20260806/cn01-dwd-v2-schema-partitions.json` (sha256:3a2f50c6637973df3f327cb0e7d950a2519235aef4ac6590fc4015a5a0ba42b7)
- Semantic report: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-c7-20260807/semantic-contract-audit.json` (sha256:8c90497ffe0623465873f938ab3c417d604693b7ac3d8d9345a6f312542d7e68)
- Lineage report: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-d-20260806/lineage-authority-audit.json` (sha256:7e5f8f8c90cde41313aea5e52845ffff0aa79e43c15ef321f8965b469a09c4c9)
- Invariant report: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-d-invariants-c7-rerun2-20260807/domain_invariants/numeric-domain-invariant-audit.json` (sha256:b47334629ba780d57ec345bc54506e39d9f7035bc3d5160903a68bd63c845cd9)
- Phase A findings: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-a-20260806/phase-a-utc-rerun-closure.json` (sha256:d69db74fdd14ff0ce071735b4e3b4f449099f6400610e4354d8410769bfd67e4)
- Daily compatibility: `verified` over `7` packages
- Replay determinism evidence: `finding`
- Rebuild/publication/rollback readiness: `finding`
- Downstream/increment compatibility: `verified`
- Semantic dimension projections: `finding`
- Semantic dimension index: `/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-c8-semantic-dimensions-20260807/semantic_dimensions/semantic-dimension-audit.json` (sha256:64bb0baa700dc955783ea664b6e99e90bcb8f18be93b504545d80ab2bb9c117b)

## Immediate blockers

- `DWDV2-SCHEMA-dwd_observation_gap`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_quality_exception`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_share_capital_observation`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_shareholder_count_observation`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_shareholding_change`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_shareholding_observation`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SCHEMA-dwd_tradability_state`: Physical partitions do not match the frozen DWD model or partition contract.
- `DWDV2-SEMANTIC-dwd_adjustment_factor`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_corporate_action`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_disclosure`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_disclosure_subject`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_financial_fact`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_financial_report`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_security_bar`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_share_capital_observation`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_shareholder_count_observation`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_shareholding_change`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-SEMANTIC-dwd_shareholding_observation`: The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.
- `DWDV2-INVARIANT-dwd_financial_fact`: The encoded value contract permits semantically incompatible representations.
- `DWDV2-COVERAGE-dwd_corporate_action`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-COVERAGE-dwd_corporate_action_term`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-COVERAGE-dwd_disclosure`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-COVERAGE-dwd_disclosure_subject`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-COVERAGE-dwd_financial_concept`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-COVERAGE-dwd_share_capital_observation`: A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.
- `DWDV2-REPLAY-DETERMINISM`: The frozen Base has no two digest-bound, independently named representative derivation executions that prove deterministic replay.
- `DWDV2-READINESS-LOCAL-CAPACITY`: CN01 local storage cannot satisfy the manifest-sized successor installation gate plus its safety margin.
- `DWDV2-READINESS-SINGLE-WRITER`: The CN01-local coordination root or authoritative single-writer lock is not ready, or is placed on the wrong filesystem.
- `DWDV2-READINESS-PARTITION-PILOT`: No verified partition pilot establishes the per-builder memory limit and NFS spill bound before a full rebuild.
- `DWDV2-READINESS-CAPACITY`: The sealed build plan requires more free storage than the measured CN01 workspace provides.

## Remaining audit scope

- `human_approval_of_findings_and_remediation_scope`
