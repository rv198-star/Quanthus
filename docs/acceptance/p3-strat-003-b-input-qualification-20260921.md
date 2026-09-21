# P3-STRAT-003-B input qualification — no pass

| Field | Value |
| --- | --- |
| Work item | `P3-STRAT-003-B` / [#319](https://github.com/rv198-star/Quanthus/issues/319) |
| Parent | [P3-STRAT-003](../work-items/p3-strat-003.md) / #317 |
| Evidence date | 2026-09-21 UTC |
| Disposition | `NO_PASS_STATIC_INDUSTRY_INPUT_COVERAGE` |
| B acceptance | **Not accepted**; the bounded qualification attempt is closed with negative input evidence |
| Delivery consequence | D / #321 remains blocked; E / #322 remains blocked by D; the parent research question remains unanswered |
| Executed source revision | `e256ffb1783d964c689a116a7f870282b96da8a3` |
| Governing request | [request.v1.json](../../config/research/p3-strat-003/request.v1.json) |
| Executed input request | [input-qualification.v2.json](../../config/research/p3-strat-003/input-qualification.v2.json) |
| Code changes | [#409](https://github.com/rv198-star/Quanthus/pull/409), [#410](https://github.com/rv198-star/Quanthus/pull/410) |

## 1. Decision and claim boundary

The corrected formal B Job reached the static-industry coverage gate and failed before publishing
a candidate-input manifest, qualification summary, final input manifest or READY. Its durable
`result_ref` is null. There is no B artifact digest that may authorize D.

The exact error is:

```text
structure_mvp_child_exit_1:quanthus.expert_evaluation.serialization.ExpertEvaluationError: p3_strat_003_industry_mapping_incomplete: 10259
```

The count is **10,259 isolated candidate-event occurrences without a static-industry membership**.
The same security may contribute occurrences on multiple dates. This is not a distinct-security
count, a row count for all historical input, an economic result, or a count of missing target labels.

The frozen P4 card requires complete candidate-security coverage for its static non-PIT industry
input. The pure scanner records missing memberships for reconciliation; the physical path refuses
publication if any isolated trigger lacks one. B's guard conforms to that requirement. The general
`missing_new_entry=block_and_count` rule does not waive this separate input prerequisite.

The current frozen source binding is also demonstrably unsuitable as full-market P4 identity
authority: it reuses the older P3 development mapping, while candidate lookup uses governed DWD
security identifiers. This is a defect in the admitted input closure, not evidence that all
historical industry information is inherently unavailable. The failed receipt does not expose
distinct missing securities, their dates, or their source classifications; this review does not
claim that changing identifiers alone would solve every gap.

The existing bounded-recovery failure exit is applied: record the concrete required-input defect
and stop this B attempt. No additional formal B retry, industry-source replacement, threshold
change, window reduction, new data platform, or D result-bearing execution is opened by this
record. A later source-binding correction would need an explicit new input closure and coverage
review; it must preserve the old request and failed receipts.

## 2. Frozen identities and terminal receipt

| Identity | Value |
| --- | --- |
| A semantic digest | `sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde` |
| A request file SHA | `sha256:e1234e80857e012e2d125babba15fd4d0985cd0b38402d5737db0470d6be7c3b` |
| B-v2 request file SHA | `sha256:ac66ada85b8fd68d31c490e613389a4b2aef8029fc60ca451bae023f038ee6e6` |
| B-v2 canonical request digest | `sha256:2be7388007e9f2ef1574d378308ee7d515c3e6f1ee9fd665596dfc6a11c1f0b9` |
| Source-runtime file SHA | `sha256:b8935da7f639d67632de3aef66be50082b59dc3a1d0281f694ea121954d919ce` |
| Job | `job_313b915e82fd4865b32c1222c5b094f2` |
| Job input digest | `sha256:88dff87b93324384ea06db02cc33a4414f50f72fca955a6c36da6e9383f4e477` |
| Idempotency key | `p3-strat-003-b-v2-exact-layout-e256ffb` |
| Concurrency key | `p3-strat-003-b:input-qualification-v2` |
| Started | 2026-09-21T15:31:58.665729Z |
| Completed | 2026-09-21T15:43:14.326292Z |
| Elapsed | 675.660563 seconds, approximately 11 minutes 16 seconds |
| Worker / attempt / claim generation | `oci-event-1` / 1 / 1 |
| State / result reference | `failed` / `null` |
| Final qualification / candidate manifest digest | None; publication was not reached |

The declared decision window remains 2025-01-01 through 2026-09-17, with execution and target
cutoff 2026-09-18. Warmup authority remains exactly the final 60 common SSE/SZSE open sessions
before 2025. These are frozen request coordinates, not a claim that this failed Job sealed a
complete annual coverage audit.

The receipt was read through the fixed OCI Worker status tool and independently corroborated
through production status after termination. The coarse progress value remained 5 throughout
the child stage; it is not a percentage of completed qualification.

The source orders target and daily-chain verification, bundle construction, input audit and
candidate scanning before the failing industry guard. This establishes which code path was reached.
It does not substitute for the absent published summary, complete coverage ledger, or artifact
deep-verification receipt. Job-local intermediate paths must not be supplied to D as accepted input.

## 3. Industry contract and provenance review

The applicable [P4 rule card](../../config/p4-candidate-price-volume-transition.research.v1.json)
is bound by A with file SHA
`sha256:04207eabf00a1f4898f28918e4f846cdd91b12f23a906e574306581e471c271c`.
Its `static_industry_mapping` input requires complete candidate-security coverage and permits
industry only for static non-PIT concentration. No sector signal or historical PIT membership
claim is introduced.

The prior [P4 remediation evidence](p4-event-production-zero-candidate-remediation-20260820.md)
documents both relevant distinctions:

- the old P3 1,687-security mapping used six-digit codes, which are not the governed DWD security
  identifiers used by P4 candidate lookup;
- recording `static_industry_missing` in the pure scan does not permit physical publication of
  an incomplete candidate cohort. That fail-closed gate implements the already frozen rule.

The B source runtime binds the older mapping at
`/srv/quanthus/research-bundles/development/strategy-replay-development-1f1ea4d-v1/tables/static_industry_mapping.json`,
with file SHA
`sha256:4ee486decef111c854576b74217a228015eb463b82efe87505c9715d4e70ca31`.
The same physical binding is recorded in
[the original replay profile](../../config/strategy-replay-ads.v1.json), which binds logical digest
`sha256:8a3ce0af78706039d5423740378d102ef9b17eec05ec5ceada679143104eb45a`.
Its accepted provenance is the 1,687-security P3 development fixture documented in
[p3-governed-strategy-development-20260729.md](p3-governed-strategy-development-20260729.md).

The reused candidate projection loads that verified mapping and looks up memberships using
`universe.dwd_security_id`. The existing mapping file verification succeeded far enough for
the scan to reach the coverage guard. File integrity is therefore distinct from suitability
for this governed candidate population.

This review read the frozen configuration, the current producer and scanner code, and those
specific historical acceptance records. It did not read the underlying failed-run candidate
rows, reconstruct missing memberships, classify every missing security, or inspect any strategy
return or outcome-label distribution. The source mismatch is established; the full data-repair
scope is not established.

## 4. Completed bounded repair and preserved prerequisite

The earlier B-v2 Job `job_b36c3aa41b3745a9a05f7806cc0bee99` at revision
`340ea120be01eaab7da79e6d3396c7ff5758e2ab` failed on prior-manifest expectation
`sha256:21ba655c360a7dc50bdbae4665f94c9be3fe9452e5b34f26c763ef869356fc46`.

One fixed retained-metadata inspection was implemented in #409. Its bounded scope also permitted
reverification of the already sealed probe's consumer-payload checksums; it did not read retained
daily payloads or reconstruct packages. It executed at
`1d3e80693228580e73b3a94db247b9462f795e2e`:

| Inspection identity | Value |
| --- | --- |
| Job | `job_d31ff95f43384313a93072fcb87de184` |
| Disposition | `INPUT_IDENTITY_INSPECTION_COMPLETE` |
| Run root | `/data/event-runs/structure-runs/run_d31ff95f43384313a93072fcb87de184` |
| Manifest SHA | `sha256:4b6364cfccc7a77673312ab484f189a480585c5f59ca2eee71f5f7d872f9be41` |
| Result digest | `sha256:8be38026bc78c1540e81d193ddf529c7e13d922db626c08a4a1a78ef7892f8b2` |
| Request digest | `sha256:54cf8d8d0a5ab8f5e67c4c91aa830e09e92e03a9180d856d009a0d08a140f168` |
| Inventory observation | 2026-09-21T15:10:58.423064Z; 35 retained manifests |
| Identity match | Two occurrences, one distinct target: original 2026-08-31 package |

The target is `dwd-daily-20260831-2cb5f15cb13f-f8f8330`, retained at
`packages/2026-08-31/2cb5f15cb13f-f8f8330/manifest.json`. The 2026-09-01 child's prior binding
names the same identity. The expected SHA is not the missing 2026-08-27 boundary identity.
The returned inspection manifest's canonical bytes were independently recomputed; its 5,510-byte
manifest matched the Job receipt SHA above. The present inventory does not recreate the earlier
failed Job's inventory and did not by itself qualify retained payload bytes.

#410 corrected only B's physical path resolution to recognize the producer's short directory and
the existing full-id job-local directory. Date, package ID, manifest SHA, READY and payload checks
remain required. Existing invalid or partially transferred packages fail closed. No latest-version
selection or P6 resolver change was introduced. Tests cover the Sep1 → Aug31 → Aug28 → research
Aug27 → Aug26 chain, including preserved original child bindings and forbidden reconstruction.

The successful prior semantic probe remains unchanged:

| Prerequisite identity | Value |
| --- | --- |
| Job | `job_ab6d6afa749244299c29995968b21b84` |
| Code | `47fac8eb0af9184589dd323fdfdc3bd6d9de93c1` |
| Run root | `/data/event-runs/structure-runs/run_ab6d6afa749244299c29995968b21b84` |
| Manifest SHA | `sha256:4f6c6d5fa0fe92d9bcee2e07b1aeace9b066ba76de2ca9b8b6fdae6847029a85` |
| Result digest | `sha256:e42d6b09e215b476d73fdd33de9ad456d7310af11f9b4018d5d86e9bfa3a6869` |
| Disposition | `PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE`; two overlap sentinels |

Only the sealed 2026-08-27 research package and its one explicitly recorded original-child bridge
remain eligible under that prerequisite. Probe success is not B acceptance. Neither this probe
nor the inspection was repeated after the corrected formal B failure.

## 5. Verification and operational isolation

The #410 implementation, including the bounded manifest evidence projection, was checked before
its one formal B run:

- Full repository `make check`: 2,756 tests total, **2,749 passed, 7 skipped, zero failures and
  zero errors**. The completed JUnit report is
  `artifacts/p3-b-layout-make-check.xml` in the preserved OCI review workspace.
- Five PostgreSQL tests skipped because `QUANTHUS_TEST_POSTGRES_DSN` was not configured.
  Two production-artifact tests skipped because their expert artifacts were not mounted.
- Related input/probe/inspection/Worker checks, changed-file Ruff, `git diff --check`,
  `make doctor`, `compile` and `planning-check` passed.
- The full test process outlived one MCP foreground timeout. Its completed JUnit receipt was
  recovered and parsed; no duplicate full test run was launched to replace the missing foreground
  response.
- GitHub-hosted Actions did not obtain a runner because of the account billing/spending gate.
  This is not a remote CI pass. The completed OCI checks are the verification evidence.
- Actual separate agents read the source and configuration for code review and for the final
  contract/count/provenance review. This is independent source review, not independent cold
  reproduction of a B artifact.

Only the event-worker was deployed, to `quanthus-event:git-e256ffb`, image ID
`sha256:17ca94f45809ec0d6d40a912278753f0ebac707a74978ff17b64eacc2915510f`.
The replay-worker remains `quanthus-replay:git-6cb72eb`, image ID
`sha256:ee9235b066a5334a545ebc92f0b51da4b522769b525252c1d1947c32325dc331`.
API `git-6a6ff44` and Web `git-43d1846` remain unchanged.

Post-run production status returned health, ready and Web-ready HTTP 200. Both Workers' actual
DWD Base/correction/daily mounts remained read-only and compliant. The P6 controller remained
enabled, with first official market date null and zero official sessions/independent blocks;
these are retained P6 observations, not progress credited to B.

Read-only process/cgroup inspection at 15:39 UTC, corroborated after failure at 15:49 UTC,
observed distinct event/replay cgroups and 16 GiB `memory.max` for each. Event `memory.peak` was
6,845,812,736 bytes, approximately 6.38 GiB. Both cgroups' memory-limit/OOM event counters were zero.
P6 memory.current was approximately 40.7 MiB. These measurements do not prove zero latency impact,
absence of all resource contention, or a `memory.min` reservation.

B writes were confined to its event-job staging path by the input-only execution path and
read-only input mounts. No P6 deployment, ledger/config/pointer write or P6 Job operation was
performed in this continuation. There was no separate byte-for-byte audit of every P6 ledger file;
the evidence is the scoped operations, unchanged runtime identities and actual status/mount checks.

## 6. #319 acceptance assessment

| Criterion | Assessment at this stop |
| --- | --- |
| All required input identities, schemas, calendars and inventories qualify | **Not accepted.** The fixed chain path was repaired; static-industry coverage fails and no final input artifact is sealed. |
| Actual 2025/2026 coverage, gaps and cutoff are auditable | **Not fully evidenced.** Fixed coordinates are known, but this failed Job did not publish its annual/session gap ledger. |
| T-close / next-open and existing PIT exception handling | Existing input-only contracts are preserved; no new comprehensive PIT acceptance or actual fill claim is made. |
| Warmup, historical population and difficult-case counts | No successful full accounting artifact exists. The 10,259 occurrences do not replace distinct-security, delisting, missing-data or target-maturity counts. |
| Shared candidate closure and availability gate | **Required candidate-industry coverage failed.** Do not publish a silently reduced common cohort. Accepted C/C-R1 engineering remains preserved. |
| Only authorized technical input use | Fixed B configuration and executed source are input-only. No strategy economics, target labels or D execution were produced; this is not an OS-level per-file access-log audit. |
| Read-only input / isolated output / P6 preservation | Actual mounts, separate resource caps, event-only deployment and runtime observations support the bounded isolation claim in section 5. |
| Final B digest and C code sealed before D | **Not met.** There is no final B/candidate digest to bind. D remains blocked even when this issue is closed as a negative qualification outcome. |

The accepted C implementation remains `1fdce60801c4f8c45a3bf1b85ac08feb95d4cd72`.
C-R1 remains `e24319c272842354ba3b7caaa8e1768b8ed0c569`, with
`config/research/p3-strat-003/runtime.v1.1.json` semantic digest
`sha256:bd9422c5dcc2dbb0cb989fdea3075a76eafcbb75f4e6527b0c1d762efae49238`.
These accepted engineering identities do not create a complete A+B+C batch binding without B.

B consumed authorized technical inputs; it is not described as completely unobserved. It did not
consume the 2025 first-result-bearing D boundary. The existing 4,173 factor-visibility exceptions
are not reclassified or counted as resolved. Zero produced target labels does not mean zero
unmatured targets. No conclusion about Trend/Risk profitability, incremental value or live
performance follows from this input failure.
