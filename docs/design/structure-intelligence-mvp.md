# Structure Intelligence MVP Technical Proof

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Authority | Research-only additive MVP; does not activate the draft stock-stage model or execution |
| Governing work item | `P3-STRUCT-001` |
| Implementation checkpoint | `../acceptance/structure-intelligence-mvp-implementation-checkpoint-20260906.md` |
| Final acceptance | `../acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md` |
| Parent design | `lld/03-label-feature-system.md`; Worker boundary in `lld/11-platform-kernel.md` |
| Base revision | `6d2d94a9296df27beca9567f63d3fb5e9ae55b6e` |
| Date | 2026-09-06 |

## 1. Decision being tested

The first structure-intelligence proof does **not** introduce generic DWS and does **not** revive
ADS. It follows the current ADR 0007 / ADR 0013 application-data boundary:

```text
admitted DWD daily projection
        |
        v
Structure Research ADM
  - exact upstream identity
  - exact policy digest
  - compact PIT structure rows
        |
        v
coarse structure fact
        |
        v
accepted trend-stability semantics adapter
        +
research-only structure fact mapping
        |
        v
existing veto / constraint / score aggregator
        |
        v
existing close-to-next-open P2 execution path
```

The ADM is thin, immutable in semantic identity, purpose-specific, research-only, and explicitly
forbidden from claiming DWD authority, generic-DWS status, operational-ADS status, production-stage
semantics, or execution authority.

Generic DWS remains demand-gated because this MVP has no cross-domain conformed-dimensional
consumer. ADS remains a historical name under ADR 0013 and must not become the current application
data surface.

## 2. What the MVP implements

`src/quanthus/structure_intelligence.py` introduces only enough machinery to prove the boundary:

- an explicit adapter from the existing Direct-DWD daily projection fields, preserving both the
  compatibility security code and governed `dwd_security_id`;
- caller-bound replay `snapshot_id` and `knowledge_cutoff`, rather than treating DWD ingestion time
  as the historical decision time;
- a deterministic 20-session application projection;
- six coarse research outcomes: unavailable, range intact, breakout attempt, breakout accepted,
  support threatened, and support breach;
- an immutable semantic ADM identity and manifest digest;
- one FeatureFact bridge into the existing expert protocol;
- reuse of the accepted `expert.trend-stability@1.0.0` rule semantics and its formal scoring
  mapping through a new `expert.trend-stability-structure-mvp@0.1.0` adapter identity, because the
  MVP's security-day population is not the original accepted opportunity population; and
- one deliberately simple structure scoring mapping so baseline trend-only decisions can be
  compared with trend-plus-structure decisions without claiming the adapter itself is accepted.

The coarse outcomes are **not** the draft thirteen-state vocabulary. The thresholds are plumbing
fixtures and are not approved trading parameters.

`config/structure-research-adm.mvp.v1.json` freezes this proof-only contract and its prohibited
claims.

`src/quanthus/structure_mvp_run.py` adds the deployable bounded runner. It consumes the existing
`config/replay-data-provider.development.v2.json` provider rather than implementing another DWD
reader, keeps a scan warm-up separate from the report window, resolves the governed
`dwd_security_id` from the job-local `daily_universe` projection, publishes immutable
`structure-adm.jsonl`, `summary.json`, `manifest.json`, and `READY`, and can run a bounded number of
independent P2 execution-compatibility probes. Those probes do not maintain a portfolio or report
performance.

`config/structure-mvp-run.development.v1.json` freezes the first intended real-data engineering
scope: scan from 2019-10-08, report 2020-01-02 through 2020-03-31, three predeclared securities
(`000001`, `300750`, `600519`), at most five P2 probes, and no model training.

## 3. Synthetic proof

The focused MVP tests prove the following without opening real market data.

### 3.1 DWD-shape -> ADM -> causal coarse state

A Direct-DWD-shaped daily sequence is adapted into the thin ADM. Reversing caller input order does
not change semantic content or manifest identity. The fixture has 20 prior rows, then a breakout
attempt and two later holding closes. The evaluator cannot label acceptance on the attempt date; it
produces:

```text
T      breakout_attempt
T+1    breakout_attempt / confirmation pending
T+2    breakout_accepted
```

This proves the MVP does not use future confirmation to rewrite the T observation.

### 3.2 ADM -> accepted trend semantics adapter -> structure comparison -> aggregator -> P2

The deployable runner reuses the already accepted trend-stability rule and formal scoring semantics
through a new research adapter identity. Baseline decisions use that trend adapter alone;
structure-assisted decisions use the same trend output plus the coarse structure fact. Synthetic
fixtures prove a neutral trend baseline can remain `no_action` while a separately observed accepted
breakout changes only the assisted path to `trial_entry`. The resulting decision is translated to a
normal `BacktestSignal`, and the existing P2 close-to-next-open engine produces one next-session
Fill. This proves that no separate matcher or execution path is required for structure information.

Additional fixtures prove invalid/missing observation gaps reset structure history instead of being
silently bridged, P2 probes are selected round-robin across the frozen security scope, Worker input
cannot substitute arbitrary config paths or commands, sealed artifacts reject tampering, and the
accepted trend helper preserves the existing stable/neutral/fragile/abstain semantics. The Event
image is also bound to an exact clean Git revision. A default-off qualification seeder can submit
exactly two fixed Structure MVP A/B Jobs through the normal DurableJobQueue when the external MCP
submission whitelist has not yet been updated; it exposes no caller-selected command, policy,
universe, or data path.

Focused MVP, traceability, and Platform-boundary closeout verification reports `61 passed`.
Repository-wide `make check` reports `1400 passed, 7 skipped`; the skips are existing environment-
bound PostgreSQL or mounted-production-artifact checks. `git diff --check` also passes. The
result-bearing Event Worker image was deployed from an exact Git revision and completed both
real-DWD qualification Runs.

## 4. What this does not prove

This proof does not establish any of the following:

- that the retrospective Direct-DWD record view proves stronger per-decision historical-publication visibility or a canonical `AS_KNOWN` contract;
- that the six coarse structure outcomes are statistically or economically useful;
- that any of the synthetic thresholds are suitable for A-share trading;
- that the draft thirteen-state model should be activated or fully implemented;
- that the structure expert should be enabled in the accepted formal strategy;
- that P6 Shadow, paper, notification, or live execution should be opened;
- that the synthetic action change has positive economic value; or
- that a machine-learning training layer is needed.

The proof also does not repair or reinterpret the existing Direct-DWD provider. The real runner
records the provider's own `point_in_time` declaration, including
`record_view=retrospective_dwd_record_view` and `per_decision_publication_proven=false` when that is
the active provider identity. Therefore a successful real run can prove engineering compatibility
and T-close replay behavior, but it cannot by itself promote the retrospective record view into a
stronger historical-publication claim.

## 5. Accepted real-data proof and next evidence boundary

The preregistered real-DWD qualification is now accepted under
`../acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md`.

Two independent Durable Jobs on exact Event Worker revision
`269e73304090ba8296a0413ed8b0a0c01430b2b3` reproduced the same 174-row ADM, state counts,
decision counts, five P2 probes/fills, ADM manifest digest, and semantic result digest. The bounded
outcome-blind review projected two rows for each actually observed coarse state; all 10 manually
reviewed cases showed no obvious T-close semantic contradiction.

The accepted MVP therefore proves the real DWD -> thin ADM -> coarse structure -> expert ->
aggregator -> P2 technical chain and deterministic reproduction. It still does not prove economic
value or stronger historical-publication PIT than the Direct-DWD provider itself declares.

The next authorized evidence step, if pursued, is a separately preregistered complete-P2
baseline-versus-structure-assisted economic comparison under the same information/history budget.
Do not expand to the thirteen-state vocabulary, train a model, add generic DWS, revive ADS, or make
trading-value claims merely because the technical MVP is accepted.

## 6. Stop conditions

Stop or narrow this research line when any of these occurs:

- real DWD cannot reproduce the required PIT inputs without a new canonical-data requirement;
- the thin ADM becomes semantically broader than the structure consumer needs;
- structure facts do not change any expert decision under a predeclared mapping;
- action changes occur but provide no useful risk/economic evidence in complete P2 replay; or
- the coarse fact set performs as well as finer state vocabulary for the same information budget.

A positive technical proof authorizes only the next evidence step. It does not authorize automatic
expansion of architecture or strategy complexity.
