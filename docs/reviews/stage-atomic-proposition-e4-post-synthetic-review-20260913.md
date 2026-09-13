# P3-STAGE-012-E4 Post-E Expert Review — 2026-09-13

## Provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is same-origin simulation, not four independent
models or human-expert validation.

The review is bound to frozen commit
`24fc416a09c535cf7bed501b7937c65ec511a95f` and E result digest
`sha256:1276254b1274ef4db0421067399b6afe4d43bf24e810dda404fdef2f9fa03036`.
It opens no market data, threshold search, candidate generation, Fact registration, preserved 2021,
strategy or trading authority.

## Short-term trader

**Support.** The E2 event trace captures a distinction that churn could not: a high-churn contracting
band can remain inside its prior close envelope, while repeated upper/lower escape followed by re-entry
expresses failed directional resolution. That is operationally closer to the “structure cannot hold a
direction” idea that motivated compact disorder.

**Counterexample.** A single failed breakout or one mean-reverting escape is ordinary market behavior.
The synthetic example with four alternating resolved escapes proves expressibility, not a count cutoff.

**Veto.** Do not turn `escape_count`, `alternation_count`, or one failed escape into a Stage0 threshold.

## Fund manager

**Support.** Persistent one-side escape and alternating failed resolution are meaningfully different
path organizations. The first can coexist with coherent repricing; the second can coexist with
structural stress. Keeping those as neutral observations preserves the atomic model.

**Counterexample.** Daily close geometry cannot claim intraday breakout intent, accumulation,
distribution, or actor behavior. A close-only prototype may also miss intraday excursions by design.

**Veto.** Do not create a new peer Stage and do not weaken P008 relocation to make the new descriptor
“win” conflicts.

## Quant researcher

**Support.** The synthetic package is non-degenerate and deterministic: high-churn orderly and stable
narrow cases produce no failed-resolution trace, one-leg repricing produces a persistent one-side
escape, the adversarial failed-resolution path produces alternating resolved escapes, and unavailable
input fails closed. No learned threshold is used.

**Counterexample.** Hand-constructed synthetic paths cannot establish market separability, optimality of
the 10-session prior envelope, or predictive/economic usefulness.

**Veto.** No fresh search, threshold selection, candidate grid or qualification claim may be derived
from the synthetic PASS.

## Data / evidence governance

**Support.** Escape/re-entry is neutral causal path geometry. If promoted, its natural owner is the
existing `B6-PATH / Directional Path Geometry Fact Family`, which already owns threshold-free close-path
geometry such as range traversal and counter-direction excursion.

**Counterexample.** Leaving a productionized observable under `stage_learning` would create a hidden
Stage-specific Fact and duplicate Structure ownership.

**Veto.** No new Fact family or Catalog registration before a separate Structure-owned design/contract
and synthetic acceptance.

## Resolution

The panel does **not** recommend parking the envelope idea: E demonstrated a real semantic dimension
that was missing from endpoint/churn summaries. It also does **not** recommend registering the current
prototype as a Fact.

The bounded decision is:

```text
E1 atomic-first contract                ACCEPT for successor architecture
E2 stage-learning envelope prototype    KEEP SYNTHETIC / RESEARCH-ONLY
E3 synthetic conformance                ACCEPT within synthetic claim ceiling
P008 relocation                         KEEP FROZEN
new peer Stage                           NOT AUTHORIZED
new Fact family                          NOT SUPPORTED
formal Fact registration now            NOT AUTHORIZED
fresh market experiment                  NOT AUTHORIZED
preserved 2021                           UNREAD / CLOSED
```

If the owner approves a next phase, the panel recommends **P3-STAGE-012-E5**:

> design and synthetically qualify a **Structure-owned B6-PATH envelope escape/re-entry extension**.

E5 must decide the minimal neutral concepts and identities, exact 20/10 profile and reset/coordinate
semantics, missingness behavior, and synthetic invariance/adversarial cases. The current
`stage_learning` prototype is evidence for that design, not the final owner or Fact identity.

Only after E5 closes should the project decide whether these semantics deserve formal B6-PATH
registration or should remain research-only. Fresh-cohort inventory remains later and separately
authorized.
