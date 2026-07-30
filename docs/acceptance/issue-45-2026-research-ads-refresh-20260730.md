# Issue 45 2026 Research ADS Refresh

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | accepted for the declared historical-research boundary |
| Accepted boundary | 137 decision sessions through 2026-07-29; T+1 context through 2026-07-30 |
| Deployed revision | `7c99724c641286dbff57be6479fdcafabfd13d3f` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-2026-research-ads-refresh-7c99724` |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |

## 1. Scope And Verdict

The additive v3 `recent_research` bundle is formally published on OCI and accepted for historical
strategy replay. It is readable by `development_research`, not writable by that principal, has no
portable ADS `CURRENT`, and declares `research_only=true` and `execution_authorized=false`.

This refresh does not replace the prior v1 recent bundle or the development, arbitration, and
permanent-blind bundles. Together the accepted exact bundles cover the 2012-2021 development,
2022-2024 arbitration, 2025 migration/blind-role, and 2026 recent-research partitions through the
latest complete decision/execution pair. Their role boundaries remain physically separate.

## 2. Source Chain

| Artifact | Identity |
| --- | --- |
| Replay snapshot | `p1-historical-replay-2026-through-20260730-v3`; manifest SHA-256 `6cfca18c3da4f6f6aba9ac050ccb67ca8aa5024cc2ab4e83b7af73007502468f` |
| CSI 300 curve | `csi300-sh000300-2012-20260730.mixed-v3.json`; file SHA-256 `1bb9fb120142485bbb392e1f09a0d979297fad85e14e197f5f62ce7dbb6cd183` |
| ADS policy | `strategy-replay-ads-v3` |
| Corporate-action policy | `p1-corporate-action-normalization-20260730-v3` |
| ST policy | v2 fail-closed missing-source rule; no prior-day status inference |

The source snapshot contains 38,531 normalized corporate-action rows and 328 versioned
fail-closed exceptions. The v2 ST build explains 627 missing intermediate observations only where
the same source day also lacks a bar; each remains `unknown` with `can_open_position=false`.

## 3. Published Bundle

| Property | Accepted value |
| --- | --- |
| Path | `/srv/quanthus/portable-ads/recent_research/strategy-replay-recent-research-7c99724-v3` |
| Bundle content digest | `sha256:449aa35caf5127390805a0d744f11f17693372611f987db6617a94872a5c4076` |
| Manifest SHA-256 | `d4b948b4acf58f518ec604e87876af17130d329ea3fd4cead9deacbedad2fc5b` |
| READY SHA-256 | `3c8c7f91481bca9a5e1d15a45ebd2fe618c37c266efde31a15dd5587527d2d05` |
| PIT-map digest | `sha256:e841008384090edbaf0fcca4c4f9a0f8c46b8cf22ef55e8d50466f165dca0442` |
| Total bytes | 556,210,751 |
| Semantic tables | 7 |
| Reader role | `development_research` |

The root publisher built the bundle serially, recomputed every canonical logical-row digest, then
ran the scope-role filesystem probe. `ubuntu` can traverse and read the tree but cannot modify its
READY marker. No portable `CURRENT` file or symlink exists.

## 4. Independent Frame Audit

The independent formal-bundle report has file SHA-256
`af1f47e3a1dc1a3de85ca995e24629db18eebc3f2c3d147fdddeea98ce279b8e`
and audit digest
`sha256:26fd4c8bc4d41180218227263d090f5029da468910ac9f531a002bd75646a68c`.
Its counts match the source-snapshot and pre-publication audits exactly.

| Audit measure | Count |
| --- | ---: |
| Frames | 137 |
| Securities | 5,220 |
| Decision observations | 711,956 |
| Execution evidence rows | 711,838 |
| Corporate actions | 3,372 |
| Corporate-action blocked pairs | 15,879 |
| Non-executable observations | 18,674 |
| Suspended evidence | 1,714 |
| Missing bars | 6,194 |
| Missing execution evidence | 137 |
| Unexplained price transitions | 114 |
| Invalid bars | 0 |

The report states `ready_for_strategy_replay=true` and `execution_authorized=false`. The evidence
root retains exact copies of formal manifest/READY, the audit report, a machine-readable summary,
and a checksum ledger.

## 5. Preservation And Residual Gate

No snapshot, revision, old ADS bundle, operational release set, legacy warehouse pointer, or ODS
release was deleted or overwritten. Catalog-native research publication, genuine unopened blind
evaluation, independent restore qualification, consumer migration, and production cutover remain
open. Future consumers must name this exact bundle id and role; they must not infer an annual
directory layout or resolve a mutable warehouse pointer.
