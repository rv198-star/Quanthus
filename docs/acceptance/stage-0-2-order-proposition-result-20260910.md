# Stage 0/2 development experiment — INCONCLUSIVE closeout

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-004-C/D` |
| Governing design | `../design/stage-0-2-order-proposition.md` |
| Delivery plan | `../../config/stage-order-proposition-delivery.v1.json` |
| Execution outcome | `succeeded`; one permitted runtime-only retry completed |
| Research disposition | `INCONCLUSIVE_REFERENCE_SUFFICIENCY`; no candidate accepted |
| Acceptance scope | completed engineering execution and conservative INCONCLUSIVE closeout, not Stage qualification or a full per-trial audit |
| Review provenance | one assistant, quantitative / short-term-operation / fund-use perspectives; neither independent model reviews nor human-expert signoff |
| Review | `../reviews/stage-0-2-order-proposition-result-review-20260910.md` |

## 1. Exact runtime evidence

The fixed audited `stage_worker_status` operation was read at
`2026-09-10T07:43:12.754279+00:00` and returned the following terminal receipt:

| Binding | Value |
| --- | --- |
| Job | `stage_87d166eb3f4a4f7f858f9b4f3e238462` |
| Kind | `quanthus.stage-order-proposition-development.v1` |
| Code revision | `1878c625ad04b23793507d92bf0e3008052a5a85` |
| Job input digest | `sha256:3ab41405c2c804fcac6015aefce6717ce3c7c6bb9d5f759d80be94391dc83e33` |
| Submitted | `2026-09-10T07:31:33.966795+00:00` |
| Started | `2026-09-10T07:31:34.792374+00:00` |
| Completed | `2026-09-10T07:42:35.518779+00:00` |
| State / exit / error | `succeeded` / `0` / `null` |
| Start-to-completion elapsed | 660.726405 seconds, approximately 11 minutes |
| Run | `stage-order-proposition-development-20260910T074234Z` |
| Manifest SHA256 | `sha256:67a1159eba611ceb8af1f43c246f49acc101786e223730af6b61a9b0a8c237ac` |
| Candidate payload SHA256 | `sha256:bd38c21ff47d17921efb132cca61e49fd5afd124b4f3b30c35e8c294ac223c41` |

The runtime artifact remains on RX6800 under
`/home/qiuyu/quanthus-data/training/runs/stage-worker/stage_87d166eb3f4a4f7f858f9b4f3e238462/stage-order-proposition-development-20260910T074234Z`.
The reported `file_count=2` identifies the summary and candidate payloads, not the entire directory
including manifest and READY. No market-data rows or runtime artifact files are copied into Git.

The original Job `stage_64da9bea52984726ab32c10bfdd36d4c` remains a separate technical failure at the
2700-second limit; it is not retrospectively renamed as a semantic failure. The owner executed the
previously supplied recovery-first script and supplied this retry receipt. The original artifact
absence was not independently observed through this session's artifact reader. No third Job was
submitted, canceled, restarted or authorized during closeout.

## 2. Frozen experiment preservation

Git comparison of the original execution revision `35439da64e81f60aa50bfeb26e4a5d0531d6eb78` with the
retry revision confirmed no changes to the development config, synthetic reference panel, either
Fact contract or the Worker job specification. The exact canonical config identities remain:

| Contract | Digest |
| --- | --- |
| Development | `sha256:894a38df99419cadcc24d1ef3fd775ffd6189609f593dc8d4487c5f4f08bdb80` |
| Synthetic panel | `sha256:cd8724da8d7aad47d501a8cc848ae6210fba36cec5de0a1c568db2877b453912` |
| Trend Evolution Facts | `sha256:480db6df61901322c107da44eb41687bf4d19d2c6d1297a846a7af68cd224e03` |
| Structure Order Facts | `sha256:57e5f60e000c4f238f83b4128d2df3acb1c7766fc01bc42829aeb085bbb8491d` |

The frozen scope remains the 512-security cohort construction, 2020-2021 reporting after 2019
warm-up, 81 Stage-2 combinations followed by 27 Stage-0 combinations, and the same 2700-second
execution limit. A new Job input digest reflects the new execution revision, not new thresholds.
The runtime-only repair and fixture equivalence evidence are retained in the linked review.
This completed real run demonstrates completion within the fixed limit; the failed original run
provides no completed baseline from which to claim an exact production speedup ratio.

## 3. Observed research result

The Worker-verified terminal result reports 224,346 usable proposition observations and 160
reference contexts. All four sampling buckets realized 40 contexts. The resolved reference labels
are not the sampling buckets:

| Resolved reference | Observed | Frozen minimum | Sufficiency |
| --- | ---: | ---: | --- |
| Stage 0: structural disorder | 69 | 20 | meets count gate |
| Stage 2: orderly consolidation | 9 | 20 | FAIL; shortage 11 |
| Ambiguous | 65 | 10 | meets count gate |
| Neither | 17 | 10 | meets count gate |

There were 21 primary-policy disagreements. The evidence tier remains the historically named
`SYNTHETIC_EXPERT_CONSENSUS`: three deterministic rule policies, not independent AI or human
judgements. Nine Stage-2 references among the 160 selected contexts does not mean that only nine
orderly consolidations exist in the 224,346 observations, the 512-security cohort or the market.
The earlier morphology-profile denominator must not be silently substituted for this result.

Both selected parameter objects are `null`. The reported pair-overlap count is `null`, not zero:
there is no selected pair to test. No Stage-0 or Stage-2 candidate, accuracy conclusion, formal
Projection Contract or QUALIFIED LabelCard is accepted.

## 4. Decisive blocker and verification ceiling

The frozen `_gate` requires the minimum Stage-2 reference count for **both** searches. Therefore
`9 < 20` is a sufficient shared blocker even for hypothetically perfect precision and recall.
A local, market-data-free check invoked the unchanged gate with the actual reference counts and
hypothetical ideal scores: Stage 0 was rejected by `stage_2_reference_count_below_gate`; Stage 2
was rejected by that same reason and `definite_positive_count_below_gate`. These are logical gate
checks, not reconstructed real trial scores.

The expected null-candidate payload was also reconstructed from the unchanged publisher contract.
Its canonical SHA256 exactly matches the candidate hash in the terminal receipt. This verifies
consistency of the no-candidate content and reported binding; it is not a remote full-file read.

The installed read-only artifact operation supports older synthetic-workflow artifacts, not this
experiment's full summary. The full retained trial arrays, their individual scores, reference
membership and source manifests were not independently fetched in this closeout. The published
code retains all 81+27 trials in `summary.json`, and the Worker completed its artifact verification;
this record does not claim a second audit of those arrays. No additional score-specific failure
reason or complete per-trial acceptance is asserted. The verified aggregate necessary-condition
failure is sufficient to stop and close **without** a candidate. Detailed readback remains an
evidence-access limitation, not permission to rerun data or to infer semantic non-identifiability.

## 5. Three-perspective disposition and stop

Quantitative: close as reference insufficiency, not proof of inseparable states. Neither relaxing
hard gates nor selecting a diagnostic winner is allowed. Stage 0's own positive count is adequate,
but the coupled experiment fails its shared prerequisite; its accuracy is not certified either.

Short-term-operation: an `order_like` sampling bucket uses broader 20-session conditions, while the
reference policies additionally inspect 10-session geometry. Forty order-like samples therefore do
not guarantee twenty positive Stage-2 references. This is a plausible protocol mismatch to review
later; the current aggregate result does not prove which clause rejected each sample. Orderliness
still does not mean accumulation, future breakout or a buy signal.

Fund-use: preserve the accepted continuous Trend Evolution and Structure Order Facts and retain the
0/2 vocabulary. Do not turn unclassified observations into Stage 0, merge labels from this result,
or interpret an inconclusive discrete experiment as invalidation of its continuous inputs.

The milestone closes `INCONCLUSIVE_REFERENCE_SUFFICIENCY` under the existing stop rule. All active
implementation, data-read, reference, search and technical-retry permissions close. No third attempt,
reference supplement, grid expansion, threshold change, 2022-2024 reread, 2025 access, primary-stage
arbitration, daily schedule, strategy consumption or trading is authorized. A later reference-design
review needs a new explicit milestone and must not relabel this already-viewed development sample
as an untouched validation set.

## 6. Engineering verification

The execution revision already passed 1847 tests with 7 skips. Closeout-specific tests bind the
successful retry, frozen semantic identities, reference-insufficiency disposition, null candidate
and null pair overlap, and reject further retry/search authorization.

Final closeout checks:

- Focused runtime/Worker/proposition/planning/document tests: **85 passed**.
- `make check PYTHON=/home/ubuntu/GitHub/Quanthus/repo/.venv/bin/python`: **1861 passed, 7 skipped**
  in 233.90 seconds, including doctor, compile and planning checks. The skips require production
  artifacts or an explicit PostgreSQL test DSN and are not claimed as executed.
- Ruff and `git diff --check`: PASS after correcting a local closeout-JSON comma and test line wrap.
- Existing Catalog snapshot: exact, 758 entries and 10 pre-existing gaps; no Catalog change.

No production runtime code, frozen experiment input, RX6800 service or immutable run artifact is
changed by this closeout. The Git commit and issue readback are bound by the subsequent tool
receipts; test success is not financial validation or completion of the unavailable trial-detail
audit.
