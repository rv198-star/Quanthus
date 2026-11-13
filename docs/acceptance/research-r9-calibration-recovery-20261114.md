# R9 calibration recovery 002

Work item: P5-RESEARCH-R9-QUALIFY-001 / #662. Result-bearing execution: **blocked / not_evaluated**. Engineering: Draft candidate, full acceptance open.

The Owner approved an additional 90-minute window, 2026-11-13T20:00:40Z–21:30:40Z, for at most12cases/24CLIstarts/0retries, retaining the expired first window and all previous56CLIstarts. The source-bound pool and original64-company exclusion passed actual readback;12cases were selected under the frozen four-anchor algorithm.

Executed code: `5e2f1387865d439b468421c23118f6a5ef719660`. Collector elapsed38.755s, peak process-tree RSS441659392bytes, CPU affinity0/1, no resource-monitor stop. All24CLIstarts lacked opinions:23empty event files and1trace ending at turn.started. Independent QA checked all24unique case/reader slots, rejected all, and retained0TRUE/0FALSE/12UNKNOWN. Fit/update0; retry0; cumulative CLI starts80. Server acceptance, active model time and billing remain unknown.

The original collector did not stop the queue after an empty trace. The prospective repair stops queued work on an empty/incomplete trace, captures each output stream through a256KiB bounded PIPE, records exit codes/signals, drains streams on timeout, and verifies every opinion's model/request/selection/rubric/prompt binding. Global RLIMIT_FSIZE was removed because it also limits shared CLI SQLite/WAL/log files. The direct cause of this invocation's early CLI exit remains unisolated. The repaired code has not repeated this experiment.

Engineering validation:69focused regressions, changed-file ruff and Linux/Python3.12 targeted mypy pass. Full makecheck was attempted; dependency failures were retained and repaired in a dedicated environment. The final full run was interrupted for bounded closeout after1164.98s,215passed/1skipped, in the existing Tencent minute normalization path. Full engineering acceptance remains open; PR#669 stays Draft.

## Evidence and retention

CN02 retains `/srv/agentdock/quanthus-ops/research-r9-a3-20261113/calibration-recovery-002`, including request/attempt/selection/source denominator ledger,24raw receipts/events/stderr, exclusion-only QA, summary and resource probes. No leftover job process was found after collection. The108-file archive was downloaded and verified:

- Archive64218bytes, SHA256 `fe831fa49f596a7d3b414aa537a2d56b4119e6d2ef19605605af9869f2223fac`.
- Manifest SHA256 `35a27ea6a5b067b14f3056ccbe67dfb08bb867d855269eca2af2d27be0369530`.
- Selection SHA256 `39621f800a92a0ee1fab21888ab3f557f513ffc79303e52d9a1251534059cc00`.
- Raw sealed receipts SHA256 `de5c2b679cd08d2e10559fff826d957a566df6a2a2b023632f6e8bc3ecd58683`.

HF research publication remains outstanding: the available Loop3 store_report endpoint rejected this unregistered research batch's schema/identity. Its protocol and credentials were not changed. CN02 and local copies remain retained until verified replacement publication and R9 acceptance.

## Next decision

The [proposed repair tranche](../../config/planning/next-delivery-request.v1.json) limits the next allocation to2CLIstarts/20minutes, requires an exact2call startup guard and offline tests before any live call, and permits no automatic remaining22calls. Both complete strict receipts are needed before proposing further calibration work. This proposal grants no runtime authority.

Original A4, source64 and all old failures remain unchanged. R9/#662/#665, learning and consumer goals remain open; the new blocker checkpoint does not promote module maturity.
