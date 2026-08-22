# P3-POS-004 Trial-Reduction Compatibility Proposal (2026-08-22)

| Field | Value |
| --- | --- |
| Status | outcome-blind proposal; pending human semantic approval |
| Work item | `P3-POS-004` / Issue #185 |
| Blocking | `P3-ATTR-001` / Issue #54 |
| Proposed strategy | `candidate.p4-trend-risk@1.0.1` |
| Proposed semantic digest | `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c` |
| Result access | none |

## Problem

The accepted strategy declares a 30% Trial entry, one legal reduction to 10%, disabled adds, and
enabled reductions. Position lifecycle `1.0.0` permits `REDUCE_FILLED` only from Dynamic, while a
strategy with disabled adds can never move from Trial to Dynamic. The January 2020 Sentinel proved
entry topology and deterministic audit output but did not execute held-position P2 actions.

Full attribution would therefore be unable to replay the approved position-risk expert truthfully.
Issue #54 remains result-sealed until this incompatibility is resolved.

## Proposed correction

The runtime addition is versioned and preserves legacy behavior:

- lifecycle `1.0.0` continues to reject Trial reduction;
- lifecycle `1.1.0` adds only `Trial + REDUCE_FILLED -> Trial`;
- the transition still requires one real P2 partial-sell Fill and a strictly smaller, positive
  same-security ledger quantity;
- a partial sell releases proportional position cost, retains the remaining position, and cannot
  trigger unit-close settlement;
- the formal strategy changes exactly `strategy_version`,
  `product_candidate.strategy_version`, and
  `position_and_execution.position_state_machine_version` to `1.0.1`, `1.0.1`, and `1.1.0`;
- all experts, thresholds, fallbacks, questions, metrics, materiality rules, DWD bindings, sealed
  partitions, execution costs, funding rules, and resource limits remain unchanged.

The resulting outcome-blind strategy semantic digest is
`sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`.

## Rejected substitutions

Ignoring `REDUCE` or translating it to `HOLD` would make the risk expert's declared held-position
surface non-executable. Translating it to `FULL_EXIT` would exaggerate the expert's authority and
change the counterfactual. Removing reduction would delete an approved expert behavior. Mutating
lifecycle `1.0.0` would silently rewrite accepted evidence. None is admissible.

## Approval and execution gate

Human approval authorizes creating and admitting the exact `1.0.1` strategy card at the digest
above and rerunning a bounded January 2020 compatibility Sentinel. It does not authorize opening
attribution results, the full 2020-2021 four-Run resource budget, Shadow, notification, execution,
or any arbitration/permanent-blind partition.

After approval, acceptance requires legacy full-sell byte compatibility, deterministic partial
sell and v1.1 lifecycle evidence, an exact registry/card rebind, and a byte-identical bounded
Sentinel reproduction before Issue #54 resumes.
