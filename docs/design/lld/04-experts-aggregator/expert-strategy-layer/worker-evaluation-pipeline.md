# P3-EXPERT-002: Worker Evaluation Pipeline

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-EXPERT-002` |
| Design status | approved |
| Delivery gate | open |
| Implementation readiness | ready; `P3-EXPERT-001` and `PK-WORKER-000` are accepted |
| Runtime budget | existing PostgreSQL Worker deployment, concurrency one |

## 1. Outcome and boundary

Admit `quanthus.expert-evaluation.v1` as a durable PostgreSQL Job and publish one immutable expert
evaluation Run. API and Web remain control-plane processes. All DWD reads, observations,
settlements, scorecard construction, and optional full P2 interventions execute in the isolated
Worker attempt process.

This issue reuses the accepted queue, lease, cancellation, generation-fencing, and recovery
contracts. It does not add Redis, Celery, RabbitMQ, a Worker registry, a dedicated scheduler, a
second always-running service, or a database table containing one row per expert opinion.

## 2. Inputs and owned paths

Required inputs are a non-empty canonical set of accepted v2 evaluation specs, exact effective DWD
identity, authorized partition digest, and operation-specific bindings. A single-expert proof uses
one spec; a formal topology binds the complete set for all of its experts and mappings. Concrete
target or strategy semantics arrive from later work and are not hard-coded into the Worker.

The issue owns or may extend:

- `src/quanthus/platform/jobs.py`
- `src/quanthus/platform/worker.py`
- `src/quanthus/platform/worker_handlers.py`
- `src/quanthus/platform/models.py`
- `src/quanthus/platform/persistence.py`
- `src/quanthus/platform/migrations.py`
- `src/quanthus/expert_evaluation/runner.py`
- `src/quanthus/expert_evaluation/cli.py`
- `tests/platform/test_expert_evaluation_worker.py`
- `tests/platform/test_postgres_expert_evaluation_runs.py`

`src/quanthus/platform/worker.py` remains the deployed entry point. Its candidate-replay behavior
must remain compatible; a small static handler dispatch is extracted only as far as required to
admit the second kind.

## 3. Job contract

The canonical input reference has these exact semantic fields:

- `schema_version = quanthus.expert-evaluation-job-input.v1`;
- `operation`: `development_evaluation`, `forward_observation`, `mature_settlement`, or
  `bounded_intervention`;
- `evaluation_specs`: one or more id/version/digest/registry-key references, sorted by stable
  identity with duplicates rejected;
- optional formal-strategy id, version, card digest, and attribution-plan digest;
- exact DWD Base, corrections/increments, effective-view digest, and through date;
- authorized partition id/version/digest and inclusive decision bounds;
- ordered requested intervention ids, each already admitted by the strategy card;
- expected parent Run digests used for settlement or intervention work.

The API/CLI accepts registry identities and digests, not an arbitrary filesystem path. The Worker
resolves paths under configured roots, checks containment and checksums, and rejects drift before
opening DWD. The submission service derives the idempotency key from operation plus every semantic
input digest; it does not trust a caller-chosen key. PostgreSQL still rejects key reuse with
different canonical input bytes. For a formal strategy, the Worker rejects a missing, extra, or
duplicate spec relative to the sealed strategy card.

The existing `replay` queue is reused initially so one deployed Worker handles candidate replay and
expert evaluation serially. Static job-kind dispatch rejects every unregistered kind. Splitting a
queue or adding a Worker is a later operational decision triggered by measured contention.

## 4. Stage model

Stage is the primary progress signal; percentage is a coarse monotonic projection and never a time
estimate.

| Stage | Durable evidence |
| --- | --- |
| `binding-inputs` | registry identities, partition, DWD, and parent digests verified |
| `building-shared-projection` | one content-addressed DWD/feature projection opened or reused |
| `producing-observations` | observation chunks written to Job-local staging |
| `settling-outcomes` | only authorized mature outcomes joined; typed skip allowed |
| `building-scorecard` | sample ledger closed and registered metrics calculated |
| `running-interventions` | only predeclared P2 reruns executed; typed skip allowed |
| `verifying-artifacts` | manifest, checksums, graph bindings, and row counts verified |
| `publishing-run` | immutable directory rename and PostgreSQL registration fenced by generation |

Every skipped stage records `not_requested`, `not_mature`, `insufficient_sample`, or
`not_applicable`. A stage cannot disappear from the Run manifest.

## 5. Artifact and persistence contract

A full development Run directory is built under a Job/generation-specific staging path and
contains:

```text
run.json
evaluation-specs.json
observations.parquet
settlements.parquet
sample-ledger.json
scorecard.json
interventions/
CHECKSUMS.sha256
READY
```

Other operations use the declared subset and represent every omitted artifact in the manifest with
a typed skip reason; fake empty Parquet files are not required. `run.json` is the canonical semantic
manifest and contains no Job id, claim generation, Worker id, transport timestamp, absolute path,
or retry counter. Its content digest defines the Run id. `READY` is written last after deep
verification, and the staging directory is atomically renamed to its content-bound Run path.

PostgreSQL adds one module-scoped `platform_expert_evaluation_runs` table containing Run id, Job id,
claim generation, input digest, artifact reference, manifest digest, evaluation/strategy
identities, bounded summary JSON, and creation time. Those operational fields are outside the Run's
semantic digest. The table stores no observation, outcome, daily score, or trade rows. No mutable
active pointer is introduced in this issue.

## 6. Claim, cancellation, and recovery

- Every progress update, terminal transition, and Run registration includes the current
  `claim_generation`. Filesystem presence alone is never trusted publication authority: a stale
  attempt may leave an unregistered content-addressed directory but cannot register a Run or
  complete the Job.
- Lease renewal continues while the isolated attempt runs. Lease loss terminates the process and
  leaves only recoverable staging data.
- Cancellation is checked between stages and while reading progress. The supervisor sends TERM,
  then KILL after the existing bounded deadline. A canceled Job never publishes a new Run.
- Retry first checks for an already sealed Run with the same canonical input and semantic manifest.
  After deep verification, the current generation may adopt and register those identical bytes;
  partial, mismatched, or non-deterministic bytes fail closed. Operational attempt metadata remains
  in PostgreSQL rather than being rewritten into the Run.
- Shared projections are reusable only when their complete manifest digest matches. A cache miss
  changes cost, not results or identity.

## 7. Implementation route

1. Add exact job-input decoding, canonical spec-set closure, and static handler dispatch while
   retaining the existing Worker entry point and replay queue.
2. Implement the domain runner/CLI child process with operation-specific stage and cancellation
   events; no DWD handle is opened by the supervisor.
3. Add semantic-manifest construction, staged checksums, deep verification, content-addressed seal,
   and fenced registration/adoption.
4. Add the additive PostgreSQL Run-reference migration and persistence methods without changing
   candidate replay tables or active pointers.
5. Exercise cancellation, lease loss, retry, stale generation, orphan adoption, and two-Worker
   competition against PostgreSQL.
6. Run candidate-replay compatibility plus semantic determinism tests before admitting the new Job
   kind in deployment configuration.

## 8. Failure semantics

Input drift, sealed-partition requests, unavailable PIT semantics, unregistered metrics, invalid
parent Runs, checksum mismatch, staging path escape, incomplete sample ledgers, and partial
interventions fail the Job without a result reference. Domain reason codes pass through in bounded
form; raw paths, credentials, and row payloads do not enter PostgreSQL errors.

A single expert or subject failure is not silently dropped. The domain runner must either encode an
authorized typed sample state or fail the complete publication. Candidate replay remains available
if expert evaluation is disabled or rolled back.

## 9. Verification and acceptance

Tests must cover exact input decoding, canonical multi-spec closure, rejected missing/extra/duplicate
specs, path containment, stage/skip transitions, monotonic progress, cancellation at every stage
boundary, process crash, lease expiry, stale generation, retry recovery, two-Worker competition,
partial directory recovery, and duplicate submission. Identical semantic inputs must produce the
same Run id and byte-identical canonical artifacts even though Job ids, generations, timestamps,
and progress receipts differ. PostgreSQL integration must prove one Job produces at most one
registered Run and that no opinion-row table exists.

Acceptance artifacts are a migration inventory, Job/Run contract fixtures, isolated-process test
evidence, PostgreSQL recovery and two-Worker evidence, deterministic artifact checksums, and a
candidate-replay compatibility run. A production deployment or Web submit control is not required
to close this infrastructure issue.

## 10. Rollback

Remove `quanthus.expert-evaluation.v1` from the static admitted-kind map and stop new submissions.
The additive table and immutable Run directories remain for audit. Candidate replay continues
through the unchanged entry point. Database rollback must not drop a table that contains published
Run references.
