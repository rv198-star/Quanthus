# Offline Research Bundle Root Migration

| Field | Value |
| --- | --- |
| Evidence date | 2026-07-31 UTC |
| Scope | Ten retained immutable offline strategy-replay research bundles |
| Former root | `/srv/quanthus/portable-ads` |
| Current root | `/srv/quanthus/research-bundles` |
| Mutation | Same-filesystem atomic directory rename only |
| Authority impact | None; every bundle remains derived and non-authoritative |
| Result | Accepted |

## Decision

The former root name could imply that retained offline research exports were another ADS authority.
They are not. The current store name describes the objects by their consumer role and does not use
`ADS`. The old root is absent and is not retained as a compatibility symlink.

Historical acceptance files and the accepted Issue #46 Phase A build plans keep their original
absolute paths because they are point-in-time evidence. Future plans and operator commands use the
new root. Existing bundle ids, manifest bytes, compatibility policy ids, and content identities are
unchanged.

## Atomicity and content preservation

Before and after the rename, the root had device/inode `2049:4417610`, mode `0751`, and ownership
`root:root`. `du -sb` reported `4,361,952,759` bytes at both paths. This includes 20,480 bytes of
directory entries in addition to the 4,361,932,279 bundle payload bytes recorded by the original
inventory.

All `manifest.json` and `READY` hashes matched the pre-migration values:

| Bundle | Manifest SHA-256 | READY SHA-256 |
| --- | --- | --- |
| `strategy-replay-development-43eed812-v1` | `bb8ffb35fdc1798db2c2d88005bdc1ab6d258806aab98d62c5490e4d159f4ba9` | `49034f49369a3a7b042d327d6ec3745ee3681dee9ae49ff7cc78803de34b2efc` |
| `strategy-replay-development-559dda3-v1` | `35530159c8dae2e29d33041ff01ec1276a95dd522859e1e64c4d5bf2363e5072` | `10fb6d5710fdc5ad30b5e9db021cbf08eeefca2ab331e42f991092a2aa0ac331` |
| `strategy-replay-development-31850a1-v1` | `98198ac3ecca205a03f4dc7899048cb30fc477412f15ca4d336c87315c7df08a` | `7b8434dc0c0e0a7a1f0fc5b6c15a9f925654e5d7c3f95d6161060f834e727f85` |
| `strategy-replay-development-1f1ea4d-v1` | `9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628` | `a51bd1df71dd1825c071d2bf8199b12ca00657ae7f4acaf31aa85f63f3d35` |
| `strategy-replay-arbitration-43eed812-v1` | `33af2fc4627e676e4272b488fbd15bf725ac17bd77817b0da26bb13556482b2d` | `92e4dfd8dfa82c4025db1522eaf5e309b5bcec03ee4c2a8bee8f5aa2f3a47bce` |
| `strategy-replay-arbitration-559dda3-v1` | `7e06fb7eed5598fcd9ef297e8309ad9a8858dcb18518b24a173a3789e99d572d` | `2e6d946781fed82e79649961f1be4e973ce2690b44c8370fd5f91a5dc1ef1314` |
| `strategy-replay-arbitration-1f1ea4d-v1` | `1a92209bc7fefa2b2a409df53e1a0e422fb980d4dd65fba17fcfbd995cb0f499` | `8963d6c378ae9d0e3d14e25666e0afd428424a9db3eded1e5dc1f7d54782a979` |
| `strategy-replay-permanent-blind-1f1ea4d-v1` | `79e39733f52891d80c26d177d5780030e124cfc12860fbaeb410e8c8ca566db2` | `554780459b6aa395489fdd7713d70befa0e0bcd39b52d4e9af76e0ec44d09625` |
| `strategy-replay-recent-research-8550579-v1` | `a271a0b7e75be98d81b72f7611b33ccd471b805eae7685375d21bc9bcc01f3e1` | `1819c5a9116f38aad579f937c5f56b8166c992d6ec342bd58e891b008795f883` |
| `strategy-replay-recent-research-7c99724-v3` | `d4b948b4acf58f518ec604e87876af17130d329ea3fd4cead9deacbedad2fc5b` | `3c8c7f91481bca9a5e1d15a45ebd2fe618c37c266efde31a15dd5587527d2d05` |

## Access and authority checks

- `ubuntu` can read the development and recent-research manifests but cannot read arbitration or
  permanent-blind manifests.
- `quanthus-arbitration` and `quanthus-blind` can read their respective sealed manifests.
- None of the three reader principals can modify a path below the new root.
- `/srv/quanthus/ads/operational/base/BASE_CURRENT.json` and the accepted provisional Base were not
  changed or republished.

The repository publisher now defaults to `/srv/quanthus/research-bundles` through
`QUANTHUS_RESEARCH_BUNDLE_ROOT`. A contract test rejects reintroduction of the former default or
`QUANTHUS_STRATEGY_ADS_ROOT`.
