# P3-STAGE-026 — High-Side Modifier Proposition Route

Design status: approved semantic/resource preregistration
Delivery gate: first P026-C Job failed before any result-bearing read; one exact P026-C-R1 technical recovery authorized

Parent: `stage-third-group-portfolio-rebaseline.md`
Predecessor closeout: `stage-downside-lifecycle-proposition.md`
Machine concept: `../../config/stage-learning/stage-high-side-modifier-concept.v1.json`
Resource/reference contract: `../../config/stage-learning/stage-high-side-modifier-blind-reference.v1.json`
P026-B pre-result review: `../reviews/stage-p026b-resource-contract-four-party-review-20260916.md`
P026-C engineering acceptance: `../acceptance/stage-high-side-modifier-reference-engineering-20260916.md`
P026-C-R1 recovery review: `../reviews/stage-p026c-pre-read-technical-recovery-review-20260916.md`
Fact substrate: `../design/structure-trend-evolution-facts.md`
Historical evidence: `../reviews/stock-stage-third-group-profile-result-review-20260910.md`

## Goal

Resume the P019 third-group portfolio after P020-C closes without reference qualification. P026 does
not turn codes 8 and 9 back into peer primary stages. It tests the previously accepted dimensional
reclassification: observable **high-side modifiers** over an explicit advancing context.

The route begins with Stage8 only because its required price-structure substrate already exists. Stage9
remains traceable but blocked until a separately justified high-side activity relation exists.

## Frozen semantic architecture

### Advancing context

`advancing_context` is an auxiliary context proposition, not a new Stage code. It means current as-of
Trend Evolution evidence supports persistent upside direction/context. Being above a past low or near
a rolling high is not enough by itself.

### Stage8 modifier

Stage8 working meaning is `upside_acceleration_extension`:

> Inside an established advancing context, current evidence supports strengthening recent upside
> velocity and/or observable high-side extension, without claiming an imminent top, exhaustion or
> future reversal.

It may use only accepted T-only Trend Evolution facts: multi-horizon return, directional efficiency,
return velocity, 5-vs-20 velocity delta, 60-session range position, drawdown and rebound. No single
range-position or return threshold is frozen here.

Stage8 is a modifier. It may coexist with a separately governed primary/context representation; it is
never a Top-1 primary-stage override by implication.

### Stage9 modifier

Stage9 working meaning remains `high_level_price_volume_divergence`:

> Inside a high-side advancing context, price evidence is slowing/weakening while a separately defined
> activity relation shows elevated or divergent participation.

The historical words `赶顶` and `派发` are not governing semantics. The first implies future-top
knowledge; the second implies actor/fund intent. Neither is admitted.

Stage9 cannot be constructed from price-only Trend Evolution facts. Its minimum missing prerequisite is
a narrow T-only activity relation bound jointly to high-side price context. Until that relation is
separately justified and qualified, Stage9 has no reference, threshold or candidate route.

## Fact-readiness audit

The accepted Trend Evolution Fact Family already supplies the complete Stage8 semantic substrate:

- 5/20/60 returns;
- 5/20 signed directional efficiency;
- 5/20 return velocity;
- 5-vs-20 velocity delta;
- 60-session range position;
- drawdown from rolling high;
- rebound from rolling low.

Therefore Stage8 may proceed to a separately frozen blind-reference resource design without creating a
new Fact family first.

Stage9 does **not** pass the same audit. P3-STAGE-003 explicitly lacked the joint high-position ×
slowing-price × elevated-activity relation. A future Stage9 prerequisite, if still justified after the
Stage8 route, must remain minimal: current/recent activity relative to a causal admitted baseline,
jointly bound to high-side price context, with no distribution/fund-flow intent.

## Frozen resource/reference contract

P026-B keeps resource machinery subordinate to the vocabulary question. It freezes one deterministic
resource/admission/packet route before any new market read.

Exact semantic-consumed identity-windows are excluded from P013, the P014 qualified semantic plan and
the P020 blind-reference case map. P015's 60 additional review rows must verify as a subset of the
already excluded P014 qualified plan. P014 L2A technical-admission-only exact windows remain reusable
with provenance because they never entered semantic/model/review consumption. Whole identities are not
burned globally: a disjoint later window may be used, but repeated identities remain a single economic
cluster.

Resource collection is capacity-limited rather than square-panel qualified:

- target 20 admission-ready rows in each of the eight cells, maximum 160 rows;
- hard floor 16 rows in every cell;
- hard floor 144 realized rows overall;
- hard floor 128 unique economic-identity clusters;
- at most 64 precommitted candidate identities per cell;
- at most two deterministically precommitted windows per identity;
- one selected row per identity inside a cell;
- cross-cell identity reuse only on non-overlapping exact windows and never as extra independent
  evidence;
- technical fallback only after 61-session input-readiness failure, in a pre-frozen order;
- no result-aware replacement or second resource route.

If any resource floor fails, P026 returns directly to four-party portfolio review under
`CLOSED_STAGE8_RESOURCE_CAPACITY_SHORTFALL_NO_REPAIR`. There is no automatic P027/P028 resource-repair
sequence.

If resource coverage passes, the same sole Job may build reviewer-visible T-only Trend Evolution
packets for the exact selected rows. Stage8 reference sufficiency is then judged independently on
identity clusters using the already established pre-opinion floors: context >=80 definite clusters
with >=20 positive and >=20 negative, Stage8 >=40 definite applicable clusters with >=20 positive and
>=20 negative, and <=15% unjudgeable rates. A resource pass therefore does not imply a semantic pass.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-026-A | Freeze high-side semantic architecture and fact-readiness split | P3-STAGE-020-C, P3-STAGE-003, P3-STRUCT-006 | closed preregistration; zero new market/result/opinion reads |
| P3-STAGE-026-B | Freeze one P020-disjoint exposure-aware Stage8 resource/reference contract | P3-STAGE-026-A | closed preregistration; one 20-target/16-floor per-cell route, >=144 rows / >=128 clusters, no Stage9 route |
| P3-STAGE-026-C | Prepare exact T-only Stage8 review packets and seal blind references | P3-STAGE-026-B | initial Job failed at pre-read semantic-contract field mapping with no artifact/result read; one separately frozen P026-C-R1 technical recovery authorized |
| P3-STAGE-026-D | Gate any finite Stage8 candidate topology | P3-STAGE-026-C | blocked; opens only if blind reference sufficiency passes |
| P3-STAGE-026-E | Four-party portfolio closeout and decide whether Stage9 activity work remains justified | P3-STAGE-026-D or earlier fail-close | blocked; no protected/strategy/trading implication |

## Boundaries

The first accepted-runtime Job failed at the initial semantic-contract validation before candidate
reconstruction or any market/admission read. Its failed budget remains immutable. P026-C-R1 separately
authorizes one exact-revision technical recovery limited to the frozen P026-A field mapping; it does
not create another resource route or change any P026-B gate. It does not authorize a second resource route, Stage8
candidate or threshold search, Stage9 activity implementation/opinion, primary-stage compression,
LabelCard, preserved 2021, 2022-2024, 2025, daily/strategy output or trading.
