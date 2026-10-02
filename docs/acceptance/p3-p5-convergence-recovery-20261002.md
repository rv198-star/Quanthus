# P3/P5 recovery and engineering evidence — 2026-10-02

This is the preserved first recovery checkpoint. Its input-location-only next action was superseded
by `p3-p5-bounded-closeout-20261002.md`, which records subsequent inventory, synthetic adapter work,
and the allowed early-closeout path. Do not interpret this older checkpoint as current routing.

Disposition: `ENGINEERING_RECOVERY_INPUT_LOCATION_BLOCKED`. This is not P3 B-R1/B acceptance,
P5 B/C/D acceptance, a terminal research conclusion, or permission to start P3 D / P5 E.

## Authority and source

The owner delegated technical execution and convergence of P3/P5 on 2026-10-02. The delegation
covers routine choices within the existing frozen requests and budgets, including valid negative
results. It does not authorize changing their thresholds, protected partitions, sources, or
production/trading boundaries. Daily ODS/DWD work and P6 remain separate.

The source baseline is main `be96a7cb3b2b7e41937aef7e5b19ab5c5fbee9f1`. Open PR #419 at
`bb28a679ff567ea8ed44e2aa52859c982b285d0c` was continued in an isolated worktree and merged with that
main. The owner's supplied instructions prohibit OCI access; conflicting later repository wording
was not used to access OCI. Remote observations below used NexusAgent only.

## P3 input-location evidence

- Live #412 is open and blocked. #319 remains the preserved no-pass input qualification; there is
  no accepted B input/candidate digest and D/E remain blocked.
- CN01 has `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1/manifest.json`, 18,629,316 bytes,
  SHA `541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`, matching its READY and
  the frozen Base identity. The manifest reports 5,201 company-industry memberships and 102
  industry nodes. These are metadata counts, not a coverage diagnosis or market-row scan.
- CN01 lacks `/data/event-runs` and both known Docker volume names
  `quanthus-platform_quanthus-event-runs` / `quanthus-event-runs`. Its inspected Quanthus roots
  contain data-lane products, not the retained failed candidate or research bundles.
- Hermes host paths did not locate the assets; Docker volume inspection returned permission denied.
  This is an unverified location, not evidence that its volumes are empty.
- CN02 is a new node with no deployed Quanthus assets. No placeholder diagnostic Job was submitted
  there to manufacture an unavailable receipt.
- No permitted copy of
  `.staging/job_313b915e82fd4865b32c1222c5b094f2/1/p3-strat-003-input/candidate-daily.parquet`
  has been verified. Normal platform recovery archives exclude failed `.staging` content.

These observations do not prove global deletion. A formal `DIAGNOSTIC_INPUT_UNAVAILABLE` artifact
was not produced. The next action is locating an already retained copy on a permitted node, then
verifying it through the frozen diagnostic. No candidate reconstruction, source collection,
accepted-predecessor rerun, formal B retry, result-bearing read or P6 mutation occurred.

## P5 reference and compute evidence

NexusAgent reported RX6800 offline. Its frozen `~/quanthus-data/training` source/correction and
reference roots have no verified migration copy on the inspected permitted nodes. The CN01 Base
identity alone is not the P5-specific 2012-2021 source/reference manifest. Market rows read: zero.

Repository metadata gives these candidate reference inventories, which must not be confused with
new P5 qualification:

| Source | Published inventory | P5 limit |
| --- | --- | --- |
| P014/P015 atomic review | 252 cases; order 33 positive / 172 negative / 47 ambiguous | same-origin simulated blind roles; source/exposure/target audit required |
| P020-C downside lifecycle | 187 rows / 179 identity clusters; Stage12 76 positive / 73 negative; Stage1 30 positive / 41 negative | preserve historical no-pass and exposure; audit against new targets and partitions |
| Stage3/4 pilot | synthetic development consensus | explicitly cannot be reused as confirmatory test |
| Stage7/8/9 | ineligible in the frozen P5 contract | remain null; no resource search to fill twelve heads |

The existing P015/P020 assets individually cannot supply the four-partition positive floor of
140 per head. This does not prove all authorized 2012-2021 resources are insufficient: B may still
qualify other admissible cases within its existing 1,500-episode budget. No head is newly accepted,
and neither `DATA_LIMITED` nor `SUPPORTED_INCREMENT` is claimed.

The local Python 3.12.9 / Torch 2.14.1 CPU synthetic probe used the fixed 844,428-parameter T0 model,
batch size 32, two warmups and three measured steps. Fresh-process resume consumed batch 5 and
ended at batch cursor 6 / epoch cursor 2, exactly matching uninterrupted model, optimizer, scheduler,
sampler, RNG and loss state. Its code digest is
`sha256:efeae34202c65027e1683242329fa42b7eaa64addd8ef2bb28c73f973cf4885f`.
The uncommitted local `cpu-probe-v2.json` receipt has file SHA
`2d76c5910b9a44488dc21fcb34cb52d194a694f6d6636bc6f11f3608d6894f3f`.
This exercises synthetic forward/backward updates and reads synthetic losses; it reads no
market-based model result. It does not validate GPU, T1, real-data sampling or the final run budget.

## Engineering changes and validation

- P3 now checks frozen Base metadata and selected industry payload checksums before row scans;
  corrupt retained candidate input seals an unavailable diagnostic while identity violations still
  fail closed. The frozen request is unchanged.
- B-R1 now has its missing governing LLD row, dedicated planner membership/spec and explicit
  dependency/status tests. D/E remain blocked.
- P5 requires exactly 120 sessions; missing output, untrained heads and unavailable facts cannot
  create assertions. Re-fitting L/G cannot retain old heads or publish partial new models.
- Checkpoint restore verifies byte/input/model/code identity, sampler and epoch state before use,
  uses weights-only loading, and compares a fresh process with uninterrupted execution.
- Recovery documents supersede the obsolete CPU/adapter next actions. A-bound LLD/request/target
  bytes remain frozen; their historical A status is interpreted through the current work-item.

Clean-main `make check`: **2,865 passed / 7 skipped / 76 failed**. Final branch `make check`:
**2,925 passed / 7 skipped / 56 failed**, with no new failing test identities and 20 resolved.
The seven focused P3/P5/traceability test modules have **104 passed / 0 failed** in that final run.
Changed implementation-file Ruff, `git diff --check`, doctor, compileall and the separately invoked
planning dry run pass. Full `make check` remains failed; unrelated daily-lane, P6 source-control,
environment and data-provider failures are not silently waived or called acceptance.

The 2026-09-22 receipt says 29 focused passes while the original PR body says 30; there is no
retained command transcript here to reconcile that historical discrepancy. It is not used as the
current acceptance count. The new dated checks supersede it without rewriting the sealed evidence.

Raw logs/JUnit/probe output remain outside Git under local `artifacts/p3-p5-convergence/`. Two
separate read-only review agents inspected the final P3 and P5 engineering diffs and found no
BLOCKER/MATERIAL issue. They did not rerun the tests, inspect market rows, or accept research gates.

## Remaining work

P3 needs the retained candidate/source location. P5 needs the source correction/reference location,
then the exact manifest and readiness audit. P5 T1 admission, real-data adapters, output provenance,
and worker integration remain unfinished. No extra platform is created while those prerequisites
are unresolved. Existing source and gate evidence must be preserved when work resumes.
