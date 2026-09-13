# P3-STAGE-012-D Expert Review — 2026-09-13

## Provenance

This is one ChatGPT assistant simulating four responsibilities: short-term trader, fund manager,
quant researcher, and data/evidence governance. It is same-origin simulation, not four independent
models and not human-expert validation.

The review is bound to frozen A-C commit
`29831e85ef1771235d7d599c86c37c84058e415a`. It opens no market-data read, preserved 2021 access,
threshold search, runtime Job or Fact registration.

## Frozen A-C basis

```text
A  PASS_ATOMIC_PROPOSITION_FIRST
B  PASS_CONSUMED_SEMANTIC_ADJUDICATION
   0 guard-overfire / 2 reference-undercall / 9 genuine-coexistence
C  INCONCLUSIVE
```

The panel reviews those findings; it does not rewrite them.

## 1. Short-term trader responsibility

### Support

Atomic-first semantics match the tape-level distinction better than a forced Stage0 label. A path can
be materially repricing and still be structurally unstable. Calling that entire shape either
"directional" or "Stage0" loses information that matters to short-horizon risk and timing.

The nine B coexistence cases are therefore a useful warning: the P011 Stage0 coverage metric combined
an instability question with a relocation-veto question. Future qualification should report both.

C also identifies the right failure mode. Compact disorder should not be defined by generic churn:
valid Stage2 can oscillate heavily inside a tight contracting band. The more plausible distinction is
whether the envelope repeatedly fails to resolve in one direction even when its endpoint width is
small.

### Counterexample

A narrow box can be noisy for many sessions without being unstable in any economically useful sense.
Likewise a one-leg repricing followed by a narrow hold can have large historical range variation while
remaining a completed directional move.

### Veto

- no third Stage0 channel directly from the fourteen consumed misses;
- no "high churn" compact-disorder rule;
- no fresh data until the envelope hypothesis survives synthetic adversarial cases.

## 2. Fund-manager responsibility

### Support

The A truth table is preferable to forcing every observation into a single primary stage. A coherent
repricing can coexist with structural stress. For portfolio use that is naturally a context/modifier:
"trend/repricing under instability" is different from direction-neutral disorder but does not yet need
a new peer Stage code.

B provides no reason to weaken P008. Two cases look like relocation under-calls and nine look mixed;
zero cases establish a reproducible guard-overfire shape. Retuning relocation from this packet would
trade one taxonomy problem for another.

### Counterexample

A taxonomy that preserves every coexistence as a new stage can explode combinatorially. The system
still needs a bounded downstream projection for consumers that require one primary state.

### Veto

- keep `mixed_directional_instability` as preserved atomic coexistence plus reason/modifier metadata;
- do not create a new primary Stage code in D;
- keep P008 relocation implementation frozen;
- retain repaired Stage2 semantics as the leading assumption until new counterevidence appears.

## 3. Quant-research responsibility

### Support

A corrects the experiment target: proposition quality should be measured before projected Stage0/2
quality. Metrics must separate instability recall, relocation recall, order recall, coexistence rate,
projection conflict and single-stage coverage.

B is sufficient to reject a result-dependent relocation retune. It is **not** sufficient to promote
the 9/2 post-closeout classifications to new ground truth because they are same-origin adjudication on
consumed evidence.

C correctly ends `INCONCLUSIVE`. All audited scalar Fact ranges overlap between compact Stage0 and
Stage2, and the current broad-path-disorder composite is TRUE for both 14/14 compact cases and 21/21
Stage2 cases. The scratch envelope descriptors show some distribution shift but not separability.

### Counterexample

It would be easy to inspect those distributions and choose a rolling-envelope variation threshold
that recovers the fourteen misses. That would be another post-result candidate search under a new
name.

### Veto

- no threshold from P008-P011 consumed observations;
- no candidate count, grid or scoring surface in D;
- no claim that B independently validates mixed labels;
- no claim that C proves a new Fact is necessary.

### Recommendation

Authorize only a **synthetic-only** next phase if the owner approves: atomic review/resolver contract
plus threshold-free envelope semantics and adversarial conformance.

## 4. Data / evidence-governance responsibility

### Support

The successor should preserve the atomic ledger as first-class evidence. For each semantic role,
future review artifacts should retain instability, relocation and order truth values before any
projected Stage status. Cross-role disagreement at the atomic layer must remain visible even if the
two projected statuses happen to match.

An envelope hypothesis can be prototyped without creating a Catalog Fact. Synthetic path functions
may establish the semantics and failure modes first. If a later review decides a formal Fact is
needed, its identity must separately freeze scale, PIT closure, required inputs, missing-data
semantics and downstream consumers.

### Veto

- no hidden production feature behind a synthetic prototype;
- no Fact registration in D;
- no use of preserved 2021 as a convenient next sample;
- no fresh-cohort inventory until synthetic semantics are accepted.

## Cross-review decisions

| Question | Decision |
| --- | --- |
| Atomic-first qualification? | **APPROVE AS SUCCESSOR ARCHITECTURE** |
| Existing Stage0/Stage2 projection? | retain as downstream projection for next synthetic phase |
| Mixed directional instability? | preserve as atomic coexistence + reason/modifier; no peer Stage yet |
| Retune P008 relocation? | **NO** |
| Reopen relocation implementation now? | **NO** |
| Retain P011 Stage2 semantic topology? | yes, as leading design assumption, not qualified thresholds |
| Does C prove a new Fact is required? | **NO** |
| Is compact-disorder work dead? | no; approve only a synthetic envelope-resolution hypothesis for owner consideration |
| Fresh development now? | **NO** |
| Preserved 2021? | unread / closed |

## Recommended bounded next phase

The panel recommends, subject to owner approval, one synthetic-only package:

```text
P3-STAGE-012-E1  atomic Primary review + resolver contract
P3-STAGE-012-E2  threshold-free range-envelope resolution/persistence prototype
P3-STAGE-012-E3  adversarial synthetic conformance + review
```

E1 must preserve all three atomic propositions and explicitly report mixed-state/projection
denominators. E2 is **not** a Fact registration and cannot import thresholds learned from consumed
market evidence. E3 must include at least:

1. high-churn but genuinely orderly contracting band;
2. one-step or one-leg repricing followed by a narrow hold;
3. repeated failed envelope resolution ending in a narrow range;
4. stable narrow non-contracting noise;
5. mixed relocation + instability;
6. unavailable/insufficient inputs.

Only after E review may the project decide whether a formal Fact, a third instability channel, or only
a projection/reference repair is justified. Fresh cohort design remains later.

## Verdict

```text
P3-STAGE-012-D: REVIEW COMPLETE
RECOMMENDATION: SYNTHETIC-ONLY ATOMIC + ENVELOPE ROUTE
OWNER APPROVAL FOR E: REQUIRED
P008 RELOCATION: FROZEN
NEW FACT: NOT AUTHORIZED
FRESH DATA: NOT AUTHORIZED
PRESERVED 2021: UNREAD
```

Machine result:
`config/stage-learning/stage-atomic-proposition-d-expert-review-result.v1.json`.
