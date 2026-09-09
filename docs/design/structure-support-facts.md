# 演化支撑事实族 / Support Evolution Fact Family

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Governing work item | `P3-STRUCT-005` |
| Formal business name | **演化支撑事实族 / Support Evolution Fact Family** |
| Historical planning code | `F9`; retained only for traceability in historical plans, config/artifact names and accepted evidence |
| Parent baseline | `structure-facts-v2.md`; accepted B1-B5 remains immutable |
| Trigger | P3-STAGE-001-H mixed closeout and `PASS_WITH_SCOPE_REDUCTION` review |
| Runtime request | `../../config/structure-f9-run.development.v1.json` |
| Semantic catalog | `../../config/structure-semantic-catalog.v1.json` |
| Code | `../../src/quanthus/structure_support_facts.py`; `../../src/quanthus/structure_f9_run.py` |
| Acceptance | `../acceptance/structure-f9-real-dwd-acceptance-20260910.md` |
| Claim ceiling | continuous Support Evolution Facts plus research-only Stage-10/11 projection; no official label or trading authority |

## 1. Naming and dimensional model

The formal domain name is **演化支撑事实族 / Support Evolution Fact Family**. `F9` is only the
historical planning code used by the earlier stock-stage capability matrix and is not the long-term
business name. Existing technical identifiers such as `structure-f9-*`, `B5-F9`, accepted artifact
schemas and historical evidence keep their names for reproducibility; new explanatory prose should
prefer the formal name and may add `(historical F9)` when traceability matters.

A **fact family** is a semantic grouping of related facts about one support relationship evolving
over time. It is not one Fact row, not a Stage label, and not a peer of an individual measurement.
The dimensional structure is:

```text
演化支撑事实族 / Support Evolution Fact Family
├─ 对象 / 身份维度
│  └─ SupportBinding：breakout episode 绑定的 exact frozen rolling-low support reference
├─ 事件事实维度
│  ├─ same-support break / close-cross-below
│  └─ same-support reclaim / close-reclaim
├─ 过程状态维度
│  ├─ phase：intact / unreclaimed / reclaimed / censored
│  ├─ break_at / reclaim_at
│  └─ unreclaimed_age_sessions 与前序 break/reclaim references
└─ 连续度量维度
   ├─ signed distance to the same frozen support
   ├─ post-break running low
   └─ post-break rebound while unreclaimed
```

Therefore `SupportBinding`, break/reclaim events, process state and continuous measurements are not
four alternative labels. They are different dimensions/record types inside the same fact family.
Stage 10/11 sit **above** this family as versioned interpretations/projections.

## 2. Decision

Route A promotes the continuous evidence underneath Stage 10/11 before any further stage expansion.
This is a **narrow extension of B3/B4/B5**, not authorization for B6 geometry/compression, B7
activity, B10 economic assessment, B11 ML/forward execution, or the parked Stage 5/6/7 definitions.

The formal facts answer only:

- which immutable rolling-low reference was captured at the breakout start as the declared support
  candidate;
- whether close crossed below that same support and when;
- how many uninterrupted admitted exchange sessions the support has remained unreclaimed;
- current signed distance to the same support;
- whether and when the same support was reclaimed;
- the post-break running low and current rebound from that low while the support remains unreclaimed.

The word `support` is a **role binding**, not a claim that the rolling low is universally strong or
predictive. Missing data, listing/coordinate change or calendar discontinuity censors continuity and
never fabricates structural failure.

## 3. Formal object boundary

The extension reuses accepted B3/B4 objects wherever possible.

| Object | Formal meaning |
| --- | --- |
| B3 `structure.anchor.rolling-low` | Exact prior-window low anchor captured at breakout start |
| `SupportBinding` | Binds one breakout episode to its exact sibling rolling-low anchor under role `breakout-start-rolling-low-candidate` |
| B4 `structure.interaction.close-cross-below` | First admitted close crossing below that exact frozen support |
| B4 `structure.interaction.close-reclaim` | Later admitted close reclaim of the exact same support and prior-loss reference |
| B3 `structure.geometry.signed-distance` | Current close / frozen support - 1 |
| `structure.geometry.post-break-rebound` | Current close / causal post-break running low - 1 while still unreclaimed |
| `structure.episode.support-failure-sequence` | Append-only intact → unreclaimed → reclaimed/censored process observations |

`unreclaimed_age_sessions=0` on the break session. Later ages count subsequent uninterrupted
admitted exchange sessions. Reclaim is terminal for the first bounded sequence. The reclaim session
is excluded from unreclaimed-rebound measurement.

The first delivery uses a 60-session process horizon because that is the evidence horizon under
which Stage 10/11 were learned. The horizon belongs to the EvaluationSpec and may be versioned later;
it is not a universal market truth.

## 4. Stage 10/11 remain projections

The Stage-10/11 development closeout is preserved as a **research projection** over the Support Evolution Fact Family (historical F9):

```text
Stage 10 confirmation candidate:
  prior same-support break
  + unreclaimed_age_sessions >= 5
  + signed_distance_to_support <= -2%

Stage 11 candidate:
  prior immutable Stage-10 confirmation ref exists
  + same support remains unreclaimed
  + post-break rebound >= 5%
```

A crucial rule is that Stage 11 consumes the **prior** Stage-10 confirmation reference. It does not
require the current observation to remain 2% below support. Example: a process may confirm Stage 10
at -5%, rebound to -1% without reclaiming support, and legitimately satisfy Stage 11. Reclaim ends
the current Stage-10/11 projection but never rewrites the earlier valid confirmation.

Projection thresholds are not embedded into Fact identities. Changing projection thresholds creates
a new projection version, not new historical support Facts.

## 5. PIT, identity and failure semantics

- All measurements are T-only and use the exact admitted row at T.
- The support target is the exact B3 sibling rolling-low anchor from the breakout formation window,
  not a later moving low or a same-valued substitute.
- Source corrections create new revisions under existing v2 rules; wall-clock build/use times do not
  enter Fact identity.
- Crossing/reclaim use final daily close only. Daily OHLC cannot infer intraday touch order.
- A missing/nonadmitted row, coordinate/listing change or hidden calendar gap censors the process.
- Censoring is never equivalent to Stage 10, bearishness, a sell action, or a failed reclaim.
- Rebound measurement exists only while the support remains unreclaimed; reclaim-day rebound is not
  published as Stage-11 evidence.

## 6. Bounded real-DWD qualification

The first qualification deliberately reuses the accepted B2-B5 development lane:

```text
scan:   2019-10-08 → 2020-03-31
report: 2020-01-02 → 2020-03-31
stocks: 000001 / 300750 / 600519
scales: 20 / 60
view:   retrospective
jobs:   exactly two independent cold result Jobs
```

Both Jobs must rebuild Direct-DWD and all prerequisites independently. Cross-Job cache reuse is
zero. The deep verifier reconstructs source admission, base Structure v2, the Support Evolution
Facts (historical F9) and the research projection from published source rows. Required semantic digests and deterministic review
sample must match between the two Jobs.

The bounded sample may contain zero Stage-10/11 confirmations; that is not an engineering failure if
the formal facts and reconstruction are valid. This slice qualifies fact semantics and reproducible
construction, not frequency, profitability or external validity.

## 7. Acceptance and exit criteria

P3-STRUCT-005 passed the bounded Route-A real-DWD qualification at revision
`44571610b1da1792a80bbd45ee7f180d59e8495a`: two independent cold Jobs reproduced the exact
input-readiness, base Structure, Support Evolution Facts (historical F9), projection and deterministic review sample identities.
The accepted evidence is `../acceptance/structure-f9-real-dwd-acceptance-20260910.md`.

The acceptance criteria were:

1. semantic catalog and blueprint expose the new concepts without changing accepted B1-B5 identities;
2. synthetic counterexamples pass: same-target identity, prefix stability, censoring, reclaim and
   prior-10-ref Stage 11 behavior;
3. Event Worker admits only the fixed historical-F9 run config and uses the deep verifier;
4. two same-revision cold real-DWD Jobs succeed and reproduce base Structure, Support Evolution Facts, projection,
   input-readiness and deterministic review-sample identities;
5. an outcome-blind review finds no contradiction in the preregistered sample;
6. no B6/B7, official Stage label, economic, daily, strategy, paper/live or sealed-partition authority
   is opened.

After this milestone, Stage 10/11 may be implemented later as projections consuming Support Evolution Facts under a
separate label/product scope. Stage 5/6/7 remain `INCONCLUSIVE_PARKED`.
