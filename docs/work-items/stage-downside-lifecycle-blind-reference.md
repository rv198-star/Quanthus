# P3-STAGE-020-C — Capacity-Limited Blind Semantic References

Design status: approved preregistration  
Delivery gate: packet artifact frozen; role-isolated blind opinion collection ready

Parent: `stage-downside-lifecycle-proposition.md`  
Machine contract: `../../config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json`  
Seal/aggregation contract: `../../config/stage-learning/stage-downside-lifecycle-reference-seal.v1.json`  
Resource ceiling: `../../config/stage-learning/stage-clustered-identity-admission-result.v1.json`  
Semantic source: `../../config/stage-learning/stage-downside-lifecycle-concept.v1.json`  
Fact substrate: `../design/structure-trend-evolution-facts.md`  
Engineering acceptance: `../acceptance/stage-downside-lifecycle-review-packet-engineering-20260916.md`

## Goal

Test whether the exact outcome-blind P024 development panel can supply sufficiently rich synthetic
expert references for the Stage-12 `downside_context` proposition and its nested Stage-1
`deceleration_stabilization` condition. This work item is a semantic-reference experiment, not a new
resource search and not candidate scoring.

The resource ceiling is immutable:

- 187 admitted rows;
- 179 unique economic-identity clusters;
- eight repeated cross-era identity clusters, which remain dependent observations;
- seven Era × Board cells with 24 rows;
- early STAR (`STAR_PRE_CHINEXT_20PCT/STAR`) with 19 rows as a capacity-limited semantic stress/audit
  stratum;
- no replacement, additional admission read, new year, dropped/merged board, or P025 resource stage.

## Packet boundary

Packet preparation attempts every one of the 187 frozen rows. It may reread the exact T-only market
window only to reconstruct the already accepted Trend Evolution Fact Family. Raw OHLC is neither
published nor retained. Reviewers receive an opaque case id, knowledge cutoff, Era × Board context,
continuous Trend Evolution facts and explicit fact-availability reasons.

Reviewers do **not** receive security identity, identity-cluster membership, candidate output,
thresholds, future rows, preserved partitions or economic outcomes. A packet that cannot be built is
retained as `UNAVAILABLE`; it does not trigger replacement.

## Blind review protocol

Two isolated same-origin development roles are used:

1. `short_term` — market-structure / trading interpretation;
2. `fund` — medium-horizon institutional / risk interpretation.

The same-origin limitation must remain explicit in every result. Each role sees only the frozen T-only
packet and cannot see the other role's opinion. Each role is sealed into a separate immutable ledger;
the combined resolver is unavailable until both role ledgers exist.

Each role answers Stage 12 first:

```text
TRUE | FALSE | UNKNOWN
```

Only when that role independently answers Stage12=`TRUE` may it assert the nested Stage-1 condition:

```text
TRUE | FALSE | UNKNOWN
```

Otherwise Stage 1 is `NOT_APPLICABLE`. Reasons are mandatory and bounded.

Resolution is deliberately conservative:

- Stage12 definite `TRUE` only when both roles say `TRUE`;
- Stage12 definite `FALSE` only when both roles say `FALSE`;
- every other Stage12 combination is `AMBIGUOUS`;
- resolved Stage12=`FALSE` makes Stage1 `NOT_APPLICABLE`;
- resolved Stage12=`AMBIGUOUS` makes Stage1 `AMBIGUOUS`;
- inside resolved Stage12=`TRUE`, Stage1 is definite only when both roles agree on `TRUE` or `FALSE`;
- unknown/disagreement is never silently converted to a negative;
- Stage12=`TRUE` + Stage1=`TRUE` is legal coexistence and is not a top-1 conflict.

## Frozen sufficiency gates

Reference sufficiency is decided on **economic-identity clusters**, not raw rows. Row-level truth
remains time-specific, so a repeated cross-era identity is not collapsed into one timeless label. A
cluster may contain both a positive and a negative row at different knowledge cutoffs; it still counts
only once in the overall independent-cluster denominator. Row counts remain coverage/descriptive
diagnostics. The gate reuses the established second-group development floor of
20 definite positives, 20 definite negatives and at most 15% unavailable/unjudgeable evidence rather
than choosing new thresholds after seeing Stage1/12 opinions.

Stage 12 requires:

- at least 80 clusters with definite TRUE/FALSE resolution;
- at least 20 definite-positive clusters;
- at least 20 definite-negative clusters;
- unavailable/unjudgeable cluster rate no greater than 15%;
- at least one definite Stage12 reference represented in every one of the eight Era × Board cells.

Stage 1 requires, within resolved Stage12=`TRUE` context:

- at least 40 applicable definite clusters;
- at least 20 definite-positive clusters;
- at least 20 definite-negative clusters;
- unavailable/unjudgeable rate among applicable context clusters no greater than 15%.

A gate failure closes P020-C as reference-insufficient. It does not reopen sampling/admission, lower a
gate, add opinions selectively, or open protected years. A gate pass authorizes only a separately
frozen P020-D candidate-scoring preregistration.

## Work sequence

| Step | Work | Exit |
| --- | --- | --- |
| Contract | Freeze exact P024 binding, packet visibility, reviewer protocol, cluster denominators and sufficiency gates | closed before semantic packet read |
| Prepare | Implement T-only Trend Evolution packet builder and deep verifier | closed engineering acceptance; exact implementation pushed |
| Packet run | Execute exactly one packet-preparation Job over the immutable 187-row panel | closed; 187/187 packets and 179/179 clusters available, zero opinions in artifact |
| Review | Collect isolated blind opinions for every available packet | ready; reviewer-only export deep-verifies the frozen packet artifact and exposes no case-map identity |
| Seal | Validate separate immutable role ledgers, then resolve rows and cluster-aware sufficiency | ready under additive pre-opinion seal/aggregation contract |
| Evaluate | Apply the preregistered reference-sufficiency gates | blocked until ledger seal |
| Closeout | Four-party bounded review and gate any P020-D successor | blocked until evaluation |

## Boundaries

P020-C does not authorize candidate/threshold search, Stage primary compression, LabelCard publication,
preserved 2021, 2022-2024, 2025, daily/strategy output or trading. It does not estimate market
prevalence and does not claim the early-STAR 19-row stratum is precision-equivalent to a 24-row cell.
Any later uncertainty or resampling must use economic-identity cluster as the dependence unit.
