# Facts v2 B2-B5: pre-result runtime blocker history and continuation

| Field | Value |
| --- | --- |
| Parent | `P3-STRUCT-004` / #216 OPEN |
| B1 / B1-R | #217 / #218 CLOSED |
| B2-B5 | #219-#222 OPEN; implementation tested, runtime acceptance not established |
| Current production revision before next repair | `fd7224bb33abf741cd1e08cf36819881e051cd61` |
| Successful v2 result-bearing Runs | 0 |
| Current claim | `v2_runtime_accepted=false` |

## 1. What is complete

The bounded B2-B5 pipeline is implemented: explicit input attempts/admission, multi-target prior-window references, same-anchor interactions, append-only loss/reclaim process observations, direct existing expert/aggregator consumption, separate generation/publication/consumption audit evidence, existing P2 compatibility probes, immutable publication and deep verification.

The implementation and synthetic/adversarial contracts do not substitute for real input-readiness or cold A/B qualification. B2 remains the first open acceptance gate; B3-B5 cannot close ahead of it.

## 2. Real attempt ledger

No failed attempt below produced a governed v2 `result_ref`; no Facts v2 result/sample/P2 outcome was used for tuning.

| Revision | Job | Stage / disposition |
| --- | --- | --- |
| `e5b9b03b9283d4251b8fcfc7210a76df82a31389` | `job_a8900f44f8404ca2abf2c30203a7da50` | failed in Direct-DWD provider construction; DuckDB resource/spill path |
| same | `job_d1b2d3c9101648dc81967b062606d000` | failed in same provider path; this initial pair is invalid qualification evidence |
| `7b12da115724987e3e9fe22569f3f5d33fbbb22c` | `job_e4dc29deede24ad1a0080761caba0b30` | diagnostic-only; improved failure detail showed out-of-memory/offload pressure before B2 |
| `fd7224bb33abf741cd1e08cf36819881e051cd61` | `job_b1714e40e8064d0bb864753aff0a8d77` | corrected A only; bounded materialization advanced to strict provenance validation and then failed `direct DWD canonical projection receipt binding differs` |

No corrected B was submitted after corrected A failed.

## 3. Blocker sequence

### 3.1 Resource mismatch

The first runner reused a general Direct-DWD provider whose physical history/execution scope covered 2011-2021 even though the frozen Facts v2 question needs only late-2019 warm-up through 2020Q1 plus one T+1 session. Under the constrained Event Worker this caused DuckDB spill/offload pressure before any v2 fact construction.

The pre-result scope repair introduced `config/replay-data-provider.structure-v2.development.v1.json`, preserving the exact DWD Base/correction/references and the frozen research question while limiting TMP materialization to 2019-09-30 through 2020-04-01. This is recorded in `structure-facts-v2-provider-scope-repair-20260907.md`.

### 3.2 Provenance relation mismatch

The bounded provider then reached a different, correct fail-closed boundary. The existing P4 canonical projection receipt freezes the full canonical provider config/scope/history and canonical dataset identities. A bounded projection cannot truthfully satisfy that equality contract.

The bounded projection is therefore modeled as an explicit **derivative of the accepted canonical parent authority**, not as a second canonical truth and not as an equality-compatible replacement. The repair is recorded in `structure-facts-v2-bounded-derivative-parent-authority-repair-20260907.md`.

The ordinary canonical Direct-DWD path remains unchanged. Only the fixed Facts v2 runner opts into the explicit bounded derivative entry point.

## 4. Current pre-result repair contract

The corrected bounded derivative path must prove:

- exact DWD Base and correction identity using existing authority checks;
- exact accepted parent canonical receipt schema, materializer version and Base/correction binding;
- bounded provider config/scope/history and its own materialized content/physical identity;
- explicit `projection_kind=bounded_derivative`;
- parent canonical bundle digest, receipt id and receipt SHA-256 retained as lineage;
- no parent canonical identity digest assigned to the child bundle;
- no cross-Job cache and no shared A/B projection;
- independent semantic reconstruction and deep artifact verification.

This changes provenance representation only. It does not change the frozen securities, dates, 20/60 reference calculations, interactions, episode transitions, consumer mapping, P2 policy/quantity, review sample, retrospective claim ceiling or two-successful-run budget.

## 5. Exact continuation

After repository gates and publication of the provenance repair:

1. deploy the exact new Event Worker revision while keeping DWD mounts read-only;
2. submit only one corrected A with a new idempotency key and the fixed v2 run config;
3. if A fails, stop and record that exact failure before any B;
4. only if A succeeds, submit one independent same-revision B;
5. require both successful Runs to reproduce bounded provider lineage/content identity and B2-B5 semantic/calculation/consumer/P2 identities while validating each physical audit receipt independently;
6. perform the preregistered outcome-blind review;
7. close #219 -> #220 -> #221 -> #222 in dependency order, then #216, only if each child has its own real evidence.

No cleanup of immutable DWD, historical Runs, Postgres volumes, rollback images or unrelated projects is authorized by this work. No public/platform historical availability claim, economic superiority, B6-B11, model, Shadow, paper or live authority is opened.
