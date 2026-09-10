# Stage 0/2 order proposition development — release ready

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-004-B` |
| Decision | `PASS_TO_SINGLE_DEVELOPMENT_RUN` |
| Runtime config digest | `sha256:894a38df99419cadcc24d1ef3fd775ffd6189609f593dc8d4487c5f4f08bdb80` |
| Synthetic panel digest | `sha256:cd8724da8d7aad47d501a8cc848ae6210fba36cec5de0a1c568db2877b453912` |
| Trend Fact contract digest | `sha256:480db6df61901322c107da44eb41687bf4d19d2c6d1297a846a7af68cd224e03` |
| Structure Order contract digest | `sha256:57e5f60e000c4f238f83b4128d2df3acb1c7766fc01bc42829aeb085bbb8491d` |

## Frozen execution

The single development Run uses the existing 512-security cohort construction, 2019 warm-up and
2020-2021 development window. It builds accepted Trend Evolution and Structure Order Facts per
security to bound memory, then samples one T-only reference context per security at most.

References are resolved before any candidate search. Stage 2 searches 81 finite combinations; Stage
0 searches 27 combinations after Stage 2 selection and receives an additional zero-overlap pair gate.
No Stage-0×Stage-2 Cartesian search is allowed.

## Claim ceiling

The Run may emit only a development proposition candidate or `INCONCLUSIVE`. It may not emit official
Stage labels, primary-stage output, daily/as-known publication, strategy or trading artifacts.
Synthetic expert consensus is not human-expert validation.

The consumed 2022-2024 arbitration partition remains closed to fresh reuse and 2025 remains
untouched. If the frozen hard gates reject either proposition, there is no in-run or post-result hot
fix inside this milestone.

## Engineering evidence

Focused tests cover positive-evidence semantics, both-false abstention, two-window reference review,
one-context-per-security sampling, precision/ambiguity gates and pair-overlap rejection. The fixed RX
Stage Worker kind is `quanthus.stage-order-proposition-development.v1` with a 2700-second wall budget.
Repository-wide validation is required before the execution revision is pushed.
