# Structure Intelligence MVP implementation checkpoint (2026-09-06)

| Field | Value |
| --- | --- |
| Status | implementation-ready; **not accepted** |
| Governing work | `P3-STRUCT-001` |
| Design | `docs/design/structure-intelligence-mvp.md` |
| Base revision | `6d2d94a9296df27beca9567f63d3fb5e9ae55b6e` |
| Real-DWD qualification | NOT RUN |
| Independent real-DWD reproduction | NOT RUN |
| MVP acceptance | NO |
| Execution / Shadow / P6 authority | NONE |

## 1. Claim ceiling

This checkpoint proves only that the bounded Structure Intelligence MVP is implementation-ready for
one governed real-DWD qualification. It does not prove that the coarse structure facts are correct,
statistically useful, economically useful, suitable for the draft thirteen-state vocabulary, or
eligible for Shadow, paper, notification, or live use.

The accepted architecture boundary for the proof is:

```text
DWD
  -> thin Structure Research ADM
  -> coarse causal structure facts
  -> accepted trend-stability semantics through a new security-day research adapter
  -> baseline-vs-structure-assisted comparison
  -> existing aggregator
  -> bounded existing P2 execution probes
```

Generic DWS is not introduced. Historical ADS is not revived. The Structure Research ADM is an
application-owned immutable research projection and cannot claim canonical DWD authority.

## 2. Frozen real-data request

`config/structure-mvp-run.development.v1.json` freezes the first qualification scope before any
result is opened:

- scan warm-up starts `2019-10-08`;
- report window is `2020-01-02` through `2020-03-31`;
- securities are exactly `000001`, `300750`, and `600519`;
- at most five independent P2 execution-compatibility probes are admitted;
- each P2 probe uses 100 shares;
- only the development replay provider and accepted `close-to-next-open-v1` P2 policy are admitted;
- no model training, parameter search, arbitration partition, permanent-blind partition, Shadow, or
  portfolio-performance claim is admitted.

The current coarse structure parameters remain technical-proof fixtures. This checkpoint does not
approve them as trading thresholds.

## 3. Implemented evidence chain

### DWD and ADM boundary

`src/quanthus/structure_mvp_run.py` consumes the existing `ReplayDataProvider` and
`HistoricalReplayFrame` T-close decision view rather than creating another generic DWD reader. It
preserves both the compatibility security code and governed DWD security identity. Missing or
invalid daily observations set a history-reset boundary so a 20-session structure window cannot
silently bridge an evidence gap.

`src/quanthus/structure_intelligence.py` builds a deterministic thin ADM with six technical-proof
outcomes: `unavailable`, `range_intact`, `breakout_attempt`, `breakout_accepted`,
`support_threatened`, and `support_breach`. The ADM binds the upstream provider identity and policy
digest and explicitly forbids DWD, DWS, ADS, production-stage, execution, and economic-value claims.

### Existing-expert semantics adapter

The MVP does not claim that the accepted opportunity-level `expert.trend-stability@1.0.0`
assessment has already been validated on the new security-day population. Instead,
`classify_trend_stability()` reuses its exact accepted stable/neutral/fragile/abstain rule
semantics, while `expert.trend-stability-structure-mvp@0.1.0` is a new research adapter identity.
Its scoring values are read from the accepted formal trend policy mapping. The artifact records the
source expert semantic and mapping digests plus the population-disposition ceiling.

Baseline decisions use the trend-semantics adapter alone. Structure-assisted decisions use the same
trend output plus the coarse structure fact. Decision changes are therefore explicit and auditable.

### Aggregator and P2

Both variants use the existing veto/constraint/score aggregator. P2 probes are independent
execution-compatibility checks, not a portfolio backtest. Probe selection is deterministic and
round-robins across the frozen security scope, prioritizing decision-changing assisted entries.
Every probe uses a normal `BacktestSignal` and the existing `BacktestEngine` under
`close-to-next-open-v1`.

### Worker boundary

`quanthus.structure-mvp.v1` is implemented as an exact second research Job kind on the existing
static Event Worker. It does not add another service or Worker registry. The Worker admits only the
repository-bound run config plus an exact code revision, launches only
`python -m quanthus.structure_mvp_run`, inherits the existing read-only DWD mounts and generation
fencing, and seals results below a separate `structure-runs/` namespace.

The verifier checks file SHA identities, the semantic result digest, ADM manifest digest, ADM row
content digest, request identity, code identity, `manifest.json`, and `READY`. A tampered ADM row
fails verification.

## 4. Verification completed

The final implementation checkpoint passed:

- focused MVP / traceability / Platform-boundary suite: `59 passed`;
- repository-wide `make check`: `1391 passed, 7 skipped`;
- skipped tests are existing environment-bound PostgreSQL or production-artifact-mount checks, not
  Structure MVP failures;
- changed-path Ruff checks: PASS;
- `git diff --check`: PASS;
- local Event image source build: PASS;
- Event Worker control-plane import boundary audit: PASS;
- production deployment dry-run from current production revision
  `f81328f4e744aaa2918b6bc265f20ff68579e4fa` to repository `main@6d2d94a...` for the Event Worker:
  PASS, proving the fixed deployment path is operational. This dry-run did not contain the
  uncommitted MVP changes and is not a Structure MVP deployment.

During full-suite preparation, `make bootstrap-p1` selected Polars `1.44.1`, while the retained
historical dual-lane contract freezes Polars `1.43.0`. Pinning only the local test venv back to
`1.43.0` restored all 40 dual-lane tests and allowed the full repository gate to pass. No repository
runtime policy was changed for this checkpoint.

## 5. Required evidence before acceptance

`P3-STRUCT-001` may close only after all of the following are produced from one exact published
revision:

1. the exact MVP revision is available through the approved GitHub distribution path;
2. the Event Worker is deployed from that exact revision with the existing DWD mounts read-only;
3. the durable queue / MCP admission accepts exactly `quanthus.structure-mvp.v1` and the frozen run
   config;
4. Run A completes on the frozen real-DWD development scope and its sealed artifact passes deep
   verification;
5. an operationally separate Run B repeats the same semantic request and reproduces the ADM content
   digest, ADM manifest digest, semantic result digest, state counts, decision counts, and P2 probe
   results;
6. the result records real coverage and unavailable reasons for all three securities and the fixed
   report window;
7. a bounded outcome-blind sample review confirms that the observed coarse-state paths are not
   obviously nonsensical at T close; and
8. the acceptance record explicitly decides whether the evidence justifies a later
   baseline-versus-structure complete-P2 economic experiment. It must not infer economic value from
   the independent probes.

## 6. Current external blockers

The source changes in this checkpoint are currently isolated and uncommitted. The available coding
workspace tools intentionally do not expose Git commit/push authority, and the current production
MCP submit schema admits only the already registered candidate-replay and P4-event-scan Job kinds.
`quanthus_production_deploy` accepts only exact `origin/main` commits.

Therefore a real-DWD Run cannot be truthfully bound to an exact published MVP revision in this
session without bypassing the project's Git and typed-Job governance. Overloading
`quanthus.p4-event-scan.v1`, forging the base commit as the new code identity, or reading production
DWD through an unrelated path is explicitly forbidden by this checkpoint.

These are release/admission blockers, not analytical passes. Until they are removed and the two
real-DWD Runs reproduce, the only correct state is:

```text
P3-STRUCT-001: OPEN
Implementation readiness: PASS
Real-DWD qualification: NOT RUN
Independent reproduction: NOT RUN
MVP accepted: NO
```
