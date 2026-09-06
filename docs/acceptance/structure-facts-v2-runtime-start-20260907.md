# Facts v2 B2-B5: fixed runtime qualification started

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-004`, parent issue #216; B2-B5 #219-#222 |
| Status at this checkpoint | Submitted; no runtime acceptance claimed |
| Governing design | `../design/structure-facts-v2-delivery.md` |
| Result-bearing revision | `e5b9b03b9283d4251b8fcfc7210a76df82a31389` |
| Implementation evidence | `structure-facts-v2-b2-b5-implementation-20260907.md` |
| Publication route | Exact origin/main revision, governed Event Worker deployment and normal MCP submit |
| Real run A | `job_a8900f44f8404ca2abf2c30203a7da50` |
| Real run B | `job_d1b2d3c9101648dc81967b062606d000` |
| Knowledge view | Explicit `retrospective`; no stronger historical availability claim |

## Frozen identities

Request: `config/structure-facts-v2-run.development.v1.json`

```text
request_digest:
sha256:8632ee7e6f6b301043f800db4def1085bd142b6bf861cbf35b2c4efffb180937
calculation_spec_digest:
sha256:e405f68fd1efed10c3e02cc643a35c2b479cace074c9df7bfca75ef0e0c46cc1
job input digest (identical A/B):
sha256:27f99da2d4fe79d903a93d6a461d3b0768804f3974a0e76a1b3eda4c244df694
Event image:
quanthus-event:git-e5b9b03
Event image ID:
sha256:e2e2d578e3b05b5a68afb07d983c6fb43b0300f9782fa67b3897be08cbd12cae
```

The semantic calculation specification is separate from report scope, universe and physical run
metadata. Extending a report does not alone change an already established observation identity.

## Submission evidence

A was created by normal `quanthus_worker_submit` on 2026-09-06 at 17:42:32.830054 UTC and was
observed running at `running-structure-v2`, attempt/claim generation 1/1, without error.
B was created by the same normal tool at 17:43:50.313731 UTC and was initially queued.
These are 2026-09-07 dates in Asia/Shanghai; no observation or historical market timestamp is
inferred from them. A/B have different idempotency and concurrency keys and separate Run roots.

```text
A idempotency: p3-struct-004-v2-a-e5b9b03b9283
B idempotency: p3-struct-004-v2-b-e5b9b03b9283
```

The predeclared two cold result-bearing Job slots are assigned to these Runs. Each Job must build
its own provider projection once and share it between B2-B5; no cross-Job cache substitutes for
independent construction. No further result-bearing Jobs or parameter search are part of this
checkpoint. Submitted/running/queued is not accepted.

## Publication and boundaries

The implementation passed 1528 tests with seven existing environment-dependent skips before
publication. Production moved from `9d726c1e28a6a02eab2c3d8dc7c8148f7c891a7f` to the exact result
revision through the Event-Worker-only deployment. API health and ready remained successful.
No source data, legacy Fact/indicator definitions, strategy thresholds, API routes or qualification
seeders were changed. No auto-submission fallback was needed: the current MCP accepted the new
fixed v2 run config directly.

The event-worker's inspected DWD mounts were read-only/compliant before deployment. The separate
legacy replay-worker's wider read-only mount had a pre-existing compliance flag; this checkpoint
neither changes that service nor claims all workers satisfy the narrower mount policy.

B1/#217 and B1-R/#218 retain their closed contract scope. B2-B5 and parent #216 remain open until
their actual staged evidence, same-revision reproduction, audit/P2 reconciliation and bounded
outcome-blind review are recorded. #215 and B6-B11 remain outside this batch.
