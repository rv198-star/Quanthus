# P3-STAGE-015-B Complete Additional Review Set Engineering Acceptance

Status: `PASS_ENGINEERING_READY_FOR_ONE_METADATA_FREEZE_JOB`

## Accepted boundary

P015-A completed successfully before this engineering freeze. Its sole Job
`stage_19326342d3bf497fba2964fbed10c29f` produced an immutable readiness artifact with 256 fixed
P014-B rows, 252 review-ready rows, all 192 P014-reviewed resources still ready, four
`PRIMARY_FACTS_UNAVAILABLE` rows, and exactly 60 additional rows marked `new_review_required=true`.
The readiness ledger digest is
`sha256:9b5f9b6c02f5df91f49fb061339307da5612af97696437b13dca2d10e4996df7`.

P015-B is accepted only to freeze those exact 60 rows as metadata. It must select **every** row
matching the frozen readiness predicate and may not stop when any semantic positive-count target is
reached.

## Frozen inputs

- P015-A readiness Job: `stage_19326342d3bf497fba2964fbed10c29f`
- P015-A manifest: `sha256:99e39c9ccce5f171bac67daee06fe2084216c46bb68bc8a40b4fb605fc55e32a`
- P015-A readiness ledger: `sha256:9b5f9b6c02f5df91f49fb061339307da5612af97696437b13dca2d10e4996df7`
- P014-B qualified-plan manifest: `sha256:f14c065d0bf18b6491b9220132d1be3c10e608fb9a69f4141c576e351aa7a173`
- P014-B plan digest: `sha256:206ed58e8349de59efd52caad958f7bd0f2f9d79f1ee0199eea6a079b6c14829`
- immutable P014 review ledger: `sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69`

## Runtime contract

The admitted kind is `quanthus.stage-complete-reserve-additional-review-set.v1` with one Job,
zero automatic retries, and a 600-second wall budget. The AgentDock helper exposes only the named
`submit-p015-b <exact-revision>` route; no generic submission, shell, cancellation, or file access is
added.

The implementation joins the immutable P015-A readiness metadata to the immutable P014-B plan by
`(security_id, window_id)` and may retain only exact resource metadata plus the readiness source
closure digest. It performs no market read and no Structure fact build.

## Fail-closed rules

P015-B rejects:

- any readiness or plan manifest/digest drift;
- any P014 review-ledger binding drift;
- any denominator other than exactly 60 matching rows;
- duplicate resource identity/window pairs;
- any row whose Era/Board/cell differs across the readiness and plan artifacts;
- any semantic-value field in the published set;
- any semantic sorting, positive-count stop, random sampling, replacement, or new identity discovery.

## Authority ceiling

P015-B does not read semantic values, reviewer opinions, threshold basis, candidate outputs,
selection transport, preserved 2021, or 2025. It does not score candidates or authorize Stage,
daily, strategy, or trading behavior.

A successful P015-B result may open only P015-C engineering for blind review of the exact frozen
additional set. Existing P014 opinions remain immutable and are never re-reviewed.

## Verification

Focused Worker/helper/artifact and traceability tests pass at 123 passed. Repository-wide `make
check` passes at 2432 passed / 7 skipped before the runtime checkpoint is committed and deployed.
