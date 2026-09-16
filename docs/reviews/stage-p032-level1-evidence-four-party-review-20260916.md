# P3-STAGE-032 Level-1 historical realtime evidence — four-party phase review

| Field | Value |
| --- | --- |
| Date | 2026-09-16 |
| Work item | `P3-STAGE-032` |
| Frozen result | `NO_PASS_LEVEL1_HISTORICAL_DISSEMINATION_EVIDENCE_INSUFFICIENT` |
| Machine evidence | `../../config/stage-learning/stage-level1-historical-realtime-evidence-feasibility-result.v1.json` |
| Review mode | four independent GPT-6 Astra read-only role sessions; no tools/files inside review sessions |
| Phase decision | 4/4 stop the current historical Stage PIT source-evidence search; preserve P029 as retrospective development-shadow semantics |

## Frozen evidence reviewed

P032 was the last preregistered bounded historical-source route after P030 and P031. It evaluated only:

1. SSE official Level-1 historical intraday snapshot evidence plus contemporaneous Level-1
   dissemination semantics;
2. SZSE official Level-1 realtime dissemination plus official historical-retention evidence.

Probe dates were fixed before evidence collection at `2019-10-08`, `2020-01-02`, and `2020-03-31`.
SSE security was `600519`; SZSE securities were `000001` and `300750`. No Stage predicate, source
integration, daily-bar aggregation, commercial source, purchase/contact, bulk download, 2022-2024
arbitration, 2025 blind data, prediction or economics were opened.

The source-evidence result was NO_PASS:

- SSE Level-1 historical snapshots cover the target period, but the official historical product is a
  3/5-second sampled archive, retains only the first of adjacent identical snapshots, and historical
  data is described as cleaned. Public evidence does not bind today's retained historical rows to exact
  originally disseminated realtime messages or a complete correction/replacement family.
- SZSE historical STEP schema lineage and realtime sessions are documented. However snapshot data is
  periodically published and cannot be retransmitted, while `OrigTime` is defined as data generation
  time rather than official dissemination time. Public evidence does not bind an official historical
  retained snapshot to the exact originally disseminated version plus complete correction lineage.
- Product-level exact-version/dissemination gates therefore fail before current historical values
  could add evidence. All fixed dates remain `NOT_EVALUABLE_AT_EXACT_VERSION_LEVEL`.

## Short-term / event-driven review

The reviewer accepted P032 NO_PASS as an inability to prove what was actually visible at the historic
cutoff, not as evidence that no realtime message existed or that Stage semantics failed.

**Selected route: A — stop the current Stage PIT source-evidence hunt.** Continuing to rotate source
families has low marginal value for short-term decision research and increasing post-hoc freedom.

Stage10/11 should remain a retrospective development-shadow semantic asset for historical case
organization, explanation and research debugging. The reviewer recommended returning to real decision
workflow value: whether Quanthus helps an analyst surface important events, inspect evidence and reduce
unproductive tracking. Future observations should capture version/receipt evidence prospectively.

## Fund / portfolio-risk review

The reviewer accepted P032 NO_PASS because unknown historical versions make it impossible to quantify
how much later correction leaked into the reconstructed risk state.

**Selected route: A.** P030-P032 reveal one stable evidence bottleneck; a commercial archive search is
a separate costly infrastructure project without enough demonstrated portfolio value to justify it now.
Weakening the PIT clock would answer a different question.

Stage10/11 remain retrospective development-shadow only. The reviewer suggested that the next product
value work test incremental risk-decision workflow quality under auditable inputs rather than spending
more time reconstructing unprovable 2020 receipts.

## Quant research review

The reviewer accepted P032 NO_PASS as strict PIT identifiability not established. `NOT_EVALUABLE` must
not be counted as a strategy failure or success.

**Selected route: A.** Repeated source substitution creates increasing post-hoc selection risk. B would
be a new procurement/archive research program; C would alter the confirmatory hypothesis.

Stage10/11 remain untested under strict public reconstruction and useful only in their retrospective
research/shadow scope. The reviewer recommends prospective auditable data retention plus preregistered
shadow experiments with fixed baselines, costs, latency and evaluation windows so future confirmation
comes from data captured with actual time/version evidence rather than source archaeology.

## Data-governance / warehouse review

The reviewer accepted P032 NO_PASS and located the remaining gap at product-level lineage/retention,
not at sample size. More dates cannot prove an absent relationship between retained history and
original dissemination.

**Selected route: A.** Freeze P029's retrospective layered representation as a governed development
asset and end the current Stage PIT source search. Historical receipt/version capture becomes a generic
future data-infrastructure capability, triggered by reusable platform demand rather than a Stage rescue
project.

The next platform value should be a reproducible, auditable research loop: hypothesis -> immutable data
version -> transformation lineage -> experiment -> review, with time-leakage checks and clear claim
ceilings.

## Consensus phase decision

All four independent roles selected **A**:

> close P032 NO_PASS and stop the current Stage historical PIT source-evidence hunt.

No role selected a commercial/third-party historical receipt archive search and no role selected a
weaker PIT proxy.

This decision does **not** close Stage10/11 as failed. It freezes their current evidence state:

- accepted development projection semantics;
- real retrospective P029 representation/consumer result;
- strict historical PUBLIC_RECONSTRUCTION not evaluated because admissible source-version/publication
  evidence could not be established;
- `primary_stage=null` and development-shadow authority remain unchanged.

## Recommended successor decision space

The reviews converge on returning to Quanthus value rather than continuing source search. Two related
next-value questions should be evaluated before implementation chooses one:

1. **consumer utility:** does layered context+condition representation measurably improve expert/model
   interpretation, consistency or review cost relative to Facts-only under identical evidence?
2. **prospective auditable shadow:** can Quanthus begin collecting immutable receipt/version/time
   evidence now and validate future expert/decision workflow value without reconstructing unavailable
   2020 receipts?

These are not automatically opened by P032. A separate route review should choose a single bounded
phase target and preregister its acceptance criteria.

## Future strict-PIT reopen gate

A future strict-PIT review may be proposed only after independently obtained evidence exists before
Stage results are inspected. At minimum it must bind:

- exact original message/snapshot or equivalent immutable source version identity;
- verifiable dissemination or receipt time sufficient for the frozen decision cutoff, with clock and
  precision semantics;
- complete correction/replacement/withdrawal and missing-message lineage or an equivalent proven
  immutability/completeness mechanism;
- auditable source/custody history and a finite reconstruction test.

Future receipts collected by Quanthus support only the period after collection begins; they cannot
retroactively establish 2020 public availability.

## Red lines

- no session-close, generation time, ingestion time or final cleaned history may be relabeled as strict
  historical availability;
- retrospective P029 success remains retrospective and cannot be promoted to PIT;
- P032 NO_PASS is not a Stage10/11 semantic or trading failure;
- no commercial-source procurement/search is implicitly authorized;
- any future PIT restart requires a new preregistered phase and independent evidence-admission review.
