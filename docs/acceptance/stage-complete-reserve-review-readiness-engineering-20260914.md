# P3-STAGE-015-A — Complete-Reserve Review-Readiness Engineering Acceptance

Date: 2026-09-14

Disposition: `PASS_ENGINEERING_READY_FOR_ONE_REVIEW_READINESS_CENSUS_JOB`

## Scope

This acceptance freezes the one-shot P015-A engineering path before the readiness census is run. It does not add a review opinion, inspect candidate/threshold values, or authorize candidate scoring.

## Frozen resource ceiling

The only resources are the exact P014-B admission-qualified plan:

- 256 globally identity-unique rows;
- eight Era × Board cells;
- exactly 32 resources in each cell;
- plan manifest `sha256:f14c065d0bf18b6491b9220132d1be3c10e608fb9a69f4141c576e351aa7a173`;
- plan digest `sha256:206ed58e8349de59efd52caad958f7bd0f2f9d79f1ee0199eea6a079b6c14829`;
- identity digest `sha256:a3aa679c1792e7545847159c9b4d26dec70b4a925e55bbeb9416852b11f8de05`.

No new stock, identity, time window, random pool, cross-cell fill or post-result replacement is permitted.

## Readiness computation

For each exact 61-session resource window, T is its frozen window end. The census may transiently reproduce the accepted primary Structure facts and directional path facts solely to answer whether the same blind review packet used by P014 can be constructed.

A row is `READY` only when:

1. the frozen primary Trend/Order fact intersection exists at T;
2. range traversal is available with a value at T;
3. counter-direction excursion has availability reason `available` or `zero_net_direction` at T;
4. the exact 61-session source/member closure is intact.

The output may persist only resource identity, Era/Board/cell, ready boolean, bounded reason code, source closure digest, whether the resource already belongs to the immutable P014 192-case ledger, and whether a new review would be required.

It may not persist OHLC, volume, Structure values, morphology values, threshold values, candidate outputs, envelope values, opinions or future outcomes.

## P014 opinion preservation

The existing P014 blind ledger is immutable:

`sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69`.

All 192 previously reviewed resource rows must still be `READY`; otherwise P015-A fails closed. Those opinions are not read or rewritten by P015-A.

## Continuation semantics

For P015-A to continue:

- total ready resources must be at least 192;
- every Era × Board cell must have at least 24 ready resources;
- all 192 P014-reviewed resources must remain ready.

These are lower-bound integrity checks only. If the census passes, P015-B must freeze **every** ready row from the complete 256-row resource ceiling. It may not stop after obtaining enough rows to satisfy the observed orderly-consolidation deficit.

## Worker and operator surface

The one-shot Worker kind is:

`quanthus.stage-complete-reserve-review-readiness.v1`

It has one exact-revision Job budget, zero automatic retries and a 3600-second wall limit. The stable AgentDock launcher delegates the restricted `submit-p015-a` helper command from the current clean Git checkout; no root helper refresh is required.

Bounded failure-tail diagnostics are included through the existing Worker doctor mechanism.

## Verification

Focused pre-run verification:

- P015-A config and availability-only readiness semantics;
- Worker one-shot budget/idempotence;
- doctor job-kind/failure diagnostic surface;
- restricted AgentDock helper command;
- P014/P015 governance and traceability contracts;
- Ruff and `git diff --check`.

Focused result: `118 passed`.

Repository-wide `make check` passed before commit/push/deploy with `2425 passed, 7 skipped`; the skips remain the existing production-artifact/PostgreSQL integration conditions and grant no Stage or market qualification.

## Authority ceiling

This acceptance authorizes only a single complete-reserve review-readiness census after full-repository verification and durable commit/push/deploy. It does not authorize new review opinions, candidate/threshold/selection transport access, candidate scoring, preserved-2021 access, 2025 access, daily/strategy use or trading.
