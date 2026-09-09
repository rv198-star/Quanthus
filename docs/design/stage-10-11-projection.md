# Stage 10/11 Projection Contract

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Work item | `P3-STAGE-002` |
| Upstream | `P3-STRUCT-005`; `P3-STAGE-001-H` mixed closeout |
| Source Facts | **演化支撑事实族 / Support Evolution Fact Family** (historical `F9`) |
| Contract | `../../config/stage-10-11-projection.v1.json` |
| Qualification | `../../config/stage-10-11-projection-qualification.v1.json` |
| Runtime code | `../../src/quanthus/stage_projection.py`; `../../src/quanthus/stage_projection_qualification.py` |
| Qualification | `../acceptance/stage-10-11-projection-qualification-20260910.md` |
| Claim ceiling | accepted formal development Projection Contract; no primary stage, official LabelCard, daily, strategy or trading authority |

## 1. Decision

Route A has already formalized the continuous facts underneath Stage 10/11. The next milestone does
not retune thresholds and does not read market data again. It formalizes how those facts are projected
into two **related propositions**.

The key modeling correction is that Stage 10 and Stage 11 do not have to be represented as two
mutually exclusive booleans at the evidence layer:

- **Stage 10 / 结构失效** is a persistent structural-failure **context proposition** after the first
  same-support observation satisfies the frozen 5-session + 2% confirmation rule. The immutable
  confirmation remains historical evidence while that support stays unreclaimed.
- **Stage 11 / 反抽** is a nested **current rebound proposition** inside an already confirmed Stage-10
  context. It requires the prior Stage-10 confirmation, the same support still unreclaimed, and the
  frozen 5% post-break rebound condition.

Therefore a projection observation may intentionally contain candidate set `{10, 11}`. This is not
publication of two primary stages. `primary_stage` remains null in this milestone. A later,
separately reviewed compression/arbitration policy may decide how a UI or downstream system maps
related propositions into one displayed stage.

This distinction avoids a false dilemma: forcing Stage 11 to erase Stage-10 context loses the causal
failure evidence, while forcing the two propositions to be mutually exclusive at the evidence layer
requires an unapproved transition policy.

## 2. Frozen projection semantics

### 2.1 Stage 10 confirmation and context

Stage 10 confirms once per Support Evolution process on the first T-only observation where:

```text
same frozen support is unreclaimed
AND unreclaimed_age_sessions >= 5
AND signed_distance_to_support <= -2%
```

The formal confirmation reference binds the projection contract/version, process identity,
confirmation business date, exact Support Evolution process revision and exact support-distance
revision. A later source correction therefore creates a different evidence revision instead of
silently preserving a stale confirmation identity.

After confirmation, the Stage-10 context remains active while that exact support remains unreclaimed.
Current distance does **not** have to remain below -2%. Reclaim or censoring clears the current
context but never rewrites the earlier confirmation.

### 2.2 Stage 11 nested rebound proposition

Stage 11 is true at T only when:

```text
prior immutable Stage-10 confirmation exists
AND same support remains unreclaimed
AND post_break_rebound >= 5%
```

It deliberately does **not** re-prove the current Stage-10 depth condition. The accepted real-DWD F9
sample already contains a case where the current support distance recovered to about -1.44% while
rebound exceeded 7%; Stage 11 remained valid because the prior structural-failure confirmation still
existed and support had not been reclaimed.

The current Stage-11 rebound proposition may later become false if rebound falls below the threshold.
That fact alone is **not** interpreted as a primary `11 -> 10` transition. Primary-stage lifecycle,
rebound termination and Stage-12 routing remain future scope.

## 3. Output contract

Each `StageProjectionObservation` binds:

- Support Evolution process and exact process observation revision;
- market date and scale;
- projection contract digest;
- support-distance and rebound source revisions when applicable;
- immutable formal Stage-10 confirmation reference, if one exists;
- `stage_10_context_active`;
- `stage_11_rebound_condition_active`;
- `candidate_stages`: `()`, `(10,)`, or `(10,11)`;
- `primary_stage = null`;
- a deterministic reason code.

The projection contract is downstream of Facts. Threshold/version changes alter the projection
contract and projection identities, not Support Evolution Fact identities.

## 4. Qualification without rereading market data

`P3-STAGE-002-C` uses exactly the two accepted `P3-STRUCT-005` cold artifacts:

```text
run_237a3e986b49490381f380a7ed5d4d14
run_f371d3f2cb0440aea2c49ceaad1c237b
```

The qualification Job must:

1. pass each source artifact through the original deep F9 verifier;
2. reconstruct the Support Evolution Facts from the artifact's published source rows;
3. apply the new formal projection contract;
4. compare every new Stage-10 context predicate with retained `stage_10_active`;
5. compare every new Stage-11 rebound predicate with retained `stage_11_active`;
6. require byte-equivalent formal projection output across the two cold source runs;
7. require every `primary_stage` to remain null;
8. report zero market-data reads and zero DWD-provider opens.

No new source partition, DWD view, future outcome, 2022-2024 arbitration or 2025 permanent blind is
read by this qualification.

## 5. What this milestone proves and does not prove

The immutable-artifact qualification passed at revision
`1cccd0ac230bcb39d89b4514fd08c8dfed639215` in Job
`job_5e2d82adb2a8492488725fcda379424f`. Both accepted F9 source artifacts produced the same formal
projection digest, all retained Stage-10/11 predicates matched, and `primary_stage` was null for all
241 observations. The deep verifier enforced zero market-data reads and zero DWD-provider opens.
Evidence is `../acceptance/stage-10-11-projection-qualification-20260910.md`.

This PASS proves that Stage 10/11 have a stable, versioned, reproducible projection contract over the
accepted Support Evolution Fact Family, and that formalization does not change the predicates already
observed in the accepted F9 run.

It does **not** prove:

- HUMAN_EXPERT_REFERENCE agreement;
- external/forward validity of 5 sessions, 2% or 5%;
- one correct `primary_stage` compression policy;
- QUALIFIED/ACTIVE LabelCard status;
- daily/as-known inference readiness;
- strategy, portfolio, notification, paper or live trading value.

Those are separate future milestones. Stage 5/6/7 remain `INCONCLUSIVE_PARKED` throughout this work.
