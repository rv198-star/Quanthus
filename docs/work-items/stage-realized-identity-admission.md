# P3-STAGE-023 — Realized-Identity Admission and Contingent Replacement

Design status: approved for execution engineering  
Delivery gate: ready for one primary-first admission Job after repository acceptance

Machine contract: `../../config/stage-learning/stage-realized-identity-admission.v1.json`  
Parent result: `../../config/stage-learning/stage-primary-unique-contingent-fallback-result.v1.json`

## Goal

Resolve the P022 fallback-reservation shortfall without weakening identity independence of the actual
review panel. Bind the exact P022 192-row primary plan, freeze a deterministic same-cell fallback
reservoir before any readiness result, admit all primaries first, and activate fallbacks only for
technical input-readiness deficits while preserving global identity uniqueness of the **realized**
8 × 24 panel.

## Frozen semantics

- The exact P022 primary plan has 192 rows: eight Era × Board cells × 24, globally identity-unique.
- All 192 primaries are screened for 61-session input readiness before any fallback activation.
- A primary that fails technical readiness does **not** consume realized identity independence.
- A primary that passes readiness immediately joins the realized identity set.
- The fallback reservoir is frozen before readiness results and is same-cell only.
- A planned primary identity may appear in a different fallback window, because planned membership is
  not realized exposure.
- Any fallback whose identity is already realized is skipped without a market read.
- A fallback is activated only after it passes the same 61-session input-readiness contract and only
  while its cell remains below 24 realized rows.
- Every activated fallback identity joins the global realized set and blocks later activation of that
  identity in any cell.
- The final realized panel must contain exactly 192 rows, eight cells × 24, and 192 unique identities.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-023-A | Freeze primary-first admission / realized-identity semantics | P3-STAGE-022 | closed preregistration in machine config; no input read |
| P3-STAGE-023-B | Execute one primary-first admission and contingent replacement Job | P3-STAGE-023-A | ready after engineering acceptance |
| P3-STAGE-023-C | Review realized plan and close P023 | P3-STAGE-023-B | blocked until sole Job completes |
| P3-STAGE-023-D | Gate P020-C preregistration | P3-STAGE-023-C | blocked; a P023 PASS may only make a separately versioned P020-C preregistration legal |

## Failure semantics

If any cell cannot realize 24 globally identity-unique admission-ready rows under the frozen fallback
order, close `CLOSED_REALIZED_ADMISSION_CAPACITY_SHORTFALL`. Do not lower the 24-per-cell target, add
boards/years, use cross-cell fill, reopen P022 ordering, or consume Stage1/12 semantic evidence.

## Boundaries

P023 may read exact-window market inputs only to reproduce deterministic input readiness. It may not
publish/retain raw OHLC, build Structure facts, read Stage1/12 labels or reviewer opinions, search
thresholds, score candidates, open preserved 2021, open 2022-2024/2025, emit daily/strategy output, or
trade.

A P023 PASS does **not** start blind review. It authorizes only a separate P020-C preregistration that
must bind the exact realized-plan digest before any semantic packet is built.
