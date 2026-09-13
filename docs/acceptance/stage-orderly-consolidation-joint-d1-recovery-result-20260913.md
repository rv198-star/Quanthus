# P3-STAGE-011-D1 Recovery Result — 2026-09-13

## Disposition

```text
TRANSPORT RECOVERY: SUCCEEDED
D1 ARTIFACT: VERIFIED / REVIEW READY
DEVELOPMENT CASES: 190 / 192
SHORTFALL: 2 / RETAINED, NO BACKFILL
CANDIDATE PAIRS: 27 FIXED
OPINIONS: 0
PAIR FROZEN: false
PRESERVED 2021: UNREAD
```

The single authorized protocol-budget transport recovery is Job
`stage_8189b79722fc48d1b774aa42f9238852` at revision
`bd1449c43d2eb436716f2e2125256a84463901a0`, recovering the immutable failed Job
`stage_e73f7197d1f34b879d8b31e78db800ce` only.

The Worker accepted the recovery only after matching the frozen recovery authorization. It completed
successfully and independently verified exactly one governed artifact:

- run: `stage-orderly-consolidation-joint-d1-20260913T063204Z`;
- manifest: `sha256:14870f6cd1521d453b7ccae6ebdd5d8d6d5ee64875372051f76214eb2227eae8`;
- fresh pool: 512;
- development identity capacity: 256;
- selected cases: 190 of 192 planned;
- shortfall: 2, retained without identity or quarter backfill;
- selection digest: `sha256:4be661b466b3b5dc34fbfc7ada98b0f3440a36eff8d524ac0f7a8ffe4eb1031b`;
- evidence digest: `sha256:b1298c1fc4ee40f5e50a155453c889306f4295790df7539dbe3fa3f1e51dcc9d`;
- path Facts digest: `sha256:62be0b7668bfcd4de8be51527e3aac7aa1b6592bd8db1582b7f3914c80351074`;
- threshold-basis digest: `sha256:d3556f0cb3bc91ecbe84a9febef79faeee7be131cf01f2ea3bf47b98d291412c`;
- candidate-grid digest: `sha256:6d2130ef91b8cdbec8c6c615cdd0d656142ed50d9c25dfd8ebafa14dacbe9a5d`;
- Stage0 profiles: 9;
- Stage2 profiles: 3;
- candidate pairs: 27;
- opinions: 0;
- parameter search: false;
- pair frozen: false;
- review ready: true.

The two fixed shortfalls do not violate the preregistered minimum complete denominator of 144 and
were not replaced after observation.

The exact preserved 2021 plan remains bound at 160 identities with plan digest
`sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7` and identity digest
`sha256:8bbbd7ca7110d06bfe07db6c2f555cc093b0c1a259aa666e359a637f9157b1e8`.
Its market data remains unread.

Machine result:
`config/stage-learning/orderly-consolidation-joint-d1-recovery-result.v1.json`, digest
`sha256:6ad3aebcda3c73b89e0ad3a94cdad89f78527cc1c9cd4506455fa17fd102bc8c`.

No further D1 Job, recovery, retry, supplement or D1 market-data read is authorized. The next step is
candidate-blind review of only the 190 prepared cases. Candidate-pair scoring remains blocked until that
review ledger is durably sealed. Preserved 2021, 2019/2020 consumed development, 2022-2025, official
labels, daily use, strategy and trading remain closed.
