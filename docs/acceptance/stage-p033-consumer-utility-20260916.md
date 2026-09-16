# P3-STAGE-033 consumer-utility acceptance

| Field | Value |
| --- | --- |
| Date | 2026-09-16 |
| Work item | `P3-STAGE-033` |
| Result | `INCONCLUSIVE_CONSUMER_UTILITY_ATTRIBUTION_MECHANICAL_GATE_PASS` |
| Mechanical gate | `PASS_CONSUMER_UTILITY_INCREMENT` |
| Population | exact P029 10-observation retrospective population |
| Evaluations | 40; 20 Facts-only / 20 Facts+Layered |
| New market / DWD reads | 0 / 0 |
| Protected partitions | not accessed |
| Production / strategy / trading authority | none |

## Acceptance statement

P033 successfully executed its frozen bounded consumer experiment. The current Facts+Layered packet
materially outperformed the current Facts-only packet on the preregistered canonical Q1-Q4 task:
12/20 Facts-only evaluations had at least one critical error versus 0/20 Layered evaluations. The
Layered arm also met the preregistered elapsed-time ceiling under both the normal and conservative
fund-role interruption accounting.

The result does **not** close the broader causal question. Post-result four-role review found that the
Facts-only packet did not establish information equivalence with the Layered packet: current
Support Evolution facts and semantic definitions may be insufficient to uniquely reconstruct prior
Stage10 confirmation and state history, while the Layered packet directly exposed the derived state.
Therefore P033 is accepted as a correctly executed, informative but attribution-inconclusive phase.

## Runtime evidence

Packet preparation technical recovery:

```text
initial job: job_de97ed388c6e4971995fddd34ac7ba5e
initial disposition: failed before packet publication
recovery revision: a09c16ca343b7afb195d6e7215fdad4438f3d8bb
recovery job: job_e35b2be0d93f4c4f9e928c2cfb409942
recovery state: succeeded
packets: 40
Facts-only: 20
Facts+Layered: 20
market reads: 0
DWD provider opens: 0
strict PIT claim: false
```

The first Job failed because the runtime image omitted repository documents already bound by exact
SHA in the preregistration. One pre-result technical recovery changed only image packaging. It did
not change the population, arms, assignment, questions, metrics, gates, model contract or claim
ceiling.

Four isolated evaluator roles used one exact model/provider identity. One fund-role outer host timeout
was recovered on the same thread using the append-only session record. Three packets had completed,
the fourth had entered context but had not completed, and later packets had not been exposed. No
completed packet was replayed and no alternative evaluator was used.

## Mechanical metrics

```text
Facts-only critical-error packets: 12 / 20
Layered critical-error packets:     0 / 20
critical-error reduction:          12
Facts-only all-four-correct:        8 / 20
Layered all-four-correct:          20 / 20
Facts-only answer-level ABSTAIN:   28
Layered answer-level ABSTAIN:       0
per-role errors:                    3 -> 0 for all four roles
Layered/Facts median elapsed:       0.886 lower / 0.923 conservative
visible response-token ratio:      1.0
```

The frozen accuracy path passes under both elapsed bounds. The efficiency path is not used because
the response-token reduction threshold was not met.

## Claim ceiling

P033 may support only the bounded statement that, on this exact retrospective population and fixed
consumer task, the current Layered product packet produced substantially better canonical-answer
alignment than the current Facts-only packet without a measured elapsed-time penalty.

It does not prove:

- information-equivalent layered-expression causality;
- independent Stage recognition from sufficient Facts;
- prediction or economic value;
- strict PIT or historical as-known validity;
- out-of-sample generalization;
- production expert, strategy or trading authority.

## Closeout

Continuous Facts remain the authoritative substrate. The Layered Stage10/11 surface retains bounded
development communication value. No successor is automatically opened. Any information-equivalent
control experiment must be a new preregistered phase.
