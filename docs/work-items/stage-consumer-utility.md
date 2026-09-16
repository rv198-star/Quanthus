# P3-STAGE-033 — Layered Stage Consumer Utility

| Field | Value |
| --- | --- |
| Design status | approved bounded experiment |
| Delivery gate | open |
| Predecessors | `P3-STAGE-029`, `P3-STAGE-032`, `P3-STRUCT-002` |
| Governing preregistration | `../../config/stage-learning/stage-consumer-utility-prereg.v1.json` |
| Route review | `../reviews/stage-p033-value-route-review-20260916.md` |
| Authority | development-only interpretation experiment |

## Goal

Answer one bounded product question:

> Does adding the validated Layered Stage10/11 overlay to the exact same Continuous Facts materially
> reduce consumer interpretation errors or review cost compared with Facts-only?

This is not a prediction, economic, strict-PIT or trading experiment.

## Fixed evidence and population

Reuse only the exact P029 ten-observation population reconstructed from the same two accepted
immutable F9 source artifacts. P033 may not read new market data, open the DWD provider, touch the
consumed 2022-2024 arbitration partition or the preserved 2025 blind partition, replace observations,
or tune Stage10/11 semantics.

The two experiment arms are:

1. `facts_only`: continuous Support Evolution facts plus the exact Stage10/11 semantic definitions;
2. `facts_plus_layered`: exactly the same packet plus the validated P028 development-shadow layered
   overlay.

Continuous Facts remain authoritative in both arms. The layered overlay is additive.

## Consumer assignment

Four role-separated consumers are used: short-term/event-driven, fund/midterm-risk, quant-research,
and data-governance. Each role sees all ten observations exactly once: five Facts-only and five
Facts+Layered. A deterministic parity assignment gives each observation two evaluations per arm and
prevents the same role from seeing both versions of one observation.

The evaluator arm label and answer key remain hidden until all role outputs are sealed. One exact
provider/model identity is required across all four isolated evaluator sessions. Evaluator runtime
unavailability blocks the experiment before packet exposure; a host-simulated substitute is not
permitted after preregistration.

## Frozen questions

Each packet asks the same four questions:

- is Stage10 structural-failure context still active?
- is Stage11 nested rebound condition active?
- does the rebound mean the Stage10 parent context is resolved?
- choose the canonical interpretation: structural failure only vs structural failure with nested
  rebound vs abstain.

The deterministic P029 projection owns the sealed answer key.

## Decision gate

The primary product metric is packet-level critical interpretation error. A packet is critical-error
positive if any frozen answer is wrong or abstained.

`PASS_CONSUMER_UTILITY_INCREMENT` requires either:

- **accuracy path:** Layered reduces critical-error packets by at least 4 of 20 evaluations, no role
  worsens by more than one error, and median elapsed time is no worse than 1.25x Facts-only; or
- **efficiency path:** Layered has no more critical errors, median elapsed time is at most 0.75x
  Facts-only, and median response tokens are at most 0.85x Facts-only.

`NO_PASS_NO_MATERIAL_CONSUMER_UTILITY` applies when error reduction is at most one packet and the
efficiency path fails. Other valid completed outcomes are `INCONCLUSIVE_CONSUMER_UTILITY`.

These are bounded product gates, not statistical significance or forward-market claims.

## Stop and re-review conditions

Stop before continuing and reconvene the four-role review if any implementation requires:

- a changed population, source or knowledge-time claim;
- altered Stage10/11 thresholds, hierarchy or `primary_stage` behavior;
- different primary metrics or thresholds after packet/result exposure;
- prospective shadow collection, economics, future outcomes or protected partitions;
- a different model/provider after any evaluator result is visible.

## Completion

P033 closes only after deterministic packet/assignment generation, sealed four-role evaluator
outputs, preregistered metric calculation, role-separated result review, repository verification and
push. It opens no automatic successor.
