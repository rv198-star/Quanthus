# P11 fixed-rule core run v1.1 — engineering candidate

This successor repairs the M1 defects independently reported against de2c1447. It is a
synthetic engineering candidate for #700/#725. The existing release-manifest.v1.json still
identifies the previous candidate; this document does not update the three-piece adoption
application or its pending industry/source bindings.

## Domain boundary and call graph

`run_daily_advice` in `product_daily/recommendation/rule_seam.py` is the prepared-input
entry point. It takes the fixed FormalStrategyRunner, its registry/panel/trend resources,
qualified in-memory scan rows, prepared P11 feature facts, held account facts, a preregistered
AdvicePolicy and a run ID. It accepts no finished candidates or caller-issued rule receipt.

```
P12 source reader -> P11 M2 raw adapter [separate successor]
 -> prepared job-local rows/features
 -> run_daily_advice
    -> scan_forward_candidates
    -> trend classification + FormalStrategyRunner.evaluate_entry + eligibility
    -> independent daily held review + FormalStrategyRunner.evaluate_held
    -> preserve actual FormalStrategyDecision objects/digests and source clocks
    -> issue coverage receipt + source closure in this invocation
    -> internal G1 assembly -> decision_sets + audit
 -> P12 account sizing / publish gate / consumer binding [P12]
```

The raw-to-feature adapter is still a separate M2 delivery. This entry does not read physical
data, create consumer bindings or publish recommendations. The supplied feature panel must
bind the actual feature registry digest. Stage elapsed time and row counts are returned;
source bytes and upstream feature time remain unknown here and belong to the source/adapter
stages. Intermediates are immutable job-local TMP, built once and shared by both expert paths;
no cache or new published data product is introduced.

## Evidence and component identity

Both expert_decision_digest and risk_decision_digest reference the actual unified
FormalStrategyDecision.digest: that object contains the guardian, veto, constraints and
scoring result. Full decision evidence travels in the package audit. Candidate and held
payloads, their clocks, member/intake identities and the actual run scope bind the receipt.
The source closure includes raw-input and feature-input content digests, registry/panel and
topology identities, outcomes and exclusions. Its digest and evaluation clocks/counts bind
the normal receipt. The canonical component is the registered card's strategy_id/version
and topology digest; caller-selected aliases are refused.

Public content-hash helpers verify content integrity. The runtime trust boundary is the
in-process fixed-rule entry point, not cryptographic authentication of arbitrary Python code.
P12 relays the resulting receipt/source closure; it does not issue them. An empty producer
allowlist admits none. The current producer ID `p11.fixed-rule-day` is an engineering identity;
formal account-mode registration remains with the versioned use receipt and P12 E5 gate.

The historical injected `DailyAdviceProducer.produce` API now requires explicit
`evidence_mode=structural_fixture` and marks its audit `TEST_ONLY_STRUCTURAL`. Default
`formal_runner` calls through that API are refused with `same_job_rule_entry_required`.
The same-job synthetic path is marked `SYNTHETIC_FIXED_RUNNER`. These evidence grades do not
set daily_adopted or change A01–A07.

## Held review and clocks

A held security gets its own daily review subject from its current source row and held
facts. It does not require a new-entry event. Maximum holding, constraint stop and structural
deterioration retain the fixed card's values and priority. Structural deterioration needs
the prior consecutive-session count; an unavailable count leaves that branch unevaluated.
Missing evidence returns hold-and-alert, while independently qualified risk/base reductions
or exits retain their result. Missing source identity is an alert, never a synthetic sale.

For prospective entries, T-close observation, real source visibility and the preregistered
decision deadline remain distinct. The source clock must bind the market day, deadline and
next open. The old opportunity-level ExpertObservation anchors at T close, so the evening
path uses the existing pure classify_trend_stability algorithm with a separately versioned
`quanthus.p11-prospective-trend-evidence.v1` envelope. It preserves source time and feature
values; no min(source_time, close) rewriting occurs. Legacy event/assessment behavior is
unchanged. Full-day volume-scope/ST input increments remain in the separate M2/P10 contract.

## Acceptance and remaining work

Four fail-first cases reproduce the de2 defects: digest substitution, Namespace imitation,
held risk exit without a new signal, and source-cutoff rewriting. Additional cases cover
real held-only exit, reduce/base-stop/missing-data outcomes, component aliases, empty trust,
public receipt injection and genuinely evaluated empty advice. Real P12 parsing/validation
is exercised for entry and held outcomes.

The review submission binds its exact Git identity and development logs externally. CN02
is the formal runtime for focused and full acceptance; unavailable fetch/runtime checks are
reported as NOT_RUN. The M2 raw adapter, P10 source qualification/publisher and native preopen
facts, P11 action adoption and the P12 isolation-station acceptance remain separate work.
Historical U06/U07 stays parked. No market rows, strategy thresholds, P6 runtime, accounts,
production configuration or deployment change in this repair.
