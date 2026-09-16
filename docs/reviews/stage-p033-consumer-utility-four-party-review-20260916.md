# P3-STAGE-033 consumer-utility result review

| Field | Value |
| --- | --- |
| Date | 2026-09-16 |
| Work item | `P3-STAGE-033` |
| Mechanical preregistered gate | `PASS_CONSUMER_UTILITY_INCREMENT` |
| Research disposition | `INCONCLUSIVE_CONSUMER_UTILITY_ATTRIBUTION` |
| Review roles | short-term/event-driven, fund/risk, quant, data/methodology |
| Review mode | four independent GPT-6 Astra sessions after all experiment outputs were sealed |
| Prediction / economics / strict PIT claim | none |

## 1. Frozen result

P033 compared 20 Facts-only and 20 Facts+Layered evaluations over the exact P029 ten-observation
retrospective population. Four roles each evaluated ten packets, five per arm, without seeing the
same observation in both arms. The answer key was not opened until all role outputs were sealed.

The preregistered mechanical result was strong:

| Metric | Facts-only | Facts + Layered |
| --- | ---: | ---: |
| evaluations | 20 | 20 |
| packet with any critical interpretation error | 12 | 0 |
| all four answers correct | 8 | 20 |
| answer-level ABSTAIN | 28 | 0 |
| median elapsed | 7.1505 s | 6.3385 s lower / 6.6010 s conservative |
| median visible response tokens | 95 | 95 |

Every role moved from three Facts-only critical-error packets to zero Layered critical-error
packets. The Layered/Facts median elapsed ratio is 0.886 under the response-turn lower bound and
0.923 under the conservative fund-interruption bound. Both satisfy the preregistered accuracy path.

The fund role suffered one outer-host timeout after three completed packets. The fourth packet had
entered the same session but had not completed. The append-only session record proved the exact
breakpoint; the same thread completed that packet and then the remaining packets. No completed
packet was replayed, no second role session was created and the model/provider identity was not
changed. The mechanical decision is the same under both elapsed bounds.

## 2. Post-result design finding

All four reviewers independently accepted the numeric gate and independently identified the same
causal-attribution problem.

The Facts-only packet exposed the current Support Evolution snapshot (`phase`, unreclaimed age,
current support distance, current post-break rebound and provenance) plus the Stage10/11 semantic
definitions. It did **not** prove that those displayed fields alone uniquely reconstruct the prior
Stage10 confirmation and all state-history conditions needed for the frozen Stage10/11 truth.

The Layered packet exposed the same current Facts plus the already-derived `[10]` or `[10,11]`
state. Therefore P033 changed more than formatting if the current Facts snapshot is not a sufficient
statistic for the state history. The observed advantage may mix:

1. supplying otherwise omitted state-history information;
2. performing the state derivation for the consumer; and
3. organizing the result as a layered semantic surface.

That combination can be useful as a product, but P033 cannot isolate item 3 as the cause.

## 3. Role conclusions

### Short-term / event-driven

The current Layered packet clearly makes the fixed Stage10/11 question easier and reduces the risk
of reading a rebound as a resolved structural failure. Mechanical PASS is valid. Pure layered
expression utility is not identified because the control may lack necessary history.

### Fund / portfolio risk

The result supports a bounded communication value: an already-derived parent risk context and nested
rebound are communicated consistently. It does not show that a consumer given all necessary Facts
would make fewer mistakes solely because of layered formatting.

### Quant research

The experiment has answer-relevant information asymmetry risk. Forty evaluations are not forty
independent market observations. The result is valid as a comparison of the current two product
packets, not as an estimate of information-equivalent explanatory causality.

### Data / methodology

Same source observation does not imply information-equivalent packet. The minimum unresolved audit
question is whether every frozen Stage10/11 truth is uniquely derivable from the Facts-only fields
actually shown. Until that is proved, reasonable abstention under insufficient information must not
be called consumer misunderstanding.

## 4. Disposition

The four-role decision is unanimous:

- preserve `PASS_CONSUMER_UTILITY_INCREMENT` as the **mechanical preregistered gate**;
- close the primary attribution question as `INCONCLUSIVE_CONSUMER_UTILITY_ATTRIBUTION`;
- do not mark the experiment invalid: the packet-level product difference is real and reproducible;
- do not claim that Layered formatting itself caused the difference;
- keep Continuous Facts as the authoritative substrate and Layered Stage10/11 as a development
  semantic surface.

## 5. Only bounded next question

If separately authorized, the minimum next question is information sufficiency/equivalence:

> Can each frozen Stage10/11 truth be uniquely reconstructed from an information-complete control
> packet, and if so does layered presentation still reduce interpretation error or review cost?

A new experiment must be separately preregistered. It must not edit or re-label the P033 result.
The cleanest control should contain the same state-relevant information as the Layered arm; otherwise
another comparison would still confound state derivation with presentation.

## 6. Red lines

- Do not call information-insufficient ABSTAIN a reasoning failure.
- Do not turn the mechanical PASS into a prediction, economic, PIT or production claim.
- Do not treat 40 role-packets as 40 independent market observations.
- Do not backfill the P033 Facts-only packets and reuse the old result.
- Do not automatically open a successor.
