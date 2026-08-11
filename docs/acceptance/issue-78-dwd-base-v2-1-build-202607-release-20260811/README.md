# DWD Base V2.1 Build202607 Release

`dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18` is the accepted release of
`dwd-base-202607-v2.1` through 2026-07-31.

## Release Identity

| Field | Value |
| --- | --- |
| Package | `dwd-base-202607-v2.1` |
| Manifest SHA-256 | `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b` |
| Content digest | `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9` |
| Release run | `dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18` |
| Manifest build run | `dwd-v2-1-gpu-p100-20260810T132353Z-3156b74e-r13` |
| Build code | `5b90cc47414a3542ab65e04e703554d487cd448d` |
| Tables / rows | `33` / `455,817,962` |
| Payload bytes | `14,447,400,032` |

## Independent Audit Closure

| Audit | Status | Canonical digest | File SHA-256 |
| --- | --- | --- | --- |
| Schema and partitions | verified | `sha256:0f56725669865fa8a89ec0040b471938f5fa30f8379a321cf9d31deb1f7cbbc2` | `833a6a6c8c7e1593c300d21dbe91f40b384c27595b8b8707f58e3f4d0a3c066a` |
| Semantic contracts | verified | `sha256:4cc59ac2bb84e0715c239bb5ed700c91001d62e7bb1ebffbe99d6b60ddf0bdd9` | `3c98bbc972b10f9567df3a136c634efec8035eb685b81e1e978d2486b3e5d2a0` |
| Domain invariants | verified | `sha256:0040cc36dad224ed3bc155c85dfceb4a493126d060872ac8ffbe99b5aee875ce` | `0c06b70c27b0f8421e7326c03e9ab2c0c0f9331f64c6d20910c9a3bd3042fb25` |
| Coverage and exclusions | verified | `sha256:5bfb22c9be7193f2cff334c576d73d61bb6248ad7c9156714da09c637f5935b8` | `2c6baa7848903b2ebebb5acb63c4f09edfff13c9b3dd01a2d730fc5238a5678c` |
| Lineage authority | verified | `sha256:d7ccdcce48110234ae0ceaaab65098ef0733ccad55fb12cbb137a1b8bf13bd61` | `dd802d683d793420969d575ca4bea998fcb6ce1e4af485a7b32d23f60990a7cd` |

The rebuild wrappers, checkpointed runtime, repair path, and five candidate-audit implementations
are production repository code. Their reliability is defined by deterministic inputs, immutable
hashes, filesystem semantics, resource bounds, and tests rather than by a node name.

The predecessor `dwd-base-202607-v2` remains immutable and is the immediate rollback package.

## Runtime Promotion

GPU-P100 installed and activated the release in place on 2026-08-11 with the node-independent
promotion code at revision `eef63d1c7252ab3ed8456818f8ec5d77ba0db05e`.

| Evidence | Value |
| --- | --- |
| Previous package | `dwd-base-202607-v2` |
| Previous manifest | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` |
| Previous `BASE_CURRENT.json` SHA-256 | `sha256:229d38776f4ccb7a6dfe0aead5b5c80efe201abb4fd0d0db1228c62bf2f9973a` |
| Current package | `dwd-base-202607-v2.1` |
| Current `BASE_CURRENT.json` SHA-256 | `sha256:95ac52cff022848db2cf34a75e831910c396a956d59631ff9f77852a3a7eddf3` |
| Installed | `true` |
| Retained rollback | `dwd-base-202607-v2` |

The first installation attempt exposed WSL's `EXDEV` behavior across two mount views of the same
ext4 device. It failed before staging publication or pointer movement and cleaned its hidden
incoming directory. Revision `eef63d1` makes promotion prefer hardlinks but fall back to verified
`copy2` only for `EXDEV`; permission and other filesystem errors still fail closed. The successful
run deeply verified source, predecessor, staging, and installed package before the pointer
compare-and-swap. No `.incoming-*` directory remained after completion.
