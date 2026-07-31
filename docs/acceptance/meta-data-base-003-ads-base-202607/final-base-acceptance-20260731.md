# Final Operational ADS Base Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Acceptance time | 2026-07-31 21:10 UTC |
| Package | `ads-base-202607-v1` |
| Package root | `/srv/quanthus/ads/operational/base/ads-base-202607-v1` |
| Publishing code revision | `58b4e92b257f431e8d1270d15698defffd31435b` |
| Verdict | Phase B final July Base accepted |

## Exact inputs

- Build-plan file SHA-256:
  `66e46a69b983efac1e2964f0317039c66d31878d93337d0c38f6a4ab079b50db`;
  canonical plan digest:
  `sha256:839b0edbe14f05302faab4e99b34225de1e53caafca3ac3ccb0912a7776b4312`.
- Prior provisional Base manifest:
  `sha256:689b84d80470d72b6396ff1c1a4ab32baed215770d8042a1904e507fe99edb55`.
- Final DWD manifest:
  `sha256:e220b8a60f50112f6d2c4fbae7248c810c7ab5461fc926dcd86990e4260de0fb`;
  final DWS manifest:
  `sha256:515840db7ddca8364ba8ef5fe8a3379bd09c4e848856704c9ea620599245169e`;
  recomputed pair digest:
  `sha256:7c44f82ab1b2a9a3bfc57ccc6d7aa560bf7d6b836d7f31c28f3b56ec55b53a27`.
- Post-publication DWD and DWS current-descriptor file digests:
  `sha256:a103432acf484e6089115bd532c0bdeea78113257f91c2b19f16f5048641805e`
  and `sha256:72ba2b6022a0bbe1779c7834238b78ffe65c5c8d6eb3bdb12f5a59c3c1a5a7d3`.
- Absorbed July 31 daily ADS:
  `ads-operational-20260731-061a62cfbf797045`, manifest
  `sha256:f9284d2a451911ad8b8dc7040550a1a2d6190ec6823e978f714095cb4fde2adf`,
  content
  `sha256:a4e8588dfe055790e939f4e43ddd1c0e47b0512c05b795c3972572ab7a5dfa7b`.
- Final semantic source v2 manifest:
  `sha256:db76df9a9cd717ec336d2ca133fcc18664331e1db169378213477a97edc499be`;
  content:
  `sha256:c67a36eff13a51a14eae3a8a6279900d87a5fea25421b150b64e5ff77e739330`.

The preflight ran before build, immediately before pointer promotion, and once independently after
promotion. Each run resolved the active DWD/DWS descriptors to the exact final package manifests,
verified their sealed input ledgers, and recomputed the same pair digest.

## Rebuild and table evidence

Two independently built candidates passed seal and deep logical verification. They produced the
same package content digest and byte-identical complete `tables/` directories. Their manifest
SHA-256 values are
`b5776b4fd5e2e776b5897416b19be88413e7ce016a1308f716ecc53538b46879`
and `abb387146e4cc721fabb0f615dcee8979cb144cf711e6de134c2d5abd4f8dc55`;
the envelope hashes differ because `built_at` is retained outside logical content identity.

The clean detached checkout at publishing revision `58b4e92` produced a final `tables/` directory
with no byte difference from either verified candidate. The final package contains 541,603,036
bytes and 42,231,609 declared rows:

| Table | Rows | Minimum date | Maximum date |
| --- | ---: | --- | --- |
| `benchmark_curve` | 3,539 | 2012-01-04 | 2026-07-31 |
| `corporate_action_exceptions` | 328 | 2011-06-23 | 2026-07-24 |
| `corporate_actions_normalized` | 38,505 | 2012-01-05 | 2026-07-28 |
| `daily_market_summary` | 3,783 | 2011-01-04 | 2026-07-31 |
| `daily_ohlcv_none` | 14,497,827 | 1990-12-19 | 2026-07-31 |
| `daily_universe` | 13,832,198 | 2011-01-04 | 2026-07-31 |
| `price_limit_execution_daily` | 13,830,801 | 2011-01-04 | 2026-07-31 |
| `security_code_validity` | 5,868 | 1990-12-10 | 2026-07-24 |
| `static_industry_mapping` | 1,687 | not dated | not dated |
| `trading_calendar` | 17,073 | 2011-01-01 | 2026-07-31 |

The append-only logical fingerprints bind the accepted provisional content to a non-overlapping
July 31 delta. Deep verification recomputed both components and their actual date boundaries; the
normal publisher reused those accepted fingerprints and did not rehash the complete historical row
stream.

## Published identity and performance

- Package content digest:
  `sha256:d46a5576ca2e20488177e602a2ab390b9a9f4dc448de6fb250f0b001239412d5`.
- Package manifest SHA-256:
  `sha256:73872631b47f0f36882dc70a5f586f3776dfdaae6e4dcb8ee0647905ebe56375`.
- `READY` file SHA-256:
  `982fd4b60308400defa71646461e39e9552fdbda572b27ae1b0c8ee25ccfcbe0`.
- Final `BASE_CURRENT.json` file SHA-256:
  `8e27fe20fead7755c62b3987f645901bf8f53e5b809232760ef12d3c51b58fb9`.
- Root publication elapsed time: 21.95 seconds; peak RSS: 2,287,488 KiB; swaps: zero;
  exit status: zero.

The final descriptor selects `ads-base-202607-v1`, phase `final`, through date `2026-07-31`, and
the exact manifest and content digests above. Publication used the prior provisional manifest as
the compare-and-swap precondition. The provisional package and absorbed July 31 daily release both
remain sealed and retained.

## Replay and authority boundary

An independent post-promotion replay produced one 2026-07-30 decision frame with 2026-07-31
execution context. It reported 5,201 decision observations, 5,200 execution-evidence rows, zero
invalid bars, zero unexplained price transitions, and
`ready_for_strategy_replay=true`. The frame-audit digest is
`sha256:fff2b86ea6385cd5187f3edd04528f1979b42847c506fdfa99de248870cd670f`;
the access-ledger digest is
`sha256:6c3e739434833b4fe1c56b75ef2750a93066b682613d1a38a69da9a4b730beee`.

The package and replay remain `research_only=true` and `execution_authorized=false`. This
acceptance advanced only the operational historical Base administrative descriptor. It did not
publish a release set, change a consumer pointer or schedule, activate a strategy, delete retained
data, or record `GO_FOR_CUTOVER`.

## Operator sequence

The accepted root publication used:

```bash
QUANTHUS_REPO_ROOT=<clean-58b4e92-worktree> \
QUANTHUS_OPERATIONAL_ADS_PYTHON=<repository-venv-python> \
ops/bin/publish-operational-ads-base \
  <build-plan-v2.json> \
  ads-base-202607-v1 \
  sha256:689b84d80470d72b6396ff1c1a4ab32baed215770d8042a1904e507fe99edb55 \
  2026-07-30 \
  2026-07-30
```

The script enforced a clean checkout, performed both upstream preflights, built and seal-checked
the immutable package, ran the bounded replay, and advanced `BASE_CURRENT.json` under the exact
manifest compare-and-swap precondition. Independent post-promotion seal, replay, and upstream
preflight checks all passed.
