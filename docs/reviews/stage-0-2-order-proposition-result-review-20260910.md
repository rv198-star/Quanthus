# Stage 0/2 development review and execution observations

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-004-D` |
| Governing design | `../design/stage-0-2-order-proposition.md` |
| Review status | TECHNICAL_TIMEOUT; runtime-only retry release verified; experiment result and semantic disposition remain pending |
| Provenance | One assistant reviewing quantitative, short-term-operation and fund-use perspectives; not independent model reviews or human-expert signoff |
| Source Job | `stage_64da9bea52984726ab32c10bfdd36d4c` |
| Execution revision | `35439da64e81f60aa50bfeb26e4a5d0531d6eb78` |
| Job input digest | `sha256:cd4d4511424b0c93097972b7c4c720e6755b04188a9d0df560f7ec3a474b3545` |
| Frozen development config digest | `sha256:894a38df99419cadcc24d1ef3fd775ffd6189609f593dc8d4487c5f4f08bdb80` |
| Frozen panel config digest | `sha256:cd8724da8d7aad47d501a8cc848ae6210fba36cec5de0a1c568db2877b453912` |

This is an evidence/review record, not a new runtime contract. It preserves the completed first
attempt and the frozen population, Fact contracts, reference rules, grids and hard gates. The
explicit runtime-only retry boundary is owned by the governing design and delivery plan.

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
acceptance of the pending real-data experiment or independent expert validation of Stage 0/2.
