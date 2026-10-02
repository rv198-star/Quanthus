# P5-STAGE-001 — Learned Stage Assessment Dual-Track Research

| Field | Value |
| --- | --- |
| Parent issue | #400 |
| Milestone | #12 |
| Current gate | A preserved; B source qualification and D synthetic recovery active; C partial; E/F/G NOT_RUN |
| Current status | OWNER_CONTINUATION_INPUT_QUALIFICATION_IN_PROGRESS |
| Governing design | `../design/lld/07-research-scoring/p5-stage-001.md` |
| Frozen request | `../../config/research/p5-stage-001/request.v1.json` |
| Frozen targets | `../../config/research/p5-stage-001/stage-targets.v1.json` |
| P6 relationship | parallel; not predecessor; no mutation |
| P3-STRAT-003 relationship | independent; no data-authority inheritance |

## Goal

Run a bounded P5 research comparison between the existing rule/Stage surface, simple learned baselines,
and one fixed small temporal encoder. The research question is semantic Stage assessment, not
portfolio return or trading.

The line preserves one historical vocabulary but two assessment routes:

```text
authoritative Continuous Facts
        |                     |
existing rule/projection   learned P5 assessment
        |                     |
        +---- read-only comparison/report ----+
```

Agreement is useful diagnostic evidence but is not an automatic confidence multiplier.

## Why this line exists

The Stage phase-1 closeout remains valid. P5-STAGE-001 does not restart a vocabulary-completion
campaign. It tests one concrete downstream research need: whether a learned multi-label assessment can
add reference-supported semantic identification or useful disagreement without first completing every
deterministic Stage rule.

The research can close with simple models winning, no increment, only a subset of useful heads, data
limitations, or compute limitations.

## Child sequence

| Gate | Issue | Purpose | Result/data boundary |
| --- | --- | --- | --- |
| A | #401 | Freeze value card, authority, targets, comparisons, statistics and budget | no market rows; no training |
| B | #402 | Freeze exact development inputs, readiness and references | explicit activation required |
| C | #403 | Validate CPU/GPU backend and recovery | synthetic/environment only |
| D | #404 | Implement R/L/G/T0/T1 interfaces and contracts | synthetic/engineering only |
| E | #405 | Train fixed matrix | train + development-validation only |
| F | #406 | Calibrate and evaluate once | calibration + frozen evaluation |
| G | #407 | Produce read-only dual-track report | sealed results only |
| H | #408 | Independent closeout | review only |

Dependencies stay `A -> (B,C,D) -> E -> F -> G -> H`. Completion of A does not auto-start any
successor.

## Current recovery checkpoint

The Owner approved the frozen four-source proposal on 2026-10-03 and explicitly limited this task
to temporary treatment; unified data repair belongs to the separate data workstream. Activation is
recorded in `config/research/p3-p5-temporary-source-activation.v1.json` without editing the sealed
proposal. [#471](https://github.com/rv198-star/Quanthus/issues/471) tracks original normalization
manifest/source-closure availability; [#472](https://github.com/rv198-star/Quanthus/issues/472)
tracks the 18,991 state-only slots as an investigation, not established missing bars. The 119-session
F9 sidecar is a research-package limitation, not evidence of DWD history loss. Temporary qualification
cannot close these data issues or mutate formal data. Missing original scope still prevents coordinate
and 120-session-window qualification. A governed replacement needs a frozen closure and comparison
before the temporary source exits new runs; retain prior audit evidence.

The approved identity-only subset ran at `d115ea05cbf087fcd4b1746473c3d6032b05db22` through
Nexus OCI `session-59d72502d2293e2738d16980`: both exact dimension members (541,560 bytes)
verified; 4,679 historical listing rows and 4,679 linked security-version rows retained. No listing
row completely lacks an overlapping security version. Two security IDs have multiple retained
versions; this is neither per-session uniqueness nor an established defect. Both dimensions have
knowledge_from `2026-08-03T23:59:00Z`, so no historical as-known claim follows.
Receipt SHA `69e90f8912226ab84fbf916c551fa3a498c4bd2771ba930ffd018a3996c820f7` lives in private
OCI `p3-p5-20261002/p5-census-runs/identity-d115ea0`. Each TMP was built once and reused;
3,339,491 TMP bytes were removed after the 0.725-second application run. No action or market rows
were consumed in this stage. Original normalization scope, coordinate/window and ACTIVE gates remain
unestablished; #471/#472 stay open. Full check at this revision: 3,014 passed / 7 skipped / 56 failed,
with the same 56 failure IDs as the earlier baseline. The seven new boundary tests also passed an
independent read-only review.

The Owner explicitly required continued attempts after PR #419 merged. Live #400/#402 were reopened
for bounded input integrity, schema and listing/session census; E/F remain inactive. OCI is an
available existing data node under the current Owner instructions; control remains NexusAgent.

`config/research/p5-stage-001/input-census.v1.json` freezes the exact 250 selected Base members
(120 daily bar, 120 tradability, 10 calendar), 824,617,539 bytes, for 2012–2021 before any payload
read. Complete member-list digest:
`sha256:84edfba056bf1cf1378dce3b4eaf087e1d80d4309e2c24d9356fd2ddff83c668`.
The narrow census verifies the complete selected closure before schema/row access, projects only
identity/session/provenance columns once into job-local TMP, and retains duplicates and unmatched
connections. It does not infer stable security identity, coordinate epochs, ACTIVE control, labels
or reference eligibility from tradability flags. Warehouse knowledge times remain diagnostic;
they are never backdated to manufacture public as-known evidence.

The exact census executed at `71864bcddfadfdcf5b2fa47ff56ead0fb7adb4ba` and finished in 97.32
application seconds (127.07 seconds including container orchestration). Manifest:
`sha256:119af99681a7ce2b2ee9620564b970fc5db7c1226f42c8f2fc6ca6dfd3e534de`.
There are 7,722,194 bar rows, 7,741,185 state rows and 7,829 calendar rows; 7,722,194 date-aligned
bar/state slots, 18,991 state-only slots, no duplicate business keys or orphan calendar joins.
All three sources have warehouse knowledge_from `2026-08-03T23:59:00Z`; none proves a warehouse
version known at the historical session close. No public as-known or ACTIVE qualification is inferred.
Each source projection was built once; aggregate TMP was 1,193,029,632 bytes and removed after use.
Source copies remain private immutable selected inputs, not a new Base or ADM publication.

Accepted F9 A/B manifests are present at exact OCI event-volume paths. Their identical
`source-inputs.jsonl` member digest is
`sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac`.
The bounded sidecar ran at `6c254d2053b9c28b12d1e8076d0759374d0ade22`, verifying both copies and
parsing one. It has 357 members: three securities with 119 contiguous sessions each. No source-contained
120-member identity window exists. This is not a full-population identity source or proof of global
capacity shortage. Its manifest is
`sha256:5a2a1cc24d962fb8fd5dd2b603fb7c15a20479d4b473447880b6707fd5542089`.

The registered P014/P015/P020 and second-group publication contracts were then inspected. None
retains a complete 120-session source closure: P014/P015 use 61-session windows, P020 keeps endpoint
Trend facts, and second-group publishes only summary/candidate members. The early D pilot's history
lacks daily security/listing/calendar/action identities. Matching an epoch string does not restore
that missing evidence. This finding does not establish global data unavailability.

`input-closure-proposal.v1.json` now binds two exact Base dimension members and the two accepted v3
normalized-action/exception members from the existing OCI correction. Its manifest/READY match
`4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde`; no payload was read.
The four members total 5,228,757 bytes. They physically include later years, so the proposal requires
a fixed historical date/identity projection and intersecting exception controls before consumption.
The separate 2026-10-03 activation above now permits temporary qualification of these exact members.
Raw actions and unbound correction tradability are not substituted for accepted source semantics.
Preserve the 120-session gate and ordinary ACTIVE controls. B is not accepted.

The Owner's continuation instruction also reopens D / #404 for the unfinished synthetic claim
recovery work under `engineering-continuation.v1.json`. The P5 execution and verifier inherit a
worker lease, so a surviving child blocks new work after supervisor death. A completed orphan
artifact is verified at the same revision; an incomplete artifact is retained and marked failed
without replaying a fit. Cancel admission and the terminal commit share a short state lock. Legacy
P5 claims with unknown ownership remain blocked. No existing worker has been deployed or changed.
C remains partial, T1 is not admitted, and E/F remain inactive.

The final synthetic recovery code passed independent read-only review and 135 focused tests.
Full `make check`: 2,992 passed / 7 skipped / 56 failed; all 56 failed IDs match the prior baseline,
with zero new failures. Full D acceptance is not inferred from these engineering results.

Current next action: execute the approved bounded temporary source qualification, retaining the
original-normalization-scope gate and routing formal repairs through #471/#472.

### Historical bounded closeout — 2026-10-02

The 2026-10-02 continuation completed the permitted early closeout. Conclusion:
`INCONCLUSIVE` because this generation's input and independent-reference qualification were not
established. It is not global data insufficiency or model ineffectiveness. See
`../acceptance/p3-p5-bounded-closeout-20261002.md` and
`config/research/p3-p5-closeout-20261002.v1.json`; independent H review and ordered live issue
dispositions are complete. #400 and #402–#408 are closed. No training or protected-year authority opens.

CN01 physical inventory is complete at manifest/file-property level: 120 bar files, 120 tradability
files and 10 calendars for 2012–2021; no payload opened. Exact source selection is B's authority and
does not require the old RX6800 package specifically. ACTIVE population, payload hashes and
independent reference capacity remain unknown. C/D retain tested partial synthetic engineering;
T1, real-data materialization and full Worker orphan recovery were not completed.

Historical next action (superseded): none within that closed generation. Do not interpret
any negative close as input acceptance. Reopening needs a new bounded qualified-reference/input proposal.

### Historical first recovery checkpoint — 2026-10-02

Before the continuation and corrected source-selection interpretation:

- the owner delegated technical execution and convergence of P3/P5 within their frozen contracts;
  this does not lower acceptance gates or open protected years, P6, production or trading;
- main `be96a7cb3b2b7e41937aef7e5b19ab5c5fbee9f1` was merged into the PR #419 recovery branch;
  A remains accepted and is not repeated as new progress;
- NexusAgent is available. CN01 has the exact Base manifest and matching READY at
  `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1`, SHA
  `541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`;
  this verifies metadata only, not a P5-authorized 2012-2021 effective view;
- the historical reference ledgers and correction are bound to RX6800, which Nexus reports offline.
  No verified migration copy has been found on the inspected permitted nodes. No market rows have
  been read, reference eligibility remains unknown, and B is not accepted;
- C's local CPU/T0 synthetic probe now compares fresh-process resume against uninterrupted
  execution, including model/optimizer/scheduler, RNG, sampler and epoch/batch cursors. The
  fixed-size 120-session window and checkpoint input/code identities are enforced;
- D has executable L/G and synthetic T0 primitives. T1 admission, real-data adapters, exact output
  provenance, and worker integration remain unfinished. These are not accepted research results;
- the existing P015/P020 reference assets are candidates for a P5 source/exposure audit. Their
  historical sufficiency decisions do not qualify P5 heads. Their individual counts also do not
  prove the entire authorized development population lacks capacity;
- E / #405 remains inactive. Do not use unqualified or rule-only references to start training.

Historical next action (superseded): locate a verified permitted copy of the RX6800 source/correction and reference
ledgers, then freeze the P5-specific 2012-2021 manifest before any market-row read. Preserve unknown
capacity as null. Do not provision another worker or rerun the completed synthetic probe merely to
replace this missing input evidence. See `../acceptance/p3-p5-convergence-recovery-20261002.md`.

### Historical start checkpoint — 2026-09-22

At the original successor handoff:

- live GitHub `main` and the local source both resolve to
  `8bd989dfae98acd4f28799a376ee0ca5df9162aa`; PR #416 is merged and #401 is completed;
- the owner explicitly activated B, C, and D in the active handoff, recorded without modifying A's
  sealed receipt in `config/research/p5-stage-001/successor-activation.v1.json`;
- branch `issue-p5-stage-001-next` is an independent worktree based on that source commit;
- B produced an outcome-blind preflight with all unresolved counts kept null and Stage7/8/9 kept
  ineligible; zero market rows were read because NexusAgent is absent from this tool context;
- C inventoried the actual local Apple M4 host. It is not the RX6800/WSL node, so no RX6800 result or
  offline claim is inferred; the isolated Python/CPU full-path benchmark remains next;
- D added the first synthetic contract primitives for the fixed encoder shape, partial-label BCE,
  required-seed aggregation, output precedence, parent applicability, T-only windows, and recovery
  envelope; no result-bearing training occurred;
- E / #405 remains inactive.

The originally pending CPU environment, L/G adapters and checkpoint round-trip were subsequently
implemented. They must not remain listed as unstarted work; the current checkpoint above supersedes
those next-action statements.

### Sealed A predecessor checkpoint

As of 2026-09-22:

- #401 completed as `A_ACCEPTED_PREREGISTRATION_ONLY` after the frozen contract tests and a fresh independent final review returned PASS with no BLOCKER/MATERIAL finding.
- A is allowed to change repository docs/config/tests and run synthetic/contract verification only.
- A must not read market rows, train a model, inspect protected 2022-2026 data, or mutate P6/P3-STRAT.
- The source baseline for A is `ff83909f4584a60cb3bdf6808cae39d339326336`.
- A's machine assets are:
  `config/research/p5-stage-001/request.v1.json` and
  `config/research/p5-stage-001/stage-targets.v1.json`.
- Final independent review PASS is archived externally with the #401 closeout evidence.
- The sealed A checkpoint states: "B, C, and D remain not started; each still requires separate
  explicit owner activation." That historical fact is preserved; the later bounded activation above
  is the current state.

Resume order after interruption:

1. verify live #401 and current branch/source SHA;
2. read this checkpoint, then the frozen request and target set;
3. inspect the latest A test/review receipt;
4. read the successor activation and the latest B/C/D receipt for the active gate;
5. continue only the unique next action and never rerun accepted work merely because the chat
   session changed.

## A acceptance boundary

A accepts only if all of the following hold:

- the downstream value card is concrete and research-only;
- the owner authorization is narrow, A itself remains no-market-read/no-training, and successors are
  not auto-activated;
- the twelve cards preserve layered semantics, T-only boundaries, null handling, 0/ABSTAIN/X
  separation, and Stage11 conditional probability;
- reference eligibility prevents rule-silver circular confirmation;
- R/L/G/T0/T1 attribution, seed aggregation, calibration, bootstrap, Brier, severe-error and
  multiple-comparison rules are frozen before any result;
- the sequence-model exception to the general P5 ladder is explicit and bounded;
- P6/P3-STRAT and protected years remain untouched;
- focused contract tests and repository checks pass;
- an actual isolated reviewer checks the final A diff/evidence.

A does not need a market-data manifest, model weights, GPU benchmark, label corpus, or economic result.

## Successor boundary

B's candidate scope is active only under its separately recorded bounded activation:

- 2012-2021 SSE/SZSE development only;
- exact manifest resolved and verified in B before any row read;
- only the source families in the frozen request;
- 2011 warm-up is not authorized by A;
- 2022-2026 remain closed;
- no strategy/economic result read.

C and D have separately started synthetic work. E cannot open until B/C/D are accepted.

## Stop conditions

Close or block instead of widening if progress would require:

- protected-year access;
- future evidence to confirm a T-stage label;
- current sector membership as a historical proxy;
- an unknown/unjudgeable reference treated as negative;
- the tested rule as the only confirmatory truth;
- result-driven head selection, gate lowering, replacement seed, or hyperparameter search;
- P6/P3-STRAT changes;
- production, strategy, broker, or trading authority.

## Closeout semantics

Allowed line outcomes include `SUPPORTED_INCREMENT`, `NO_INCREMENT`, `PARTIAL`,
`INCONCLUSIVE`, `DATA_LIMITED`, `COMPUTE_LIMITED`, and `INVALID_EVIDENCE`.

A itself may close as `A_ACCEPTED_PREREGISTRATION_ONLY`, `REOPEN_NOT_AUTHORIZED`, or
`INVALID_A_CONTRACT`. A green test suite is necessary evidence, not permission to overstate the
research authority.
