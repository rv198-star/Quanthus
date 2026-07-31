# Operational ADS Base Inventory Preflight

| Field | Value |
| --- | --- |
| Evidence date | 2026-07-30 UTC |
| Method | read-only OCI filesystem listing, byte counts, manifest/READY inspection, and accepted issue #45 evidence review |
| Mutation | none |
| Status | preflight only; not the frozen Phase A build-plan inventory |

## Portable research exports

The root-owned `/srv/quanthus/portable-ads` tree contains ten immutable `READY` bundles totaling
4,361,932,279 bytes. Every object is a derived offline export with
`research_only=true` and `execution_authorized=false`; none is authoritative operational ADS.

| Scope | Bundle | Bytes | Preflight disposition |
| --- | --- | ---: | --- |
| development | `strategy-replay-development-43eed812-v1` | 326,436,797 | retained earlier attempt; evidence only pending final duplicate/supersession mapping |
| development | `strategy-replay-development-559dda3-v1` | 326,506,488 | retained earlier attempt; evidence only pending final duplicate/supersession mapping |
| development | `strategy-replay-development-31850a1-v1` | 326,542,084 | retained earlier attempt; evidence only pending final duplicate/supersession mapping |
| development | `strategy-replay-development-1f1ea4d-v1` | 326,551,858 | accepted portable export; evidence only until exact per-decision PIT equivalence is proved |
| arbitration | `strategy-replay-arbitration-43eed812-v1` | 472,849,810 | retained earlier attempt; evidence only pending final duplicate/supersession mapping |
| arbitration | `strategy-replay-arbitration-559dda3-v1` | 472,877,743 | retained earlier attempt; evidence only pending final duplicate/supersession mapping |
| arbitration | `strategy-replay-arbitration-1f1ea4d-v1` | 472,973,670 | accepted portable export; evidence only until exact per-decision PIT equivalence is proved |
| permanent blind | `strategy-replay-permanent-blind-1f1ea4d-v1` | 525,334,025 | accepted migration/role-isolation export; evidence only, not genuine unopened blind authority |
| recent research | `strategy-replay-recent-research-8550579-v1` | 555,649,053 | accepted earlier recent scope, retained and superseded for coverage by v3; evidence only |
| recent research | `strategy-replay-recent-research-7c99724-v3` | 556,210,751 | latest accepted portable export; evidence only until exact per-decision PIT equivalence is proved |

The accepted exact portable identities are:

| Bundle | Content digest | Manifest SHA-256 |
| --- | --- | --- |
| `strategy-replay-development-1f1ea4d-v1` | `sha256:ed799c7825c10ae1a1e6b7a722c0df82e35703a77d822a77b3d1cc2a5970c284` | `9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628` |
| `strategy-replay-arbitration-1f1ea4d-v1` | `sha256:7ecb8873bd4f32cf4cd9ef0198d39afce98618a19dc036ad608c301740cdaf62` | `1a92209bc7fefa2b2a409df53e1a0e422fb980d4dd65fba17fcfbd995cb0f499` |
| `strategy-replay-permanent-blind-1f1ea4d-v1` | `sha256:39216935777718daa6c6916d0095009ec450083634679cbf32548beb997b3dd6` | `79e39733f52891d80c26d177d5780030e124cfc12860fbaeb410e8c8ca566db2` |
| `strategy-replay-recent-research-7c99724-v3` | `sha256:449aa35caf5127390805a0d744f11f17693372611f987db6617a94872a5c4076` | `d4b948b4acf58f518ec604e87876af17130d329ea3fd4cead9deacbedad2fc5b` |

The historical portable manifests explicitly state
`capability_fixture_without_per_decision_publication_proof`. Their source knowledge cutoff is
later than historical decisions, so their bytes cannot enter a decision-visible Base merely
because schemas and values appear compatible. Final Phase A mapping must either bind exact
per-date PIT equivalence or rebuild those tables from accepted DWD/DWS/PIT inputs.

## Operational daily ADS

The current OCI dual-lane publication tree contains four complete operational daily bundles
totaling 1,241,996 bytes. Each has `market_daily`, `market_status`, and `market_summary`; each is
research-only and binds one exact canonical catalog/generation.

| Date / bundle | Bytes | Catalog | Bundle digest | Manifest SHA-256 |
| --- | ---: | --- | --- | --- |
| 2026-07-27 / `ads-operational-20260727-16ef5d44eda1f824` | 300,953 | `catalog-3e7870e9125ed2fa5dcf` | `sha256:de14327c4bbb8dd40828fe3b02cf083a067fecab9192375e21db90ee9d0bfe3d` | `bcafc91051cb2087a15ee006b20107c6908dd001d2b85b8a1e96fd90b01e5d26` |
| 2026-07-28 / `ads-operational-20260728-4598754703e65903` | 316,558 | `catalog-4f9e243a71158e86ab89` | `sha256:dd45c101db873590bf85c50f0a61d9df61c5ea79c6a7bbbb95e6203f12857c58` | `18daeb3a21ed933272bcdc01f5eb9a8225d273fc156e390df733ba8b3ce13294` |
| 2026-07-29 / `ads-operational-20260729-eb1617fa056eda49` | 313,669 | `catalog-b6a15f33c67fac070b03` | `sha256:8f736ac791eccaaa1d206c02deeb3b3e2a03f0451fc264fe53ea049c3e89710b` | `8d7b0e7226595f2c37313a351a00ce6b06e00ecca6bc2f699ff10c062e0f3a45` |
| 2026-07-30 / `ads-operational-20260730-bc696f8e9013e3a7` | 310,816 | `catalog-0e8ec5a9571e7ede6922` | `sha256:2ee4a53d61d87fd5efe253cc886e28b8569d3dbaff0e1222b44d7dc5fb49a53f` | `036bd6522ee2af565ee862adab8dd5ff17f918a943a856c3cc6d6bae7a103070` |

These small daily revisions explain the size mismatch with broad portable exports; they do not by
themselves provide historical Base coverage. They remain immutable and are not concatenated or
deleted by Issue #46.

## Publication blockers

- No real `dwd-base-through-20260730-v1` or `dws-base-through-20260730-v1` package/manifest was
  present for an exact Phase A plan. Draft policy names and dry-run digests are not substitutes.
- `/srv/quanthus/ads/operational/base/BASE_CURRENT.json` was absent; absence is not permission to
  publish without upstream and semantic gates.
- The 2026-07-31 close and accepted final DWD/DWS/daily generation were not yet available, so Phase
  B was time-blocked.
- The repository checkout contained uncommitted implementation work; the root publisher correctly
  refuses such a checkout.

No source path, digest, classification, or semantic equivalence was guessed to bypass these gates.
