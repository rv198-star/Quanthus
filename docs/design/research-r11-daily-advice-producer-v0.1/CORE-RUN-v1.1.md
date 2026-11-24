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

## Successor after the 1bee0686 review

The 1bee candidate remains CHANGES_REQUIRED. The successor binds prepared feature code,
signal date, close/mark price, industry and listing-session coordinate to the exact scan
row before entry evaluation. Missing required entry projection fields produce named local
exclusions and preserve qualified neighbors. For held reviews an unbound feature payload
is discarded; a source-only view carries no inferred domain feature, and independently
qualified duration/marked-return base guards still run. Unknown branches hold and alert.

Holding and exit plans now count as non-empty audit content. Only an ENDED-only package
is empty advice. Source closure and result diagnostics preserve a named whole-day block,
and local exclusions are retained in diagnostics as well as source closure.

The normal producer version is shared by policy, receipt and audit. A mismatched policy
version blocks the run. The complete policy digest, including window_ref/eligible/expires,
binds the receipt and source closure. Held context uses a versioned no-trend contract with
assessment_requested=false; the actual scorer is abstained, with the context body retained.

Code provenance records the checkout HEAD separately from the last source/config commit
and tree. Local uncommitted source is explicitly marked dirty with actual file hashes and
no asserted committed source identity. Metadata-only checkpoint commits therefore do not
replace the executable source identity. Source and split policy digests are also recorded.

`config/research/r11/d1b-20261124/p12-consumer.synthetic.v1.json` pins the existing 9de1cdb9
test consumer and loader bytes. Integration tests verify that consumer, using an explicit
QUANTHUS_P12_WORKTREE_ROOT or discovery of the pinned Git worktree; no absolute machine
path is embedded in the tests. This binding is synthetic compatibility only, separate from
P12's future E5 consumer registration. These changes keep RuleDayOutput's seven fields and
their types unchanged. The Owner's paper_quote_feed principle is a separate pending
machine-policy update: it applies to P10-verified T+1 preopen fields, not daily source or
historical qualification.
