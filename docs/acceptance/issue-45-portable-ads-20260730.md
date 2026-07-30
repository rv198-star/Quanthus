# Issue 45 Portable Strategy ADS Deployment Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | accepted for the declared boundary |
| Accepted boundary | three-node deployment, three role-scoped bootstrap bundles, access isolation, and normal replay input migration |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |
| Deployed revision | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` |
| Bundle publisher revision | `1f1ea4d5d43a661b79f47d61fc5e47dc64185d58` |
| Governing design | `docs/architecture/adr-0005-dual-lane-canonical-and-ads-publication.md` |

## 1. Scope

This record covers the formal OCI publication of the three historical `strategy_replay` bootstrap
bundles, their semantic and operating-system role checks, migration of the normal strategy replay
input to explicit portable ADS, and deployment of the exact reviewed revision on OCI, CN01, and
Hermes.

It does not accept production dual-lane cutover, enable target timers, stop the legacy OCI
publisher, authorize cleanup, prove catalog-native historical publication, or reveal a genuinely
unopened blind evaluation. All three bundles remain `research_only=true` and
`execution_authorized=false`.

The first full formal replay exposed a right-boundary reader defect at the final 2021-12-31
execution session: the reader incorrectly required runner-only T+1 evidence to be a decision PIT
binding. Revision `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` keeps decision snapshots bound to
their exact PIT rows while allowing an execution snapshot only when it is the next complete session
of an authorized decision. It records that authorizing decision in source lineage and rejects any
other execution date. The failed run evidence was retained in its original output directory.

## 2. Three-Node Deployment

| Node | Revision | Doctor | Timer state |
| --- | --- | --- | --- |
| OCI | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` | legacy and dual-lane preflights OK | legacy `enabled/active`; target epoch and arbitration `disabled/inactive` |
| CN01 | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` | `dual-lane node deployment preflight OK` | candidate `disabled/inactive` |
| Hermes | `a9a4a85721d8a5830e3d2b5364639446c4ec2f62` | `dual-lane node deployment preflight OK` | candidate `disabled/inactive` |

All three doctors reported policy digest
`sha256:5869be346f9017f1af73375935b5ea07e0cc2e1099aa28db3d9e4741bea922ea`, fixed
primary `cn01`, standby `hermes`, and `delete_enabled=false`. The OCI target tree contained no
`GO_FOR_CUTOVER.json`.

## 3. Published Bootstrap Bundles

| Scope | Decision range | Bundle / content digest | Manifest SHA-256 | READY file SHA-256 | Bytes |
| --- | --- | --- | --- | --- | ---: |
| development | 2012-01-04 to 2021-12-30 | `strategy-replay-development-1f1ea4d-v1` / `sha256:ed799c7825c10ae1a1e6b7a722c0df82e35703a77d822a77b3d1cc2a5970c284` | `9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628` | `a51bd1df71dd1825c071d2bf8199b12ca00657ae7f4acaf0af31aa85f63f3d35` | 326,551,858 |
| arbitration | 2022-01-04 to 2024-12-30 | `strategy-replay-arbitration-1f1ea4d-v1` / `sha256:7ecb8873bd4f32cf4cd9ef0198d39afce98618a19dc036ad608c301740cdaf62` | `1a92209bc7fefa2b2a409df53e1a0e422fb980d4dd65fba17fcfbd995cb0f499` | `8963d6c378ae9d0e3d14e25666e0afd428424a9db3eded1e5dc1f7d54782a979` | 472,973,670 |
| 2025 migration | 2025-01-02 to 2025-12-30 | `strategy-replay-permanent-blind-1f1ea4d-v1` / `sha256:39216935777718daa6c6916d0095009ec450083634679cbf32548beb997b3dd6` | `79e39733f52891d80c26d177d5780030e124cfc12860fbaeb410e8c8ca566db2` | `554780459b6aa395489fdd7713d70befa0e0bcd39b52d4e9af76e0ec44d09625` | 525,334,025 |

Each immutable bundle contains seven manifest-discovered semantic tables, writes `READY` after the
manifest, and has no mutable `CURRENT`. Root-owned publication recomputed canonical logical-row
digests and then ran an independent filesystem probe under the scope principal. Failed publication
attempts and staging evidence were retained rather than reused or deleted.

The source is accepted snapshot `p1-historical-replay-20260729-v1`. Its bootstrap PIT map correctly
records `capability_fixture_without_per_decision_publication_proof`; these bundles do not claim that
an independent canonical publication existed at every historical decision time.

## 4. Scope Isolation

| Principal | Development | Arbitration | 2025 migration |
| --- | --- | --- | --- |
| `ubuntu` | read | denied | denied |
| `quanthus-arbitration` | denied | read | denied |
| `quanthus-blind` | denied | denied | read |

The bundle roots are mode `0750` and owned by `root` with groups `ubuntu`,
`quanthus-arbitration`, and `quanthus-blind`, respectively. Each authorized principal can traverse
every directory and read every file in its bundle but cannot write a bundle path. All six
cross-scope checks fail closed.

The 2025 bundle proves physical role separation only. Its source was visible to the development
operator before these principals existed, so it is a migration and capability artifact rather
than genuine unopened blind evidence. No strategy or result in this acceptance uses it.

## 5. Strategy Dependency Boundary

Normal `audit-historical-replay-frames`, `run-baseline-strategy-suite`, and
`run-governed-portfolio-replay` help and parser contracts expose explicit `--ads-bundle` and
`--ads-role` inputs. The hidden `--snapshot-dir` branch remains only for byte-reproducing frozen P3
acceptance and emits an acceptance-only warning.

The four strategy consumers in `backtest/baseline_research.py`,
`backtest/portfolio_replay.py`, `backtest/strategy_replay.py`, and `governed_strategy.py` neither
import `quanthus.pipelines` nor call ODS selection, warehouse, or legacy snapshot loaders. They do
not resolve ODS/DWD/DWS or mutable `CURRENT` paths. Focused
`tests.contract.test_strategy_ads_boundary` verification passed all three checks.

`historical_bundle.py` and `historical_frames.py` remain present for the bootstrap builder and
frozen P3 compatibility. `strategy_replay_ads.py` still assembles this historical bootstrap from
the accepted snapshot. Catalog-native ADS publication remains an explicit open delivery item, not
a runtime dependency of normal ADS replay.

## 6. Formal P3 Replay

The formal governed portfolio replay completed successfully from only the development ADS bundle
at deployed reader revision `a9a4a85721d8a5830e3d2b5364639446c4ec2f62`. Its run manifest binds:

- bundle id `strategy-replay-development-1f1ea4d-v1`;
- content digest
  `sha256:ed799c7825c10ae1a1e6b7a722c0df82e35703a77d822a77b3d1cc2a5970c284`;
- manifest SHA-256
  `9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628`;
  and
- source snapshot `p1-historical-replay-20260729-v1` with the declared historical-bootstrap PIT
  limitation.

The retained outputs are below
`/home/ubuntu/data/quanthus/acceptance/issue-45-portable-ads-a9a4a85`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `feature-panel.parquet` | 572,813,487 | `5df954c7bf11858b46ccee394c01c089e2c7c146c79d7cbc60c38a383bdeb57d` |
| `governed-result.json` | 406,561,760 | `89c2661ccf3b2a8b86bc59402baa0f0b6b6d64eaa92c86543795709635993e3e` |
| `governed-scoreboard.md` | 859 | `53dc8030c0e0dcaaa514edb77ea9fdd59bd59c5de7e8c2eac83bd75451e5c552` |
| `issue45-p3-business-equivalence.json` | 2,270 | `ee6eca4fdbc396e1f9b14d88e76d9246407e64ef73ad015d5ea61c565fddcafc` |
| `issue45-feature-panel-comparison.json` | 14,067 | `1d2adf9001d4b37e934412e2b369d92c8852b8d55e0fed239f6b26b63b69c3cb` |

The control result was the retained pre-publication development-bundle run at
`/tmp/quanthus-strategy-replay-ads/development-v2-governed-result.json`, SHA-256
`b7ffe23b1bbc3a16d919c11f1551d0cd919b28782c910ced5d63b71918807a74`. Whole-result hashes are
expected to differ because snapshot ids, exact input lineage, feature-panel identity, decision ids,
opportunity ids, event ids, transition ids, and evidence digests bind their physical bundle.

The machine-readable business comparison therefore retained every price, quantity, fee, reason,
action, score, state, and timestamp while normalizing only those input-lineage-derived identities.
It proved exact business equivalence for all 52,423 decisions keyed by market date, security code,
and capital unit; all 7,344 engine events as an order-independent multiset; all 947 lifecycle
transitions; all 2,431 equity points; all 230 closed trades; final ledgers and lifecycles; performance
and breakdowns; the opportunity funnel; and all validation gates.

The two feature panels have the same schema, 7,724,219 rows, and exact
`(market_date, security_id)` key set. They are not byte- or value-identical: 7,162 rows differ,
confined to the three frozen code-validity identities `SZSE-000022`, `SZSE-000043`, and
`SZSE-300114`. The formal scoped bundle applies its declared code-validity and scope-continuity
boundaries, including fail-closed exclusion where complete identity continuity is unavailable.
No normalized strategy decision, trade, ledger value, equity point, metric, or gate changed. This
bounded safety difference is disclosed rather than hidden behind equal terminal metrics.

The accepted replay produced 461 operations (231 buys and 230 sells), 230 closed trades, 31.909470%
total return, 4.746455% maximum drawdown, 37.391304% win rate, 6.875168% average invested capital,
31.761317% time in market, and 19.109432% return after removing the three largest closed-trade
contributions. All four configured gates passed.

## 7. Resource Evidence

OCI had four CPU cores, 23 GiB RAM, and no swap during publication and replay. The three full
publication/deep-check runs were intentionally sequential. Approximate observed peak RSS was 5 GiB
for development, 7.1 GiB for arbitration, and 7.8 GiB for the 2025 migration bundle; the formal P3
replay reached approximately 4.2 GiB RSS while using about 2.2 of the four CPU cores. The accepted
publication mode therefore remains sequential; no concurrency claim is made by this evidence.

## 8. Preservation And Residual Gate

No legacy ODS, DWD/DWS, snapshot, ADS, target artifact, failed publication, log, pointer, or timer
was deleted or repurposed. No consumer was switched to the target release-set pointer, and the
legacy OCI publisher remains the production authority.

This closes the formal three-node deployment, historical bootstrap backfill, role-isolation, and
normal ADS replay-input slice only after section 6 is complete. Issue #45 remains open for
catalog-native publication, full PIT/tradability candidate families, multi-session qualification,
clock and failure drills, independent restore/retention evidence, consumer cutover, writer fencing,
and explicit `GO_FOR_CUTOVER`. Cleanup remains separately prohibited.
