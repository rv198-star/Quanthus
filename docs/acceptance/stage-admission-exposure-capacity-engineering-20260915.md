# P3-STAGE-021-B Admission Exposure Capacity Engineering Acceptance

Design status: accepted engineering freeze  
Delivery gate: ready for one metadata-only capacity Job

## Scope

This acceptance freezes the implementation that compares the old P020-B exact-window exclusion rule
with the P021-A L2A/L2B exposure taxonomy. It does not reopen P020, create a new development sample,
read market bars, or evaluate Stage1/12 semantics.

## Frozen inputs

The Job exact-binds and verifies:

- P013 exact-plan result/artifact;
- P014 admission result/artifact;
- P014 qualified-plan result/artifact;
- P021-A taxonomy;
- P020-B original resource contract;
- the accepted listing/calendar metadata reconstruction contract.

The P014 qualified 256 exact windows must be a subset of the P014 admission ledger. Those 256 remain
semantic-consumed. Only admission-ledger windows outside that set are classified L2A and allowed back
into the later semantic-development reservoir.

## Capacity outputs

For all eight frozen Era × Board cells the Job reports:

- old-rule candidate identity/window counts;
- repaired-taxonomy candidate identity/window counts;
- identities/windows restored by L2A reclassification;
- whether repaired per-cell identity capacity reaches the unchanged minimum 32;
- whether a deterministic globally identity-unique 8×32 metadata reserve is feasible under the same
  fixed cell order and P020 identity seed.

No exact P020 semantic plan is frozen by this Job.

## Runtime governance

- Job kind: `quanthus.stage-admission-exposure-capacity.v1`
- maximum Jobs: 1
- automatic retries: 0
- same-revision submission is idempotent
- different revision is rejected without a separately frozen recovery authority
- stable RX6800 helper exposes only `submit-p021-b`; no generic shell/cancel path is added

## Authority

Allowed:

- listing/calendar metadata;
- immutable P013/P014 artifact metadata and exact identity-window membership.

Forbidden:

- market bars or admission-market reads;
- Stage1/12 semantic facts or opinions;
- thresholds/candidates/scoring;
- P020 reopen;
- preserved 2021, 2022-2024, 2025;
- daily/strategy/trading use.

## Verification before repository-wide check

```text
161 passed
Ruff PASS
git diff --check PASS
```

Repository-wide `make check` remains required before commit/deploy.

## Claim ceiling

Passing this engineering acceptance means only that P021-B is ready to measure metadata capacity
under the already-frozen taxonomy. It does not establish that L2A reuse is sufficient, reopen P020, or
qualify any Stage proposition.
