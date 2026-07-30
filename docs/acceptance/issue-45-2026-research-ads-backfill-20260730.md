# Issue 45 2026 Research ADS Backfill Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-PIPE-003` |
| Tracking issue | GitHub issue #45 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | accepted for the declared historical-research boundary |
| Accepted boundary | 2026 decisions through 2026-07-24 with next-open context through 2026-07-27 |
| Deployed revision | `8550579d7386bfb9b2c56f53f9a128accf1c6de9` |
| Evidence root | `/home/ubuntu/data/quanthus/acceptance/issue-45-2026-research-ads-backfill-8550579` |
| Governing design | `docs/design/lld/01-data-foundation/strategy-replay-ads.md` |
| Production cutover | blocked; no `GO_FOR_CUTOVER` was recorded |

## 1. Scope And Verdict

The additive 2026 `recent_research` backfill is complete and available on OCI as one immutable
portable strategy-replay ADS bundle. It contains 134 continuous decision frames from 2026-01-05
through 2026-07-24 and next-open execution context through 2026-07-27. The bundle is readable by
the existing `development_research` role, is not writable by that principal, and is accepted for
historical strategy replay.

This record does not accept production dual-lane cutover, authorize an order or notification,
open arbitration or permanent-blind data, prove per-decision historical publication, or switch a
consumer pointer. The bundle declares `research_only=true`, `execution_authorized=false`, and the
historical-bootstrap PIT limitation
`capability_fixture_without_per_decision_publication_proof`.

## 2. Source Chain

| Artifact | Identity | Evidence |
| --- | --- | --- |
| 2026 base snapshot | `p1-2026-research-base-20260730-v1` | snapshot manifest SHA-256 `a65ae145168c0ef23f059e693d2b4f69505b1bfb88de5dde02b89cb0738dd9f0` |
| Corporate-action revision | `corporate_action_replay_2012_20260727_v2` | manifest SHA-256 `d3ae9c8bbfe2b2e0ac27d3d2aea14466380442b7f45d05c6718d4809da8dea70` |
| Replay snapshot | `p1-historical-replay-2026-through-20260727-v1` | bundle digest `sha256:17a1a02f4a4fffb2bcf25aab1bbc429f776af0802948f1afdcbdbf621cbcd304`; manifest SHA-256 `d86260353ac79313965fe847e87b2b5b3c87dddd5d36e6f7527973d9cd4c2fc1` |
| CSI 300 curve | `csi300-sh000300-2012-20260727.tencent-v2.json` | logical digest `sha256:b6626b93375e7c826df5f0775adb04bcfd134eb7512d9f6952397844059ac09f`; file SHA-256 `669021379583551af352624182f312e32105979440bce6851da0a39a29605434` |
| ADS policy | `strategy-replay-ads-v2` | policy SHA-256 `sha256:4c727218ae66706361da54921394f1819073d69c41bfef02695e653c7cc7b29d` |

The corporate-action revision contains 38,530 normalized source rows and 328 versioned
`security_fail_closed` exceptions. It is an additive child of
`corporate_action_replay_2012_2025_v1`; its policy retains the accepted normalization thresholds
and changes only the declared source/ledger scope and identity. No exception was converted into
inferred executable evidence.

## 3. Published Bundle

| Property | Accepted value |
| --- | --- |
| Path | `/srv/quanthus/portable-ads/recent_research/strategy-replay-recent-research-8550579-v1` |
| Bundle content digest | `sha256:26201b010d01a59c40532bb929ebed277d18a1c9914161d1b6831f1c37826cbf` |
| Manifest SHA-256 | `a271a0b7e75be98d81b72f7611b33ccd471b805eae7685375d21bc9bcc01f3e1` |
| READY SHA-256 | `1819c5a9116f38aad579f937c5f56b8166c992d6ec342bd58e891b008795f883` |
| PIT-map digest | `sha256:21be5568aeedcf2ed2c6ad401e56bf5a59a6845a8944c21b1c2752cf6414a7a8` |
| Total bytes | 555,649,053 |
| Semantic tables | 7, each exposed through `manifest.json` |
| Reader role | `development_research` |

The seven tables are daily unadjusted OHLCV, daily universe, price-limit/execution evidence,
security-code validity, trading calendar, normalized corporate actions, and the matching
corporate-action exception ledger. The bundle also contains the checksum-bound CSI 300 curve and
static concentration-only industry mapping required by the replay profile.

The formal tree has mode `0750` on its scope and bundle directories and `0640` on its files, with
group `ubuntu`. An `ubuntu` probe could traverse and read `READY` but could not write it. No
`CURRENT` file or symlink exists in the `recent_research` scope; every consumer must name the exact
bundle.

## 4. Replay Audit

The historical snapshot, pre-publication ADS, and formal `/srv` ADS audits produced identical date
boundaries and counts. The formal report has file SHA-256
`a0346f502469f972fd5e955ddc29fd84c984070766c496ab9c77698590e41ed1` and internal audit digest
`sha256:c5cd1a36adff86c26c2742c401e0cb4be7a9aa03704ac952d43378a3edb4af33`.

| Audit measure | Count |
| --- | ---: |
| Frames | 134 |
| Securities | 5,220 |
| Decision observations | 696,353 |
| Execution evidence rows | 696,238 |
| Corporate actions | 3,371 |
| Corporate-action blocked pairs | 15,510 |
| Non-executable observations | 17,646 |
| Suspended evidence | 1,693 |
| Missing bars | 5,564 |
| Missing execution evidence | 134 |
| Unexplained price transitions | 106 |
| Invalid bars | 0 |

The reader retained missing, non-executable, exception, and unexplained-transition observations as
fail-closed evidence. The final report states `ready_for_strategy_replay=true` and
`execution_authorized=false`; readiness does not turn those observations into permitted fills.

The retained evidence root contains the three audit reports plus exact copies of the formal
`manifest.json` and `READY`. Their copy hashes match the published files.

## 5. Preservation And Residual Gate

Publication created a new `recent_research` scope and bundle id. It did not append to or overwrite
the accepted development, arbitration, or 2025 migration bundles. As a preservation check, the
development bundle retained manifest SHA-256
`9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628` and READY SHA-256
`a51bd1df71dd1825c071d2bf8199b12ca00657ae7f4acaf0af31aa85f63f3d35`, matching its prior
acceptance record. No snapshot, revision, ADS, pointer, or legacy warehouse data was deleted.

This closes only the requested 2026 historical ADS backfill. Catalog-native publication, the first
scheduled dual-lane cycle, full candidate-family qualification, independent restore evidence,
consumer release-set cutover, and all cleanup authority remain open or blocked under Issue #45.
