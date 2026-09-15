# P3-STAGE-022 Primary-Unique / Contingent-Fallback Engineering Acceptance

Design status: accepted engineering freeze  
Delivery gate: ready for one metadata-only feasibility Job

## Scope

P022 refines only resource independence semantics after P021 restored per-cell metadata capacity but
showed that pre-reserving all `8×32=256` rows as globally unique reached only 243. It does not read
market bars or Stage1/12 semantics and does not reopen P020.

## Frozen contract

- active primary panel = 8 cells × 24 = 192 rows;
- all 192 primary economic identities must be globally unique;
- primary exact windows come only from the P021 repaired metadata reservoir;
- dormant fallback pool = eight candidates per cell;
- no fallback identity may equal any primary identity;
- cross-cell duplicate identities are legal only while fallback rows remain dormant;
- later activation is same-cell only and must preserve global identity uniqueness against the realized
  primary panel and previously activated fallbacks;
- all fallback activation is later, technical-readiness-driven and separately gated;
- worst-case simultaneous activation of all 64 fallbacks is diagnostic, not a P022 pass requirement.

## Implementation

The P022 runner uses deterministic bipartite matching for the primary panel rather than simple greedy
cell allocation. This prevents a false shortfall when an identity shared by two cells can be reassigned
to preserve a scarcer cell. Exact windows are then selected by frozen metadata-only hashes.

After primary identities are fixed, each cell freezes the first eight non-primary fallback identities
under a separate frozen seed. The artifact also computes the maximum simultaneous activation possible
inside those already frozen pools as a risk diagnostic. It does not activate any fallback.

## Runtime governance

- Job kind: `quanthus.stage-primary-unique-contingent-fallback.v1`
- maximum Jobs: 1
- automatic retries: 0
- same-revision submission is idempotent
- different revision is rejected without separately frozen recovery authority
- stable RX6800 launcher exposes only `submit-p022-b`; no generic shell/cancel path is added.

## Current verification

Focused implementation/worker/helper verification before repository-wide acceptance:

```text
165 passed
Ruff PASS
git diff --check PASS
```

Additional governing-document and contract tests are required before the result-before-run checkpoint
is committed. A full repository `make check` is also required before deployment.

## Claim ceiling

Engineering acceptance proves only that the P022 metadata feasibility experiment is preregistered and
executable. It does not prove primary/fallback capacity, input readiness, Stage1/12 semantic quality,
preserved validity, economic value or trading authority.
