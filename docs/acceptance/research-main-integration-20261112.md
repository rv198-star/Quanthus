# R1/R2/R4 main integration candidate — audit handoff

Work item: META-RESEARCH-INTEGRATION-001 / [#633](https://github.com/rv198-star/Quanthus/issues/633), parent [#608](https://github.com/rv198-star/Quanthus/issues/608). Aggregate [Draft PR #635](https://github.com/rv198-star/Quanthus/pull/635).

## Project effect and decision

This integration turns the separately accepted R1 delivery chain, R2 price-response assets
and R4 capital consumer into one reviewable main candidate. It also preserves main's local
acceptance tool (#595), corrected data classification (#599) and GitHub lifecycle rules
(#625). R4's accepted completed / inconclusive / park decision stays with its original
research evidence. The next action is the source research session's independent audit,
followed by an explicitly released ordinary main merge.

Engineering scope is the combined code and traceable historical import. This work reads
repository metadata and runs synthetic engineering tests. Existing research requests,
producer identities, parameters, cutoffs, HF packages and sample exposure are retained.

The investment consolidates nine original PR heads into one candidate and one complete
combined-code acceptance. Known machine times are recorded with the commands; active
human/model cost is unknown. No new research compute or Worker expansion was needed.

## Fixed identity

| Object | Commit |
| --- | --- |
| Original main / import target | `28f6081b950dcf2b271ea81e3c8b8c79bc98d75d` |
| R4 source | `993ce0e02f43ba9993a6280cb13b1b662fdbb0c7` |
| Common ancestor | `b73b37d74f26b719af08e7510eb87def3c45d18b` |
| Original ordinary merge | `ab2c07c5a5339378b00165c4aff739c1537a66d5` |

Main advanced during execution by merging #634. It was fetched from GitHub and normally
merged into the candidate, retaining all six maintenance paths. Current main base is
`f7b1c7071ddc7fb6a1d7690789e8fd88e7c93409`; the first fully tested candidate is
`faef98b0149986659e04bb42a8cc852e29f80221`, whose parents are the original ordinary merge
and this new main. The original merge still has the exact 28f/R4 parents above.
The immutable import receipt keeps its original 28f target identity and clock.
Main now contributes 21 commits; research contributes 71. The independent audit then found a missing fixed-parent guard in R1 publication.
The narrow repair commit is `37aaa47a6aa737868a3529969e22a19f43e6414c`; it changes
only the publisher and its synthetic unit tests. The final documentation/ledger follow-up
must prove unchanged executable, semantic configuration and test files against this repaired head.

## All intersecting paths and their resolution

| Path | Initial merge | Resolution and evidence |
| --- | --- | --- |
| `AGENTS.md` | Automatic | Main #625 lifecycle section and R4 batch value handoff rule both preserved. |
| `docs/README.md` | Automatic | Main local-acceptance route and R1/R2/R4 research routes both preserved. |
| `docs/work-items/index.md` | Automatic | Main metadata ownership and worktree closeout rules plus R4 bus route preserved. |
| `config/planning/learning-blueprint.v1.json` | Content conflict | Union by stable module/work-item ID; main TRACE watch paths retained; all 20 original items retained plus #633 = 21. |
| `config/planning/learning-progress.v1.json` | Content conflict | Exact main 106-record prefix followed by exact R4 34-record suffix; versioned import receipt binds both Git blobs. New #633 checkpoints append through the canonical recorder. |
| `docs/work-items/learning-blueprint-progress.md` | Content conflict | Regenerated from the validated catalog/ledger; no manual status resolution. |

`MERGE-PRESERVATION.json` proves 23 original-main-only and 85 research-only changed paths, including
deletions, preserve their respective side's bytes. The 48 checked frozen paths consist of 46 existing files with identical hashes and two
preserved absent root-level aliases (src/quanthus/daily_domain.py and
src/quanthus/daily_core_domain.py). The immutable original receipt is retained;
FROZEN-PATH-CLARIFICATION.json adds the actual tracked daily-module locations and hashes. The refreshed main contributes 28 side-only paths, all retained exactly, as recorded in
SOURCE-INVENTORY-FINAL.json. The subsequent guard repair changes only two research-side
paths, the publisher and its tests; the other 83 research-only paths keep their source bytes.
The original preservation receipts remain intact and the repair has a separate delta receipt. The exploration ledger exactly matches the source
23 records and retains main's 9-record prefix.

The original source research session independently accepted the import code/contract on
ab2c07c5: 107 focused checks and eight real-ledger rejection cases; a second read-only
role also checked consecutive imports and nine boundaries. Its signed-off scope is code
and actual-ledger preservation; final combined acceptance remains with that session.
The independently reviewed import implementation/tests/catalog/ledger are byte-identical
in faef98b0. The old receipt's then-pending text remains an immutable historical fact.
Independent receipts and harnesses are copied with hashes under `independent/`.

The [history-import design](../design/blueprint-history-import-v1.md) defines the bounded
two-clock exception. Target ledger SHA256 is
`e12b9bb70612d8c14c916c12eb34f37567e5c85b73a009d5b448ea996f8a6f20`;
source ledger SHA256 is
`a0f80c71a98ac847f3bc7a4aa50dae4b1d01721e6c1fe5aede064f0cb698742d`.
Original checkpoint IDs, objects, order and `recorded_at` remain intact.
Import `IMPORT-R4-993ce0e0-20261112` was recorded at
`2026-11-12T08:20:56.522265+00:00`.
Ordinary chronology, evidence identities, facet `supersedes`, issue observation freshness
and immutable prior receipts all remain checked.

## Publication guard finding and repair

The source audit reproduced `DatasetInfo(private=True, sha=None)` reaching
`file_exists(revision=None)`, `list_repo_files(revision=None)` and
`create_commit(parent_commit=None)` before the result-commit validator. The probe uses a
synthetic API and performs no real network request or HF publication.

The fix rejects non-string, empty and non-40-character lowercase hexadecimal heads
immediately after reading `info.sha`. Valid heads remain the exact `parent_commit`;
already-delivered packages are read back at the same fixed head without creating a commit.
Sixteen negative cases failed on the original publisher and passed after the fix.
The new idempotent case and existing normal-publication case pass; the whole publisher
module has 106 passing tests. The unchanged independent probe now reports zero subsequent
remote calls and `GuardError: immutable result repository head`.

`PUBLISH-HEAD-RED.json`, `PUBLISH-HEAD-FOCUSED.json`, `PUBLISH-HEAD-REPAIR.json` and the
original/repaired probes retain the before-and-after evidence. `docs-impact: none` for
the repair: it enforces the existing fixed-version publication contract. Historical
research requests, producer identities and sealed result packages remain as recorded.

## Engineering validation

The full `make check` on repaired commit `37aaa47a6aa737868a3529969e22a19f43e6414c` passed:
**9,119 passed, 35 skipped, zero failures or errors**, 62 warnings;
875.810 seconds including doctor, compilation and planning checks.
The 35 optional/environment skips cover external production artifacts, PostgreSQL DSN,
root-only namespace checks, astock and optional ML libraries. Exact nodes/reasons are
retained in `full-check-publish-guard/pytest.json` and JUnit.

Log SHA256: `c228c1aa5e8c3404d70fc43266d1e108660becee743a9b713ec9c2f3b0cd495d`.
Engineering receipt: `ENGINEERING-ACCEPTANCE-GUARD.json`,
SHA256 `449cd34d2237f7b7d05316be727ea39bc2c5eed5ea33ff6db25f62f5f0e43dc4`.
Tracked files were unchanged and TMP retention errors were empty during the complete run.
The earlier faef98b0 full result (9,102 passed / 35 skipped, 874.249 seconds) remains
in `full-check/` and `ENGINEERING-ACCEPTANCE.json` as a pre-repair baseline.

The corrected harness passed 95 focused blueprint/exploration/documentation cases.
Both ledgers passed append-only/change-impact validation against exact current main f7b1.
The final documentation/ledger-only follow-up is separately checked and its executable,
semantic-configuration and test closure equality against 37aaa47a recorded in
`FINAL-CLOSURE-EQUIVALENCE.json`; its final Git identity is in `CANDIDATE-FINAL.json`.

| Test inventory | Collected |
| --- | ---: |
| Current fixed main f7b1 | 8,965 |
| Fixed R4 | 9,024 |
| Pre-repair candidate faef98b0 | 9,137 |
| Repaired candidate 37aaa47a | 9,154 |
| New import contract cases | 31 |
| New publication guard/idempotent cases | 17 |

Every main node and every faef98b0 node is present. One R4 node name is intentionally superseded by main's already
accepted #599 test: `test_empty_day_requires_hashed_clocked_same_day_status_evidence`
becomes `test_empty_day_requires_hashed_clocked_same_day_status_and_daily_corroboration`.
The strengthened case preserves the status/identity checks, requires daily corroboration,
and checks both UNRESOLVED/R3 and corroborated JUSTIFIED_NO_DATA behavior.
The exact side diff and node mapping are retained; unexplained missing cases = 0.

Ruff passes for every Python file changed on either side plus the new import code/tests.
Import-module mypy passes. Mypy over the integrated production modules retains exactly
three existing R4 `no-any-return` diagnostics in `hf_dwd_research.py`
(at lines 462, 602 and 818 after the two-line repair). The baseline had four diagnostics;
the `str | None` argument diagnostic led to the reproduced missing-parent finding and is
resolved by the explicit pre-access guard. The remaining three are unchanged type-annotation
debt; new diagnostics = 0.

The isolated engineering runtime uses Python 3.10.12, pytest 8.4.2, Ruff 0.14.3,
mypy 1.18.2, DuckDB 1.5.6, PyArrow 23.0.1 and huggingface-hub 2.4.0.
Full actual versions and editable source binding are in `runtime.json` and
`runtime.freeze.txt`; sealed historical research environment locks are unchanged.

## Failures retained and corrected

- Naively appending research history reproduced `checkpoint time moved backwards`.
- Initial new positive CLI fixture omitted the catalog from its synthetic Git snapshot.
  The fixture now commits both ledger and catalog; 59 blueprint tests pass.
- The isolated runtime initially lacked build tools/Ruff executable/HF type dependency.
  Its setup logs and final package inventory record the corrections.
- First complete-check startup could not import the telemetry helper after Makefile set
  `PYTHONPATH=src`; it stopped before collection. The helper is now in the private runtime.
- Second startup lacked the parent of pytest's explicit temporary directory. It was
  stopped after the setup failure was reproduced. The driver now creates that directory,
  and the identical telemetry/TMP setup passed 95 blueprint/governance/documentation cases.

The candidate's tracked code did not change during these environment corrections.
The guard test initially exceeded Ruff's line-length limit on one parametrization line;
wrapping it fixed lint before the repair commit. The failed lint log is retained.
The first repair push timed out; one bounded HTTP/1.1 retry published the identical commit.
The subsequent valid ab2c07c5 full run was deliberately interrupted after main advanced;
its log and reason remain in `full-check-ab2c07c5-interrupted`. The first successful run targets faef98b0. A new full run in
`full-check-publish-guard` validates the repaired 37aaa47a code; baseline evidence stays intact.
Failed logs stay in `full-check-attempt1`, `full-check-attempt2`, `plugin-smoke`,
and the initial focused log; corrected smoke is `plugin-smoke-fixed`.

## Original PR disposition and retained references

| PR | Original head | Status entering integration |
| --- | --- | --- |
| #617 | `4d1c69b192fa344b3a8366540c1cb34cef434221` | Open; included in candidate |
| #618 | `0323b7ae8e7f8263b30f11ae715a394df470ae4b` | Open; included in candidate |
| #621 | `f84e1bb94da3f28f35a0bf192d48410efdd3346e` | Merged into research side |
| #622 | `9c944d922e59feea304df3327addb0eb96e110f5` | Merged into research side |
| #623 | `21468d7ed563930fad5df5e055a73f8a206fc204` | Merged into research side |
| #626 | `bfb16e25a101f855f941255d89d8cbc8f1b9944a` | Open; included in candidate |
| #629 | `b24f506752185885b4d91afa98deb479b212b75c` | Open; included in candidate |
| #630 | `05e3d774f49d79e93996527580233d803046cfb1` | Open; included in candidate |
| #631 | `993ce0e02f43ba9993a6280cb13b1b662fdbb0c7` | Open; included in candidate |

All nine original heads are ancestors of the candidate. The open stacked PRs and their
heads remain until independent audit, main merge and ancestry read-back are complete.
#633 tracks that post-merge disposition; #608 stays open; #628/R4 remain closed.
#616/#620 and other data-line changes stay with their owners.

CN02 candidate worktree:
`/srv/agentdock/worktrees/issue-633-r4-main-integration`.
Evidence and private test runtime:
`/srv/agentdock/quanthus-ops/r4-main-integration-20261112`.
The three detached inventory worktrees hold the exact main/R4 collection baselines.
The integration owner retains them for independent audit and the final merge; after that,
check tracked/untracked/ignored artifacts, unpushed commits, processes and dependent PRs
before releasing any checkout/runtime. Preserve the evidence package and source commits.

Production deployment manifest SHA256 remains
`38867ffe6a4c8b842af0f137429702512438d8ecc00975db30246dcb72f3a1dc`,
mode 0444. The primary worktree stays at
`43cd2c279d1174b8142463a31d424af7af9a458f`, with clean tracked files.

## Audit entry

Start with [PR #635](https://github.com/rv198-star/Quanthus/pull/635), this report and
`ENGINEERING-ACCEPTANCE-GUARD.json`. The CN02 evidence bundle includes commands, full logs,
JUnit, exact node IDs, runtime freeze, merge/conflict and preservation receipts, type-baseline diagnostics and the narrow guard repair, and the source session's independent preaudit artifacts.
`EVIDENCE-MANIFEST.json` binds every exported member by SHA256.

The original research session owns final independent acceptance of the current candidate.
The bounded integration-candidate handoff stops here. After that session's release,
perform an ordinary main merge, verify all original research heads are ancestors, then
update original PR dispositions and release only references/worktrees with no remaining
evidence or dependency use. #633 retains those remaining closeout tasks.

