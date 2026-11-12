# R1/R2/R4 main integration — post-merge closeout

Work item: [#633](https://github.com/rv198-star/Quanthus/issues/633).
Parent: [#608](https://github.com/rv198-star/Quanthus/issues/608).

The accepted R1/R2/R4 research assets now reside on main. PR #635 was independently
accepted and ordinarily merged on 2026-11-12T09:58:11Z. The reusable delivery and
consumer code is available from main; the fixed holding research result remains
completed / inconclusive / park under its original rules.

## Accepted identity and evidence

| Identity | Exact value |
| --- | --- |
| Main merge | `2d7a8bed39c12648b5774801e99676df6f172679` |
| First parent | `f7b1c7071ddc7fb6a1d7690789e8fd88e7c93409` |
| Audited candidate / second parent | `fef4be14b93db1af41db78a5a1dbac930a3572da` |
| Merge and candidate tree | `7d11dabab46e836afe6a6216bcab609be225b172` |
| Full-check execution closure | `37aaa47a6aa737868a3529969e22a19f43e6414c` |
| Independent merge release receipt SHA256 | `0505f3a3efd83f668fc9e03b0b04e04aad7506246d79fd035aa86f67850b8765` |

The original main and all nine research heads are ancestors of the main merge.
The publication guard fix and all original research commits retain their identities.
The full-check closure passed **9,119 tests, with 35 skipped and zero failures/errors**.
The final candidate's six documentation/ledger changes preserved the remaining closure;
107 affected governance checks and 21-issue reconciliation passed. Final independent
review additionally ran 135 checks successfully.

The [candidate report](research-main-integration-20261112.md) remains its historical
pre-merge snapshot. Its then-pending gates and the immutable import receipt are preserved;
this successor report records the accepted final result. The 48 frozen paths comprise
46 files and two retained absent paths.

## Original PR and reference disposition

| PR | Original head | Disposition after #635 |
| --- | --- | --- |
| #617 | `4d1c69b192fa344b3a8366540c1cb34cef434221` | GitHub marked merged |
| #618 | `0323b7ae8e7f8263b30f11ae715a394df470ae4b` | GitHub marked merged |
| #621 | `f84e1bb94da3f28f35a0bf192d48410efdd3346e` | Already merged in retained research chain |
| #622 | `9c944d922e59feea304df3327addb0eb96e110f5` | Already merged in retained research chain |
| #623 | `21468d7ed563930fad5df5e055a73f8a206fc204` | Already merged in retained research chain |
| #626 | `bfb16e25a101f855f941255d89d8cbc8f1b9944a` | Closed with integrated-through-#635 receipt |
| #629 | `b24f506752185885b4d91afa98deb479b212b75c` | Closed with integrated-through-#635 receipt |
| #630 | `05e3d774f49d79e93996527580233d803046cfb1` | Closed with integrated-through-#635 receipt |
| #631 | `993ce0e02f43ba9993a6280cb13b1b662fdbb0c7` | Closed with integrated-through-#635 receipt |

The nine original head branches and #635 integration branch were deleted after checking
exact head identity, main ancestry, and absence of open PR head/base dependencies.
The receipt records all ten references. Other work lines, including #616/#620,
remain with their existing owners.

## Issue and ledger reconciliation

#608's current value brief and #633 checkbox were updated. #633 was actually closed
as completed at **2026-11-12T10:12:25Z**. The subsequent 21-issue read observed only
#633 changing relative to the accepted candidate snapshot:

- state: `closed`; state_reason: `completed`;
- updated_at: `2026-11-12T10:12:25Z`;
- body SHA256: `978634b4857eb356c74722ba1b151222374a961a2b6ce2e450df2370df53717f`.

`CP-633-MAIN-CLOSED-20261112` appends this returned observation with an explicit
no-module-change rationale. The prior 142 records and original import receipt are
unchanged; the exploration ledger remains at 23 records. This follow-up updates
documentation and governance metadata only. Its exact diff and executable/config/test
closure proof accompany the follow-up PR for independent review.

## Bounded resource disposition

The task-owned `inventory-main`, `inventory-r4` and `inventory-main-f7b1` worktrees
were normally removed after verifying clean tracked/untracked state, cache-only ignored
files and no active processes. `INVENTORY-CLEANUP-RECEIPT.json` records absence after removal.

The integration worktree, `issue-633-main-closeout` branch, private acceptance runtime
and local transfer-only `publication.git` are retained for this metadata follow-up.
After independent acceptance and ordinary merge, remove them only after confirming no
unique commits, evidence or process dependencies. The final external cleanup receipt
records the actual result. Original research-owner worktrees remain with their owner.

All original logs, failed/interrupted attempts, sealed research and the published audit
bundle remain retained. The original immutable bundle is not rebuilt; post-merge receipts
are delivered in a separate supplement.

## Durable locations

CN02 evidence root:
`/srv/agentdock/quanthus-ops/r4-main-integration-20261112`.

The original audit bundle is `quanthus-pr635-audit-bundle.tar.gz` at that root and at
`/Users/william/Documents/Codex/2026-11-11/quanthus-main-merge-plan-v1-md-2/outputs/`.
It is 3,844,326 bytes, SHA256
`acff2ca40811ce9447f4c6db76b1226d98c426bb5f60b1054ce4efa70bdc6941`;
all 160 regular members were independently downloaded and hash-verified.

The `postmerge/` subdirectory contains the live issue snapshot, closed checkpoint,
PR/merge receipts, follow-up diff, closure equivalence and affected-check logs.
The original full-check log SHA256 is
`c228c1aa5e8c3404d70fc43266d1e108660becee743a9b713ec9c2f3b0cd495d`.

Production manifest:
`/etc/quanthus/p10-container-deployment.daily-base251-76b242de1752.json`,
SHA256 `38867ffe6a4c8b842af0f137429702512438d8ecc00975db30246dcb72f3a1dc`,
mode `0444`. The primary worktree remains at
`43cd2c279d1174b8142463a31d424af7af9a458f` with clean tracked state.
Integration and closeout perform no production or research execution.

Nexus task bookkeeping retains its recorded final-review schema error. Git, test,
independent-review and lifecycle receipts provide the actual completion evidence.
