# Structure Intelligence MVP Technical Proof

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Authority | Research-only additive MVP; does not activate the draft stock-stage model or execution |
| Governing work item | `P3-STRUCT-001` |
| Implementation checkpoint | `../acceptance/structure-intelligence-mvp-implementation-checkpoint-20260906.md` |
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
accepted trend helper preserves the existing stable/neutral/fragile/abstain semantics.

Focused MVP, traceability, and Platform-boundary verification currently reports `59 passed`.
Repository-wide `make check` reports `1391 passed, 7 skipped`; the skips are existing environment-
bound PostgreSQL or mounted-production-artifact checks. Ruff on every changed Python path and
`git diff --check` also pass. A local source-built Event image containing the MVP runner builds
successfully.

## 4. What this does not prove

This proof does not establish any of the following:

- that real DWD historical rows satisfy the required PIT-admission contract;
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

## 5. Next bounded real-data proof

The real-DWD qualification remains small and falsifiable:

1. run the frozen `structure-mvp-run.development.v1.json` request inside a Worker image that has the
   existing read-only DWD Base/correction mounts and P1 dependency group;
2. verify the sealed Structure MVP package and record input identity, row counts, coverage,
   invalidity reasons, build time, bytes, and memory;
3. repeat the same request in an isolated attempt and require the ADM semantic digest and result
   digest to match;
4. inspect a stratified sample of the coarse structure observations without future bars; and
5. only after that proof, define a separate complete-P2 baseline-versus-structure-assisted economic
   comparison. The current independent P2 probes are execution-compatibility evidence only.

The development worktree now has the P1 research dependencies needed for tests, but it does not
have the DWD Base or frozen research-bundle mounts. Production Event Worker has those exact
read-only mounts, but the current deployed revision and MCP submission schema do not yet admit
`quanthus.structure-mvp.v1`. The source implementation therefore cannot legally perform step 1
until the exact repository revision is published, the Event Worker is deployed from that revision,
and the typed Job kind is admitted. Do not bypass that gate by overloading an unrelated Job kind.

Do not expand to the thirteen-state vocabulary, train a model, add a generic DWS, revive ADS, or
make economic claims until this bounded experiment shows a concrete reason to do so.

## 6. Stop conditions

Stop or narrow this research line when any of these occurs:

- real DWD cannot reproduce the required PIT inputs without a new canonical-data requirement;
- the thin ADM becomes semantically broader than the structure consumer needs;
- structure facts do not change any expert decision under a predeclared mapping;
- action changes occur but provide no useful risk/economic evidence in complete P2 replay; or
- the coarse fact set performs as well as finer state vocabulary for the same information budget.

A positive technical proof authorizes only the next evidence step. It does not authorize automatic
expansion of architecture or strategy complexity.
