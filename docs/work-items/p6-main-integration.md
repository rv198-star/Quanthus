# P6 Base 2.4 integration into main

| Field | Value |
| --- | --- |
| Parent | [Work-item index](index.md), P6-R1 / #511 and P10 #538/#539 |
| Governing design | [P10 ownership](../design/lld/12-permanent-data-line.md), [data foundation](../design/lld/01-data-foundation.md), [P6](../design/lld/08-validation-governance.md) |
| Design status | approved: Owner instructed split integration into main on 2026-10-23 |
| Delivery gate | open until the bounded PR stack are integrated and checked |

## Source and boundaries

Retain `codex/p6-538-regression` at
`c1faad291cce16cc153de1780ba5a5a58bbaae61` as the accepted source. The starting
main is `f5b87dab454a719240d56944cb2243ed8765113f`, with common ancestor
`3382b63fcd29e3b1e67b2f90aee41372c27831ac`. The source has 396 commits beyond
main; importing that entire history would include unrelated collector and
operational changes. Select only the P6 dependency closure and its tests/config.

Preserve main's newer data-quality policies and legacy Daily interfaces. Port
only the explicit native-parent helpers required by the selected source readers;
the main quality-digest implementation must remain identical. Existing worktrees, original commits, sealed
inputs, acceptance receipts and ledgers remain unchanged. This is source-code
integration, not production activation or a new warehouse publication.

## Ordered batches

0. **CI baseline prerequisite ([#549](https://github.com/rv198-star/Quanthus/pull/549)).**
   Restore synthetic runtime/provider isolation and canonical date validation;
   explicitly enable TypeScript test loading. Frozen production policies remain
   unchanged. Main already had these CI failures before P6 integration.
1. **P10 source foundation.** Bring the opt-in Base 2.4 read/projection and purpose
   interfaces, their required standalone verifier helpers, explicit contracts and
   synthetic tests. P10 owns qualification. Existing legacy consumers remain on
   their own interfaces; source diagnostics never imply execution or PIT admission.
2. **P6 consumer and historical scope.** Integrate the qualified-input consumer,
   isolated paper-cycle/runtime hooks, fixed-snapshot historical replay and the
   Owner's recorded closeout. Preserve expert/veto/next-open policies, the cancelled
   prospective programme and all prior negative historical-PIT findings.
3. **Bounded #538 successor.** Integrate the portable semantic repair validator,
   explicit effective view and repaired-input regression. Keep the precise
   allowed-key closure and real later knowledge time. Full-image physical inclusion
   remains separately tracked by #539, not accepted by a code merge.

Each batch is a separate commit/PR with an explicit predecessor. The shared
prerequisite is the selected, versioned source foundation; no market input,
feature panel or replay artifact is rebuilt during this integration. Focused
checks run while editing. Final `make check`, documentation contracts and CI
validate the actual integrated source; earlier task-branch tests do not replace
these checks. Record baseline CI failures separately from integration regressions.

## Acceptance and recovery

Verify every selected source/config/test path and retain an external selection
receipt, source SHA, main SHA, commands and complete plain-text check logs.
Source/config changes must not include market payload, credentials, local databases
or generated run artifacts. Keep semantic source differences explicit, especially
compatibility with main's current Daily quality gates.

The accepted historical producer and new repair have immutable earlier receipts.
This integration does not grant historical PIT, economics, prospective credit or
production execution. No CURRENT, host service, container deployment or frozen
policy change is authorized by merging these PRs. Update this recovery checkpoint
with the actual PRs and final main identity when all batches complete.
