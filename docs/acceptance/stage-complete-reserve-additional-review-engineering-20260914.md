# P3-STAGE-015-C Additional Blind Review Engineering Acceptance

Status: `PASS_ENGINEERING_READY_FOR_ONE_EXACT_60_BLIND_REVIEW_PREPARE`

## Preconditions

P015-B closed through the single authorized technical recovery Job
`stage_ac0fb812b46e4ae5bfe57d7b2dc82aee`. Its immutable artifact freezes exactly 60 additional
review resources with set digest
`sha256:e9960db222cdb8bdd2bdffb02ddea1351b9b7610616392d9daaa751a7d879358`.
Every P015-A row marked `new_review_required=true` is included; no positive-count stop, semantic
sorting, replacement or new identity discovery occurred.

The existing P014 review remains immutable at 192 cases and ledger SHA
`sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69`.

## Accepted C1 runtime

The admitted kind is `quanthus.stage-complete-reserve-additional-review-prepare.v1` with one Job,
zero automatic retries and a 3600-second wall budget. It reads only the exact 61-session windows in
the P015-B 60-row set and requires the reviewer-visible primary Structure facts plus frozen
range-traversal and counter-direction-excursion facts at exact window end T.

The Job may publish only:

- exact review selection metadata;
- blind primary evidence with the retained 21-close history;
- the two frozen directional path facts;
- Era / board / window strata;
- source bindings and summary metadata.

It does not compute or publish threshold basis, candidate grid, envelope scoring measurements,
candidate scores, Stage projections or preserved evaluation.

## Blind review boundary

Review roles remain `short_term` and `fund`, explicitly same-origin simulation. Reviewer pages do
not expose candidate output, threshold basis, envelope measurement, existing P014 opinions, future
rows, preserved 2021 or 2025.

The 60 new cases must be reviewed in their frozen evidence order. Existing P014 opinions are not
read during C1 prepare and are not re-reviewed.

## Immutable incremental seal

`seal-p015-review` accepts exactly the 60 new atomic opinions. The Worker verifies their parent
manifest/evidence binding and all blinding declarations, then binds the already-sealed P014 ledger
by its exact SHA. The new ledger stores only the 60 new opinions plus:

- the immutable old-ledger SHA and 192-case count;
- a deterministic combined 252-case reference summary.

It does not copy, renumber or rewrite the 192 P014 opinions. Re-sealing identical content is
idempotent; any changed content is rejected.

Sealing the new ledger does **not** authorize candidate scoring or preserved-2021 access. Those
remain blocked through the separate P015-D reference-sufficiency gate.

## Verification

Focused C1 Worker/helper/review-seal checks passed at **130 tests**. Repository-wide `make check`
then passed at **2442 passed / 7 skipped**, with the skips limited to the existing production-artifact
or PostgreSQL-dependent acceptance surfaces. Ruff and `git diff --check` are clean. This is the
runtime-engineering acceptance boundary only; no P015-C result-bearing Job has run yet.
