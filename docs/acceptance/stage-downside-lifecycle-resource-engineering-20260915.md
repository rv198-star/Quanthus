# P3-STAGE-020-B Downside Lifecycle Resource Engineering Acceptance

Design status: accepted engineering freeze  
Delivery gate: ready for one resource/admission Job

## Scope

This acceptance freezes only the resource/admission layer for the `1/12` downside-lifecycle route.
It does not collect Stage-12/Stage-1 review opinions, build Trend Evolution semantics, search thresholds,
score candidates, compress to one primary stage, or open preserved partitions.

## Frozen resource contract

- reuse the accepted P013 exposure-aware unit: economic identity × non-overlapping 61-session window ×
  PIT board × role;
- preserve the eight Era × Board stress cells;
- keep the pre-result quota at 32 reserve / 24 primary / 8 fallback per cell;
- require 256 globally unique identities in the exact semantic plan;
- allow the same economic identity in a later development batch only through a disjoint exact window;
- exclude all 256 P013 exact-plan windows;
- exclude every exact identity-window actually read by the P014 admission screen, not merely its final
  256-row qualified plan;
- retain the preserved-2021 identity-global exclusion and keep 2022-2024 / 2025 closed.

## Admission semantics

The sole permitted market read is deterministic input-readiness reconstruction for exactly 61 venue
sessions. No raw OHLC is published or retained in the artifact. The Job may persist only bounded
readiness reasons and source digests. Structure facts, Stage semantics, review opinions, thresholds,
candidate output and candidate scoring remain forbidden.

A capacity shortfall is a valid fail-closed experimental disposition, not a process error. In that
case the Job must still publish a governed artifact and stop before any blind semantic review. Only
code/binding/runtime failures are process failures.

## Runtime governance

- Job kind: `quanthus.stage-downside-lifecycle-resource-plan.v1`
- maximum Jobs: 1
- automatic retries: 0
- same-revision submission is idempotent
- different revision is rejected without a separately frozen recovery authority
- stable RX6800 launcher exposes only `submit-p020-b` for this new kind; no generic shell/cancel path
  is added.

## Verification

Focused verification before this acceptance:

```text
153 passed
Ruff PASS
git diff --check PASS
```

The focused tests cover P019/P020 route traceability, P020-A semantics, P020-B consumption/exposure
boundaries, admission-shortfall artifact behavior, Worker one-shot/idempotence and the bounded
AgentDock helper.

A repository-wide `make check` remains required before commit/deploy.

## Claim ceiling

Passing this engineering acceptance means only that P020-B is ready for one governed resource and
input-admission planning run. It does not imply Stage-12 or Stage-1 semantic validity, reference
sufficiency, candidate quality, preserved validity, economic value or trading authority.
