# Continuous Structure Facts v1 proposal

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open; Real-DWD qualification pending |
| Authority | Owner-approved fact-product design; development qualification only |
| Governing work item | `P3-STRUCT-003` |
| Predecessor | `P3-STRUCT-002` complete-P2 economic comparison |
| Data boundary | DWD -> thin Structure Research ADM |
| Execution authority | none |

## 1. Why this is the next proposal

`P3-STRUCT-001` proved the real DWD -> Structure ADM -> expert -> aggregator -> P2 engineering
chain. `P3-STRUCT-002` then compared the same structural information in continuous versus coarse
representations on the accepted formal-strategy development population. The corrected evidence
shows the coarse representation is economically dominated by the continuous representation under
the frozen comparison, while neither structure-assisted variant establishes forward or production
value.

The next milestone should therefore improve the **auditable structural fact layer**, not expand the
number of discrete states.

The proposed principle is:

> Publish causal anchors, episode memory, signed distances, and confirmation progress as immutable
> facts. Let experts choose how to map those facts into scores or decisions. Keep coarse states as a
> derived display/audit vocabulary rather than the primary computational representation.

## 2. Scope

Continuous Structure Facts v1 is a fact-product milestone only. It does not introduce a new formal
strategy, a new expert, model training, Shadow, paper, live execution, generic DWS, or ADS.

The durable path remains:

```text
DWD
  -> Structure Research ADM
  -> Continuous Structure Facts v1
  -> consumer-owned scoring/policy mapping (separately gated)
  -> existing expert / aggregator / P2
```

The milestone should reuse the accepted Direct-DWD provider, PIT decision view, immutable artifact
identity, missing-data reset semantics, and Event Worker infrastructure already proven by
`P3-STRUCT-001` and `P3-STRUCT-002`.

## 3. Proposed fact contract

The first version should publish the smallest fact set needed to explain support/resistance and one
causal breakout episode without forcing an investment interpretation.

### 3.1 Rolling anchor facts

For each security-date with sufficient valid history:

- `rolling_resistance20`: maximum high over the preceding 20 valid sessions, excluding T;
- `rolling_support20`: minimum low over the preceding 20 valid sessions, excluding T;
- `resistance_margin`: `close / rolling_resistance20 - 1`;
- `support_margin`: `close / rolling_support20 - 1`;
- `volume_ratio20`: T volume divided by preceding-20-session mean volume when available;
- source-window start/end dates and exact T-close `knowledge_cutoff`.

These are facts, not trading thresholds. A positive resistance margin does not by itself authorize
an entry; a negative support margin does not by itself authorize a sale.

### 3.2 Breakout episode facts

An episode is a causal memory container, not a market-state class.

The proposal removes the MVP's volume threshold from **episode creation**. A breakout episode starts
when T close is strictly above the preceding rolling resistance. The current volume ratio is
published separately so a consumer can decide whether volume confirmation matters.

At episode creation, freeze:

- `episode_id`;
- `attempt_at`;
- `attempt_snapshot_id`;
- `breakout_level` = the preceding rolling resistance observed at the attempt;
- `episode_support_level` = the preceding rolling support observed at the attempt;
- `attempt_volume_ratio20`;
- anchor source-window identity.

For every later T-close while the episode remains active, publish:

- `breakout_margin` = `close / breakout_level - 1`;
- `episode_support_margin` = `close / episode_support_level - 1`;
- `post_attempt_hold_count` = number of completed later closes at or above the frozen breakout level;
- `episode_age_sessions`;
- current `volume_ratio20`;
- `episode_status` limited to structural lifecycle bookkeeping such as `active` or `ended`, not an
  investment state.

If a later close falls below the frozen breakout level, the episode ends. A future breakout creates
a new episode with a new immutable identity. The old episode must never be rewritten with new
anchors.

### 3.3 Missing-data boundary

A missing, invalid, or PIT-inadmissible observation:

- sets an explicit availability/failure reason;
- ends or resets sequential history where causal continuity cannot be proven;
- blocks new derived episode claims until sufficient valid history is rebuilt; and
- never implies support damage, bearish structure, forced reduction, or forced sale.

## 4. What v1 deliberately does not own

Continuous Structure Facts v1 should not publish or own:

- entry/exit scores;
- portfolio allocation priority;
- `support_threatened` or `breakout_accepted` as authoritative computational states;
- ATR or volatility-normalized thresholds unless separately justified;
- thirteen-state labels;
- ML features selected from result-driven search;
- current-position context;
- execution/tradability authority; or
- economic acceptance claims.

A coarse state renderer may remain as a deterministic read-only projection over these facts for
human explanation, but it must be replaceable without changing the underlying fact identity.

## 5. Identity and auditability requirements

Each published fact row should bind:

- security code and governed `dwd_security_id`;
- market date and timezone-aware knowledge cutoff;
- DWD provider/content identity;
- Structure ADM policy/version;
- availability/reset reason;
- rolling-anchor source interval;
- active `episode_id` when applicable; and
- an immutable row digest.

The package manifest should bind the exact upstream identity, policy digest, security/date scope,
row count, content digest, and code revision. An independently rebuilt package under the same
semantic revision must reproduce byte/semantic identities according to the acceptance contract.

The accepted MVP follow-up about row-local explainability becomes a hard requirement here: an
active/confirmed episode row must carry its own `breakout_level` and episode identity. A reviewer
must not need to replay prior rows merely to learn which anchor the current row refers to.

## 6. Proposed implementation boundary

Reuse `src/quanthus/structure_intelligence.py` only where its causal mechanics remain valid, but do
not promote the six MVP coarse states into the v1 fact schema.

A likely implementation split is:

- `structure_facts.py`: immutable fact/episode contracts and deterministic sequential builder;
- existing Structure Research ADM artifact path: upstream application boundary;
- read-only coarse-state renderer: compatibility/explanation projection only;
- Event Worker: same `quanthus.structure-mvp.v1` family or a separately approved typed config under
  that family, never arbitrary commands.

No generic reusable DSL is required for v1.

## 7. Proposed acceptance plan

The milestone should close only after all of the following, in order:

1. **Contract/golden semantics**
   - prior-20 anchors exclude T;
   - episode anchors freeze at creation;
   - no future rewrite of earlier facts;
   - a failed hold ends the old episode rather than moving its breakout level;
   - gaps reset continuity;
   - row-local episode explanation is complete.

2. **Synthetic adversarial cases**
   - one-day false breakout;
   - repeated breakout attempts;
   - support breach after breakout;
   - suspension/missing-bar gap;
   - corporate-action-adjacent daily observations under existing DWD/PIT semantics.

3. **Bounded real-DWD A/B build**
   - development partition only;
   - one frozen scope and deterministic security selection;
   - independent rebuild with identical fact/episode digests;
   - no result-driven threshold changes.

4. **Consumer compatibility proof**
   - one read-only adapter demonstrates an expert can consume the continuous facts through the
     existing expert/aggregator contract;
   - no complete-P2 economic claim is required to accept the fact product itself.

5. **Separate value gate later**
   - only after the fact contract is accepted may a new preregistered consumer/scoring experiment
     ask whether the richer facts improve the formal strategy or another expert.

## 8. Stop conditions

Stop or redesign before implementation if:

- the episode contract requires hindsight to identify anchors;
- the fact schema duplicates already-authoritative DWD facts without adding causal application
  semantics;
- the proposed fields require hidden position/strategy state;
- missing-data behavior cannot remain fail-closed; or
- the product expands into a generic DWS/state engine before a concrete consumer justifies it.

## 9. Owner decision and implementation authority

The owner approved construction after reviewing the milestone effect and explicitly accepted all
three architecture decisions:

1. `P3-STRUCT-003` is the next Structure Intelligence engineering milestone;
2. breakout episode creation uses **price-only crossing**, while volume is published as an
   independent fact and never hidden inside episode existence; and
3. coarse states remain only a deterministic read-only explanation projection over continuous
   facts, not the primary computational representation.

Implementation and bounded production qualification are authorized. `P3-STRUCT-002` subsequently
closed after its owner-authorized same-revision A/B reproduction accepted
`prefer_continuous_structure_facts`, so this milestone may proceed to its fixed Real-DWD A/B and
outcome-blind review. This approval does not authorize thirteen-state expansion, model training,
Shadow, paper, or live execution.
