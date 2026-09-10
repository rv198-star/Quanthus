# Stage 0/2 development review and execution observations

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-004-D` |
| Governing design | `../design/stage-0-2-order-proposition.md` |
| Review status | CLOSED — `INCONCLUSIVE_REFERENCE_SUFFICIENCY`; technical retry succeeded, no Stage candidate accepted |
| Provenance | One assistant reviewing quantitative, short-term-operation and fund-use perspectives; not independent model reviews or human-expert signoff |
| Source Job | `stage_64da9bea52984726ab32c10bfdd36d4c` |
| Execution revision | `35439da64e81f60aa50bfeb26e4a5d0531d6eb78` |
| Job input digest | `sha256:cd4d4511424b0c93097972b7c4c720e6755b04188a9d0df560f7ec3a474b3545` |
| Frozen development config digest | `sha256:894a38df99419cadcc24d1ef3fd775ffd6189609f593dc8d4487c5f4f08bdb80` |
| Frozen panel config digest | `sha256:cd8724da8d7aad47d501a8cc848ae6210fba36cec5de0a1c568db2877b453912` |

This is an evidence/review record, not a new runtime contract. Sections 1-6 preserve the earlier
execution and repair checkpoints. The final retry result and disposition are recorded in section 7;
no historical Job, source population, Fact contract, reference rule, grid or hard gate was changed.

## 1. Evidence interpretation established before terminal result

The frozen reference panel is implemented by three deterministic threshold policies in
`src/quanthus/stage_learning/order_proposition_development.py:_review_one/resolve_references`.
`SYNTHETIC_EXPERT_CONSENSUS` is the retained schema identifier. It does **not** mean three humans or
three independent AI models reviewed the observations. Candidate/reference feature overlap makes
any selected candidate evidence of bounded agreement with a designed rule reference, not independent
validation of the financial meaning of order or disorder.

Reference policies inspect both 10-session and 20-session geometry, whereas candidate rules inspect
20-session geometry. This is an intentional difference in the frozen design. Failure must therefore
be separated into reference scarcity, inability of this candidate representation to reproduce the
reference, and actual unresolved business semantics. Failure alone proves neither that Stage 0/2
are intrinsically indistinguishable nor that they should be merged.

One reference context per security limits repeated observations from the same security. It does not
establish cross-security independence. The Wilson bound is the frozen nominal development gate;
it is not an independently calibrated production confidence statement. No external validation,
profitability or forward/as-known claim follows from it.

## 2. Three-perspective review constraints

### Quantitative perspective

Apply the original 81 Stage-2 and 27 Stage-0 grids and retain every trial, including failures.
Distinguish reference-count gates from precision, coverage, ambiguity and F1 gates. Do not select
from an ineligible diagnostic ranking. Zero selected-pair overlap is an internal consistency check,
not independent proof of semantic separability; much of the separation is explicitly designed into
the opposite width-change inequalities and different center-shift thresholds.

### Short-term-operation perspective

Stage 0 requires positive geometric instability; an unclassified observation cannot default to 0.
Stage 2 describes observable orderly consolidation, not accumulation, future breakout or a buy
setup. T-only window measurements must not be retold as knowledge of a later price move. The
10-session/20-session disagreement needs to remain visible rather than being silently resolved by
adding post-result conditions.

### Fund-use perspective

The accepted continuous Trend Evolution and Structure Order Facts remain usable research inputs
regardless of whether these two discrete propositions freeze. There is no requirement to force
coverage of every security-session with one label. Neither a selected pair nor an inconclusive
experiment authorizes allocation rules, risk actions, official LabelCards or trading.

## 3. Runtime-cost observation (synthetic fixture only)

While the RX6800 Job remained running, an isolated OCI test used the existing unit-test
`sequence(160)` fixture, B2 admission and the unchanged `build_order_observations` function. No real
market data was read and no project source or remote process was changed.

A cProfile run produced 100 order observations and 55,510 calls to `DailyInput.member_digest`.
Of the 10.609 seconds of profiled wall time, approximately 9.849 seconds were attributed cumulatively
to that property. These cumulative times overlap other call-stack totals and include profiler
overhead; they must not be extrapolated into an RX6800 completion estimate.

A separate non-profiled comparison on the same fixture measured:

| Test-process variant | Elapsed seconds | Result |
| --- | ---: | --- |
| Unchanged production functions | 3.716239 | 100 observations |
| Test-process-only memoization of each immutable input object's member digest | 0.326239 | Exactly equal observation tuple and build counts; 160 distinct member digests |

The temporary property replacement was restored before the test process ended. This is a bounded
performance hypothesis check, not an implemented fix, production benchmark or qualification.
Any later optimization must preserve exact Fact identities/source closure and receive its own tests;
this observation does not authorize hot-fixing, restarting or resubmitting the frozen Job.

## 4. First attempt and temporary readback outage

The Job was submitted at `2026-09-10T05:54:15.779025+00:00` and started at
`2026-09-10T05:54:16.705771+00:00`. The fixed audited `stage_worker_status` operation repeatedly
returned the exact revision/input identity above, `state=running`, `stage=executing`, and null
`completed_at`, `exit_code`, `error` and `result_ref`. The latest explicitly timestamped successful
observation retained here is `2026-09-10T06:20:03.666962+00:00`. The status file's own `updated_at`
remained `2026-09-10T05:54:21.100505+00:00`; it is not a live progress/worker heartbeat.

At `2026-09-10T06:23:52.034925+00:00`, the same read channel reported an SSH banner-exchange timeout.
Subsequent exact-Job reads at `2026-09-10T06:24:34.865970+00:00` and
`2026-09-10T06:30:50.436413+00:00` reported TCP/SSH connection timeouts.
A bounded OCI Tailscale status inspection reported peer `RX6800` / `100.89.43.38` as
`Online=false`, with `LastSeen=2026-09-10T06:20:00.1Z`; the OCI Tailscale backend was `Running`.
At that point these observations established a readback/connectivity blocker, not a failed or
canceled research Job. They did not establish whether computation on RX6800 continued while its
network peer was offline.

The dedicated status route remains usable when the peer is reachable. The presently installed
artifact reader exposes only older synthetic-workflow result types, not this Job's full summary.
An attempted direct read-only log inspection with the existing identity was refused locally by
OpenSSH's key-permission check. No key, permissions, remote service, checkout, Job or artifact was
changed; no alternate identity or privilege escalation was attempted. Detailed per-trial results
have not been read and must not be invented from the aggregate status.

Connectivity recovered: the audited read at `2026-09-10T06:37:36.693210+00:00` again returned the
original Job. At `2026-09-10T06:39:39.288868+00:00`, it returned terminal `state=failed`,
`error=stage_worker_wall_budget_exceeded`, `exit_code=-15`,
`completed_at=2026-09-10T06:39:21.724431+00:00`, and `result_ref=null`. The frozen 2700-second
execution budget was exhausted. This does not establish which calculation stage was reached;
remote log contents have not been read.

`P3-STAGE-004-C/D` remain unaccepted and unclosed. No reference counts, eligible-trial counts,
selected parameters or semantic INCONCLUSIVE disposition have been inferred. A null result reference
is not proof that no sealed files exist remotely. Before any retry, inspect the original Job for a
complete READY/manifest artifact and recover/verify it if present instead of duplicating execution.
Preserve the failed Job, logs and any artifacts.

## 5. Runtime-only repair and bounded retry

After the original Job reached terminal failure, `DailyInput.member_digest` was changed from a
recomputing property to an object-local `cached_property`. The exact identity formula, dataclass
fields and serialization are unchanged. Replacement input objects obtain fresh cache state. This
is not cross-Job caching or market-data materialization.

`build_order_observations` now optionally reports completed-security counts; the runner prints them
every 16 securities and at completion. The callback does not change observations or scoring inputs.
The development config, synthetic panel, Fact contracts, cohort construction, parameter grids,
gates and 2700-second limit remain unchanged.

Regression evidence is in `tests/unit/test_stage_order_runtime_cost.py`: one calculation per object,
unchanged canonical serialization, fresh identity after source/action/listing/coordinate replacement,
complete assembled observation-byte equality with the original uncached formula, and progress-output
independence. The initial focused set passed 57 tests. A post-repair, non-profiled run of the same
160-row synthetic fixture produced 100 observations in 0.324079 seconds with 160 object-local cached
digests. This remains a local fixture result, not a claim about real RX6800 run duration.

The governing design's runtime-retry section and delivery plan allow at most one additional technical
attempt, after checking for a recoverable sealed artifact. This does not reopen semantic tuning or
permit a larger time budget. The new revision must be deployed and the resulting Job bound before
runtime acceptance; no retry Job has been submitted in this checkpoint.

## 6. Repair release verification

The combined runtime/worker/proposition/document checks passed 71 tests with Ruff and
`git diff --check` passing. All four canonical development/panel/Trend/Order contract digests remain
identical to the originally frozen values; no search rule, sampling threshold or time budget changed.

The first full-repository check found one stale file-digest binding in `catalog-lineage.v1.json`
after the input module changed. Review confirmed that `admit_daily` and the declared lineage
relationships were unchanged. The source evidence digest and deterministic Catalog snapshot were
refreshed; the catalog retains 758 entries and 10 existing gaps. The 50-test focused Catalog/runtime
recheck passed and the rebuilt snapshot matched exactly.

Final `make check PYTHON=/home/ubuntu/GitHub/Quanthus/repo/.venv/bin/python` passed with
**1847 passed, 7 skipped** in 240.95 seconds. The skipped cases require production artifacts or an
explicit PostgreSQL test DSN and are not claimed as executed. These are repair-release checks, not
acceptance of the then-pending real-data experiment or independent expert validation of Stage 0/2.

## 7. Final retry readback and three-perspective closeout

At `2026-09-10T07:43:12.754279+00:00`, the fixed audited read-only status operation returned terminal
success for `stage_87d166eb3f4a4f7f858f9b4f3e238462`, revision
`1878c625ad04b23793507d92bf0e3008052a5a85`, Job input digest
`sha256:3ab41405c2c804fcac6015aefce6717ce3c7c6bb9d5f759d80be94391dc83e33`.
It ran from `2026-09-10T07:31:34.792374+00:00` to `2026-09-10T07:42:35.518779+00:00`
(660.726405 seconds), with exit code 0 and no error. Its research disposition is `INCONCLUSIVE`.

The result binds Run `stage-order-proposition-development-20260910T074234Z`, manifest
`sha256:67a1159eba611ceb8af1f43c246f49acc101786e223730af6b61a9b0a8c237ac`, and candidate
`sha256:bd38c21ff47d17921efb132cca61e49fd5afd124b4f3b30c35e8c294ac223c41`.
It reports 224,346 observations and 160 reference contexts, 40 in each preregistered sampling bucket.
Resolved labels: Stage 0=69, Stage 2=9, ambiguous=65, neither=17; primary policy disagreements=21.
Both selected parameter objects are null. Selected-pair overlap is null, not an observed zero.

### Quantitative conclusion

The common reference gate requires at least 20 Stage-2 references. Nine is insufficient and blocks
both searches, regardless of how favorable their other metrics might be. A market-data-free test
with these exact counts and hypothetical perfect scores confirms this necessary-condition failure
in the frozen `_gate`. This is not evidence that Stage 0/2 are intrinsically inseparable, nor does
it prove a Stage-0 precision/coverage failure. No candidate may be selected from diagnostic ranks.

The expected null-candidate payload reconstructed under the unchanged publisher contract hashes
exactly to the candidate SHA256 reported by the Worker. The read-only adapter still cannot retrieve
this new job kind's full summary/trial arrays. Thus no actual per-trial F1/precision, complete list
of failed metric gates, or independent source-membership audit is claimed. The inspected terminal
aggregate and frozen common gate are sufficient for conservative no-candidate closure, not full
trial-level acceptance.

### Short-term-operation conclusion

Forty `order_like` sampling contexts are not forty positive Stage-2 references. The sampling surface
uses broad 20-session geometry while reference policies additionally examine 10-session behavior.
This could contribute to the positive-reference shortage; without individual reference decisions
we cannot attribute the nine positives to a particular policy clause. In particular, nine positives
among the sampled 160 is not the total population frequency of orderly consolidation.

The future question is whether the reference protocol expresses the intended observable orderliness
and how to obtain independently judged sufficient references. It is not permission to remove the
10-session conditions, lower the count gate or amend the finished run. There is still no future
breakout, accumulation or trading implication.

### Fund-use conclusion

Retain the continuous Trend Evolution and Structure Order Fact families as accepted bounded
research inputs, and retain both 0/2 names without freezing their discrete rules. Do not force every
observation into a class or merge 0/2 based on this insufficient experiment. No allocation or risk
action is authorized by the result.

### Joint disposition

Close `P3-STAGE-004` as `INCONCLUSIVE_REFERENCE_SUFFICIENCY`, meaning completed execution and
inconclusive experiment closeout only. `runtime_accepted=true` in the existing delivery plan denotes
that completion; `development_candidate_accepted=false` and `reference_sufficient=false` prevent
confusion with Stage qualification. C/D are closed with this explicit evidence ceiling, not with a
claim that every individual trial was re-audited. All implementation/read/reference/search and
technical-retry permissions are now closed. No third execution or reference supplement occurs.

A subsequent sampling/reference-design review is a separate owner decision. It must preserve the
observed development status of this sample and the 2022-2024/2025 boundaries. Final evidence:
`../acceptance/stage-0-2-order-proposition-result-20260910.md`.
