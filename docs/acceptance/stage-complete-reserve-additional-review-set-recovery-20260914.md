# P3-STAGE-015-B-R1 Technical Recovery Acceptance

Status: `PASS_ENGINEERING_READY_FOR_ONE_TECHNICAL_BINDING_RECOVERY`

## Failure attribution

The sole original P015-B Job `stage_66051c46d8d94852888182e67bef3b7a` at revision
`6c94ab786b2d3f92f8b5cbd9e68ecb1f066211bb` terminated before publishing an artifact.
The bounded Worker traceback is `CompleteReserveAdditionalReviewSetError:p014_review_ledger_binding`.

The defect is implementation-only. The P015-B code attempted to verify the frozen P014 review SHA
at `closeout['immutable_review']['ledger_sha256']`; the accepted P014 closeout stores that exact
binding at `closeout['review_ledger']['ledger_sha256']`.

The exception was raised before the P015-A readiness artifact or P014-B qualified-plan artifact was
opened. Therefore the failed P015-B Job did not read new runtime resource data, did not create blind
review packets, and did not consume semantic, reference, threshold, candidate, selection, preserved
2021, or 2025 surfaces.

## Frozen recovery contract

`config/stage-learning/stage-complete-reserve-additional-review-set-recovery.v1.json` authorizes
exactly one recovery Job and binds:

- the exact failed Job, revision and input digest;
- the original unchanged P015-B config SHA
  `sha256:1bd50e11f8038b2a55a4424e6480ddd29a008463b60fd1fd1b11692e4031f9e9`;
- the accepted P014 closeout and immutable review-ledger SHA
  `sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69`;
- the accepted P015-A result.

The only authorized code repair changes the closeout field path from `immutable_review` to
`review_ledger`.

## Preserved invariants

The recovery may not change:

- the exact 256-row P014-B resource ceiling;
- the P015-A readiness artifact or 252-ready / 60-new denominator;
- the exact P015-B inclusion predicate;
- the requirement to include all 60 matching rows;
- the existing P014 192-case review ledger;
- any P012 reference or numeric gate;
- candidate, threshold, selection-transport, preserved-2021, or 2025 access.

No resource replacement, positive-count stop, semantic sorting, new identity discovery, daily,
strategy, or trading authority is opened.

## Worker enforcement

The Worker admits a different revision for this Job kind only when the original one-shot budget
points to the exact failed parent, the failed status has no result artifact, all recovery
authorization bindings match repository bytes, and no recovery budget has already been used.
The same recovery revision is idempotent; a third revision is rejected.

## Verification

Focused recovery state-machine, P015-B, helper and traceability verification passes at 125 tests.
Repository-wide `make check` passes at 2434 passed / 7 skipped before the recovery revision is
committed, pushed and deployed.
