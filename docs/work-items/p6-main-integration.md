# P6 Base 2.4 integration into main

| Field | Value |
| --- | --- |
| Parent | [Work-item index](index.md), P6-R1 / #511 and P10 #538/#539 |
| Governing design | [P10 ownership](../design/lld/12-permanent-data-line.md), [data foundation](../design/lld/01-data-foundation.md), [P6](../design/lld/08-validation-governance.md) |
| Design status | approved: Owner instructed split integration into main on 2026-10-23 |
| Delivery gate | accepted-conditional for bounded source integration; GitHub-hosted CI cannot start because of billing |

## Source and boundaries

Retain `codex/p6-538-regression` at
`c1faad291cce16cc153de1780ba5a5a58bbaae61` as the accepted source. The starting
main is `f5b87dab454a719240d56944cb2243ed8765113f`, with common ancestor
`3382b63fcd29e3b1e67b2f90aee41372c27831ac`. The source has 396 commits beyond
main; importing that entire history would include unrelated collector and
operational changes. Select only the P6 dependency closure and its tests/config.

Preserve main's newer data-quality policies and legacy Daily interfaces. Port
only the explicit native-parent helpers required by the selected source readers;
the main quality-digest implementation must remain identical. The P3 byte-frozen
`daily_domain.py`, `daily_ods_catalog.py` and their frozen request remain identical.
The optional price-basis candidate builder is outside this selected dependency closure. Existing worktrees, original commits, sealed
inputs, acceptance receipts and ledgers remain unchanged. This is source-code
integration, not production activation or a new warehouse publication.

## Ordered batches

0. **CI baseline prerequisite ([#549](https://github.com/rv198-star/Quanthus/pull/549)).**
   Restore synthetic runtime/provider isolation and canonical date validation;
   explicitly enable TypeScript test loading. Frozen production policies remain
   unchanged. Main already had these CI failures before P6 integration.
1. **P10 source foundation ([#550](https://github.com/rv198-star/Quanthus/pull/550)).** Bring the opt-in Base 2.4 read/projection and purpose
   interfaces, their required standalone verifier helpers, explicit contracts and
   synthetic tests. P10 owns qualification. Existing legacy consumers remain on
   their own interfaces; source diagnostics never imply execution or PIT admission.
2. **P6 consumer and historical scope ([#551](https://github.com/rv198-star/Quanthus/pull/551)).** Integrate the qualified-input consumer,
   isolated paper-cycle/runtime hooks, fixed-snapshot historical replay and the
   Owner's recorded closeout. Preserve expert/veto/next-open policies, the cancelled
   prospective programme and all prior negative historical-PIT findings.
3. **Bounded #538 successor ([#552](https://github.com/rv198-star/Quanthus/pull/552)).** Integrate the portable semantic repair validator,
   explicit effective view and repaired-input regression. Keep the precise
   allowed-key closure and real later knowledge time. Full-image physical inclusion
   remains separately tracked by #539, not accepted by a code merge.

Each batch is a separate commit/PR with an explicit predecessor. The shared
prerequisite is the selected, versioned source foundation; no market input,
feature panel or replay artifact is rebuilt during this integration. Focused
checks run while editing. Final local `make check` and documentation contracts validate the actual
integrated source; earlier task-branch tests do not replace these checks. Hosted
CI has not executed because account billing/spending admission fails. Record
baseline failures separately from integration regressions.

## Acceptance and recovery

Verify every selected source/config/test path and retain an external selection
receipt, source SHA, main SHA, commands and complete plain-text check logs.
Source/config changes must not include market payload, credentials, local databases
or generated run artifacts. Keep semantic source differences explicit, especially
compatibility with main's current Daily quality gates.

The accepted historical producer and new repair have immutable earlier receipts.
This integration does not grant historical PIT, economics, prospective credit or
production execution. No CURRENT, host service, container deployment or frozen
policy change is authorized by merging these PRs. Actual main merge identities are recorded by the four linked PRs and the
checksummed read-only integration handoff; use those primary records rather than
a cached local branch identity.

## Completed source-validation checkpoint

The corrected integrated code at `4af28bc0bc9175ac810fb5ee6f29c681587241f1`
passed local `make check`: 4,414 passed and 20 skipped with Python 3.10.12.
The final integrated Web install/lint/test passes all 18 tests on Node 22.23.3.
Changed-file Ruff passes; the 65-module new adapter/verifier type subset passes.
Broad strict mypy remains unqualified (82 diagnostics in nine files, including
ported legacy dependency debt). Python 3.12 and pinned CI Node 22.13 were not run
locally. The hosted CI jobs were rejected before execution by account billing.
These limitations are recorded separately from the passing local source checks.

The first complete-check attempt exposed the frozen legacy Daily file conflict.
Restore that path to exact main bytes and exclude its unrelated opt-in candidate
builder/tests; never change the frozen probe to make it pass. The affected
15-case legacy subset and all earlier failure-marker cases pass after correction.
The corrected full run above is the integration evidence. This final checkpoint
is documentation only; source, configuration and tests match the checked commit.

This checkpoint is published by the last PR after its three predecessors. It
does not change source qualification, formal Base #539 incorporation, historical
PIT/economic acceptance or production activation. The original source branch,
workspace changes and prior run evidence remain preserved.
