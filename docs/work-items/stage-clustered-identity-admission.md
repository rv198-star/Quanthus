# P3-STAGE-024 — Cross-Era Clustered-Identity Development Admission

Design status: approved  
Delivery gate: open engineering; no new readiness read until this contract is durably frozen

Machine contract: `../../config/stage-learning/stage-clustered-identity-admission.v1.json`  
Parent result: `../../config/stage-learning/stage-realized-identity-admission-result.v1.json`  
Parent review: `../reviews/stage-p023-realized-identity-admission-review-20260916.md`

## Goal

Close only the remaining P023 technical-admission deficit without rerunning any completed readiness
observation and without turning cross-era reuse into fake independent evidence. P024 starts from the
immutable P023 179-row admission-ready panel. It may read only P023 fallback candidates that were
skipped solely because their economic identity was already realized in another cell.

The target remains 24 distinct identities inside every Era × Board cell. The same economic identity
may appear in more than one cell only when the exact windows do not overlap. Such rows belong to one
explicit identity cluster and never count as separate identity-independent observations.

## Frozen semantics

- P023 remains a historical fail-close; its artifact, readings and 179-row plan are immutable.
- No P023 readiness success or failure may be rerun.
- New readiness reads are restricted to P023 attempts with reason
  `SKIPPED_REALIZED_IDENTITY_CONFLICT` and `market_read=false`.
- Supplemental reads are restricted to `STAR_PRE_CHINEXT_20PCT/STAR`, the sole P023 deficit cell.
- Candidate order is inherited from the already-frozen P023 identity rank then window rank; P024 may
  not reorder using market values, readiness results, semantic evidence or later outcomes.
- Each cell must remain identity-unique. Cross-cell identity reuse is legal only across pairwise
  non-overlapping exact windows.
- Every repeated economic identity is one cluster. The final artifact must publish total row count,
  unique-cluster count and repeated-cluster count.
- `192 rows` must never be described as `192 independent identities` when clusters repeat.
- Any later P020-C/P020-D artifact must carry both row count and unique-cluster count so reference or
  scoring reports cannot silently inflate independence.
- Preserved/final evaluation retains identity-global exclusion; P024 changes development-resource
  representation only.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-024-A | Freeze clustered-development admission and accounting contract | P3-STAGE-023 | this preregistration; no new readiness read |
| P3-STAGE-024-B | Implement artifact-bound supplemental admission runtime | P3-STAGE-024-A | deterministic code/tests only; P023 readings reusable and immutable |
| P3-STAGE-024-C | Run the sole supplemental admission Job | P3-STAGE-024-B | PASS only at 8×24 admission-ready rows with within-cell uniqueness and complete cluster accounting |
| P3-STAGE-024-D | Review/close and gate P020-C preregistration | P3-STAGE-024-C | pass may open only a separately frozen P020-C preregistration; failure returns to four-party portfolio review |

## Pass and failure semantics

PASS requires exactly 24 admission-ready rows in each of eight cells, 192 rows total, one identity per
cell, pairwise non-overlapping windows inside every repeated-identity cluster, and complete cluster
accounting. Cross-era repeated identities are development clusters, not extra independent evidence.

If the frozen conflict-skipped candidate surface cannot fill the remaining deficit, close
`CLOSED_CLUSTERED_ADMISSION_CAPACITY_SHORTFALL`. Do not automatically create P025, lower the cell
quota, merge/drop STAR, add years, change ordering or open semantics. Any successor after failure
requires a new four-party portfolio decision.

## Boundaries

P024 may read market inputs only for the previously unread, conflict-skipped candidates allowed by the
frozen contract and only to reproduce the same 61-session technical readiness check. It may not retain
raw OHLC, build Structure facts, read Stage1/12 labels or reviewer opinions, search thresholds, score
candidates, open preserved 2021, open 2022-2024/2025, emit daily/strategy output, or trade.

A P024 PASS does not start semantic review. It permits only a separately frozen P020-C preregistration
that binds the exact clustered-plan digest and its row/cluster accounting before any T-only packet is
built.
