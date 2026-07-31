# Provisional Operational ADS Base Acceptance

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Acceptance time | 2026-07-31 UTC |
| Package | `ads-base-through-20260730-v1` |
| Package root | `/srv/quanthus/ads/operational/base/ads-base-through-20260730-v1` |
| Publishing code revision | `4a7a3d0f99150d025a0684fd93b6d129f453c38d` |
| Descriptor-mode follow-up | `72dad9e` |
| Verdict | Phase A accepted; final July Base remains open |

## Exact inputs

- Build-plan file SHA-256:
  `903403ec5d2b27a4796ba02b8b076bf0d9a35711c2165d1ac3b235c48e19063b`.
- Plan digest:
  `sha256:346b8e0e7c48e5bf4cac2f1710100494d343e81bd8687f2be3cd9f82d481bf65`.
- Corrective semantic-source v2 manifest:
  `sha256:33e51bab6e3a6aac6df36c4d34f243a8d1664a0b589461dea48222e03fd8aab2`;
  content:
  `sha256:640b778491b768430852fea573bffa909334589234d19b381cfbb7a50980de9f`;
  accepted semantic audit:
  `sha256:15a6d3b532bf1f4399a2bbda1bfa898cdae38c475d103f3fe21e63aa6b32cbb1`.
- DWD v2 manifest:
  `sha256:0f5eb81915ef3a165cea8b3656dcfb7b319038d2df9034416d1e12777b032096`;
  DWS v2 manifest:
  `sha256:4a02032ea184e4ac946babc49eafadcd279a62ffaa9480d735a0cf220afecce1`.
- DWD/DWS current descriptor file SHA-256 values remained
  `a33928eb2de3db3b41d307814f9125d26f1b45c216869e157234560d63fcf4a9` and
  `1422689f2291f6ee35fc00f790ed9c777344df23e5dac760a698638bfedef7ea`.

The build preflight proved that the current DWD/DWS descriptors still matched the plan and
cross-bound the same accepted pair before staging. Every table bound one sealed semantic source
and one part. The resulting ten-table `tables/` directory is byte-identical to semantic source v2.
It contains 42,216,212 logical rows and 556,205,901 source bytes.

`daily_market_summary` exposed one cross-layer schema-id difference during the first fast-path
attempt: its source manifest retained the DWS logical schema id while the byte-identical Parquet
file has a different Arrow serialization schema SHA. The attempt failed in 0.72 seconds before
large-table scanning and exposed no target. The accepted implementation keeps the source bytes
bound by their physical SHA and records the actual copied Arrow schema SHA in the ADS manifest.
All other source physical metadata and seven other Parquet schema hashes matched exactly.

## Rebuild and performance evidence

| Gate | Elapsed | Peak RSS | Result |
| --- | ---: | ---: | --- |
| Optimized full build A | 7.65 s | 832,748 KiB | passed |
| Optimized full build B | 7.75 s | 838,300 KiB | passed |
| Normal seal check A | 1.06 s | 111,816 KiB | passed |
| Isolated one-day replay | 13.49 s | 2,354,504 KiB | passed |
| Root end-to-end publication | 21.83 s | 2,328,996 KiB | passed |

Build A and B produced the same package content digest and byte-identical complete `tables/`
directories. Their manifest SHA values differed only because the immutable envelope retains
`built_at`; publisher revision and runtime are likewise outside logical content identity but
remain bound by the manifest file SHA. No optimized path invoked Python logical-row hashing.

The clean-path replay ran in independent mount and network namespaces with `/srv`,
`/home/ubuntu/data`, and `/run` masked. It read only the copied Base, produced one 2026-07-29
decision frame with 2026-07-30 execution context, and returned
`ready_for_strategy_replay=true`, `research_only=true`, and
`execution_authorized=false`.

## Published identity and containment

- Package content digest:
  `sha256:ed2a01f36c1a6ac5a51cb2f5feb7356971acaddddfe8ab486952648dbfd1dabc`.
- Package manifest and `READY` binding:
  `sha256:689b84d80470d72b6396ff1c1a4ab32baed215770d8042a1904e507fe99edb55`.
- `BASE_CURRENT.json` file SHA-256:
  `6238345e3bf3a46ab78a282f27b78bd573cecff6d3f9c68cc6bf32fc7c8b24f2`.
- Root and package directories are `root:root 0750`; package files and the descriptor are
  `root:root 0640`.

The descriptor was created under the explicit `absent` compare-and-swap precondition. The root
publication flow passed DWD/DWS preflight, build, seal check, bounded replay, and immutable
promotion. It changed no DWD/DWS descriptor bytes, release set, schedule, portable export,
operational daily ADS, execution authority, or consumer-visible pointer. No `GO_FOR_CUTOVER`
exists. The final July Base remains a separate build requiring exact accepted July 31 inputs.
