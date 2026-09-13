# P3-STAGE-012-A Atomic Proposition / Resolver Audit — 2026-09-13

## Disposition

```text
P3-STAGE-012-A: PASS_ATOMIC_PROPOSITION_FIRST
FRESH DATA: NOT OPENED
PRESERVED 2021: UNREAD
THRESHOLD / CANDIDATE SEARCH: NONE
```

This audit uses only repository contracts and already-consumed P008-P011 evidence. It changes no
runtime predicate and selects no numeric parameter.

## Conclusion

The existing three-proposition ontology remains the correct starting point:

```text
structural_instability_20
coherent_directional_relocation_20
orderly_consolidation_20
```

The important correction is the **qualification unit**, not the truth table. Future successor work
must evaluate the atomic propositions before evaluating the projected Stage0/Stage2 label.

The current bounded projection remains:

```text
I=TRUE,  R=FALSE, O=FALSE -> STAGE_0
I=FALSE, R=FALSE, O=TRUE  -> STAGE_2
I=TRUE,  R=TRUE,  O=FALSE -> mixed_directional_instability; Stage0/2 projection NEITHER
R=TRUE,  O=FALSE          -> outside Stage0/2 projection
O=TRUE with I=TRUE or R=TRUE -> AMBIGUOUS_CONFLICT
all positive propositions FALSE -> NEITHER
UNKNOWN-dependent combinations -> AMBIGUOUS
```

`instability + relocation` is valid coexistence, not a resolver conflict. It receives no new peer
Stage code in A; it remains preserved atomic state plus reason/modifier metadata pending broader
taxonomy review.

## Why this matters for P011

A projected Stage0 false negative is not equivalent to an instability false negative. A case can have
positive instability and still correctly remain outside Stage0 because relocation is also positive.
The P011 closeout mixed these two failure meanings when reporting Stage0 coverage, which was correct
for its frozen gate but is not sufficient evidence for deciding which atomic proposition needs repair.

Future blind review must therefore preserve and score the atomic ledgers directly. Agreement on a
projected status does not prove every underlying atomic proposition was correctly recognized.

Machine evidence:
`config/stage-learning/stage-atomic-proposition-ontology-audit.v1.json`.

## Authority ceiling

A opens no implementation, market-data read, preserved evaluation, threshold search, candidate
surface, new Fact, daily label, strategy or trading authority.
