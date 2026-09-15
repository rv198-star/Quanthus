# P3-STAGE-020-C Review-Packet Engineering Acceptance — 2026-09-16

Status: accepted engineering checkpoint  
Result-bearing packet Job: **not yet executed at this checkpoint**

## Scope

This acceptance covers only the engineering path that will prepare candidate-blind, future-blind,
T-only review packets for the exact immutable P024 capacity-limited development panel. It does not
accept any Stage12/Stage1 semantic reference, candidate rule, threshold, preserved evaluation,
strategy output or trading behavior.

Governing preregistration:

- `config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json`
- work item `P3-STAGE-020-C`
- frozen resource ceiling: 187 rows / 179 economic-identity clusters;
- capacity-limited early STAR stratum: 19 rows;
- no replacement or resource/admission reopening.

## Accepted implementation

The implementation adds `src/quanthus/stage_learning/downside_lifecycle_reference_prepare.py`.

It:

1. deep-verifies and digest-binds the exact immutable P024 artifact and clustered plan;
2. attempts every one of the 187 frozen rows and no additional identity/window;
3. rereads only each exact T-only development window required to reconstruct the already accepted
   Trend Evolution Fact Family;
4. emits exactly the accepted continuous Trend facts at T and no Stage classification;
5. separates the reviewer surface from the auditable identity/cluster map;
6. publishes no raw OHLC and retains no future rows;
7. emits zero reviewer opinions and cannot perform candidate or threshold scoring;
8. retains packet failure as `UNAVAILABLE` without replacement.

The reviewer-facing `review-packets.jsonl` is deep-verified to exclude security/listing identity,
identity-cluster id, exact window id/start, candidate, threshold and future fields. An internal
`case-map.json` preserves the opaque-case-to-identity/cluster/window binding for audit and later
cluster-aware sealing; it is not reviewer input.

## Worker / AgentDock control

A single admitted Worker kind is added:

`quanthus.stage-downside-lifecycle-reference-prepare.v1`

The Worker budget is one-shot and idempotent at an exact revision. A different revision is rejected as
having no retry/replacement authority. The restricted RX6800 AgentDock control surface adds only:

`submit-p020-c-packets <exact-revision>`

The existing deploy and runtime-revision checks remain mandatory. No generic command or file surface is
opened.

## Verification

Focused tests:

```text
145 passed
```

Repository gate:

```text
2522 passed
7 skipped
0 failed
```

The seven skips are the existing production-artifact/PostgreSQL acceptance skips and are unrelated to
P020-C packet preparation.

Synthetic unit tests verify an available 61-session packet contains exactly eleven Trend measurements
across the seven accepted concepts and no identity/future/candidate fields. A deliberately short input
remains `UNAVAILABLE` instead of triggering replacement.

## Authority after acceptance

Authorized next action only:

- deploy the exact accepted engineering revision to the governed RX6800 Stage Worker;
- execute the sole P020-C packet-preparation Job;
- deep-verify and freeze that immutable packet artifact.

Still blocked until that artifact exists:

- any Stage12/Stage1 reviewer opinion;
- review-ledger sealing;
- reference-sufficiency evaluation;
- P020-D candidate topology/scoring;
- preserved 2021, 2022-2024, 2025;
- daily/strategy/trading use.

## Claim ceiling

This checkpoint proves only that the preregistered packet-preparation mechanism and its bounded Worker
control surface conform to the frozen P020-C contract under the repository test suite. It says nothing
about whether the 187-row panel contains enough Stage12 or Stage1 semantic references; no such opinion
has been collected yet.
