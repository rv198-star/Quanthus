# P10 container jobs through ODS/DWD completion

Work item: [P10-DATA-OPS-016 / #537](https://github.com/rv198-star/Quanthus/issues/537).
Governing design: [LLD 12](../design/lld/12-permanent-data-line.md).
Design status: approved by Owner 2026-10-22. Delivery gate: blocked pending a
qualified image and container acceptance; current host timers remain in service.

## Boundary

One pipeline image, three explicit one-shot roles: Daily, recovery and Weekly.
Daily invokes the existing `daily_base24_cycle` through source acquisition, ODS
catalog, one immutable daily core, native 5m, complete DWD verification and sealing.
It does not stop at provider success. A held 5m source reports partial delivery;
an authoritative closed date reports its event disposition. Neither is full Daily
success. Existing date locks, READY ordering and consumer/PIT gates remain active.

The Owner explicitly chose **no mail container or mail image**. The host wrapper
calls the lightweight external API after a terminal event, including Docker start
failure. Safe events and idempotent mail receipts persist outside the container.
Mail failure leaves qualified data intact; replaying mail does not invoke collection.
There is no message broker, internal cron or new scheduler. Resend is the existing
operational provider; strategy/forward-email transport is outside this issue.

The initial Weekly entry binds and verifies the already implemented financial
statement domain with its original envelopes/receipts. It reports other domains
held, never complete Weekly acceptance. Industry currently has a separate qualified
snapshot/verifier but no sealed general collection/build CLI in this change; wiring
all Weekly domains and their current acquisition is an outstanding delivery step.
Unsupported nonempty domains are rejected rather than ignored.

## Prepare and build an immutable image

Use a fresh task-owned context outside both source repositories. No untracked
source, host `.git`, credentials, venv, payload or historical run is copied. The
context generator exports only exact tracked `src` and `config`, plus the versioned
Dockerfile and hash-locked requirements. The separate collector is pinned to
`f839af0033ab4aa8cbe6d9fb933f29db88792e91`. Both code bundles verify their exact file
set, sizes and full hashes. The old host Git-checkout verification remains valid.

```bash
PYTHONPATH=src python3 -m quanthus.pipelines.securities_warehouse.container_code \
  --repo "$QUANTHUS_REPO" --revision "$QUANTHUS_FULL_SHA" \
  --collector-repo "$QUANTHUS_COLLECTOR_REPO" \
  --python-image "$PINNED_PYTHON_IMAGE" --output "$NEW_BUILD_CONTEXT"
docker build --target pipeline \
  --build-arg PYTHON_IMAGE="$PINNED_PYTHON_IMAGE" \
  --build-arg PIPELINE_REVISION="$QUANTHUS_FULL_SHA" \
  --file "$NEW_BUILD_CONTEXT/Dockerfile" "$NEW_BUILD_CONTEXT"
```

`PINNED_PYTHON_IMAGE` must be an actually verified official Python 3.10 slim
reference including its complete registry digest. No mutable default/tag fallback
is provided. Registry access/digest selection and building were not completed in
the current agentdock session: the Docker daemon socket denies this user and public
Docker registry metadata requests timed out. Do not add a user to the Docker group,
change socket ACLs or substitute another daemon to bypass this refusal.

`ops/data/runtime.lock` pins the 33 runtime packages from the actual CN02 Python
3.10 environment and published wheel hashes, excluding test/development tools. This
locks package artifacts; successful installation/ABI compatibility still requires
the image build and smoke tests. No claim that the unbuilt image is qualified.

## Deployment configuration and paths

Copy and complete `ops/data/deployment.example.json` privately; placeholders fail
closed. Pin the actual image ID (`sha256:...`) or repository digest, full code SHA
and Compose file SHA256. `.env` auto-loading and ambient Docker/Compose selection
variables are excluded. The Compose project is `quanthus-data`; all services have a
manual profile and help-only default, so a default `compose up` does no collection.

Container configuration is separate from the currently running host configuration.
Its Daily `pipeline_revision` must equal the image's bundled revision and collector
must be `/opt/collector`. Translate only these mounted locations:

| Container location | Purpose | Access |
| --- | --- | --- |
| `/data/base` | Exact accepted Base DWD | Read only |
| `/data/history` | Explicit bootstrap cores, preserving their relative layouts | Read only |
| `/data/runtime` | Existing Daily root, original captures, locks, DATES, outputs | Read/write |
| `/data/weekly-input` | Exact qualified Weekly source/receipts/config | Read only |
| `/data/weekly-jobs` | New bounded Weekly run parents | Read/write |
| `/run/config/*.json` | Separate pinned role configurations | Read only |
| `/run/events` | Small safe terminal events | Read/write |
| `/data` | Ephemeral prior-descriptor namespace, 16 MiB tmpfs | Temporary |

The Daily `prior_anchor` becomes `/data`; the prior descriptor can refer to both
mounted history and the same persisted runtime. No writable ancestor of the real
Base is mounted. No historical whole-Base copy is required. Configuration path
translation and exact predecessor binding require isolated comparison before a
real switch. Do not point an unfinished container test at writable live runtime.

The initial financial Weekly plan has kind `p10-container-weekly-financial-v1`,
`domains=["financial_statements"]`, `execution_authorized=false`, exact
`code_revision`, `through`, `config`/`config_sha256`, `base`, `source`/`source_sha256`,
`receipts`/`receipt_sha256`, `source_control`/`source_control_sha256`,
`expected_envelopes`, a new unique `job` under `/data/weekly-jobs`, and explicit
`Limits` fields. These limits include retained output, spill, verification scratch,
free-floor, RSS and elapsed budgets. An existing sealed/failed job is preserved;
reuse requires an explicit new plan/disposition, not output overwrite.

The host mail secret is an existing authorized private JSON file with `api_key`,
`sender` and `recipients`. It is never mounted into the pipeline or printed. No real
mail is sent by tests. `mail_secret` may be omitted while preparing isolated tests;
the wrapper then reports `not_configured`, not successful mail activation.

## Operation, checks and rollback

`ops/bin/run-p10-container-job ROLE --deployment FILE [--day YYYY-MM-DD]` starts a
single job. `ROLE=mail --event FILE` retries only the retained event. On ordinary
failure/timeout/termination the wrapper attempts to stop only its uniquely named
container and records a failure event. Daemon/host hard failure can prevent cleanup;
actual container/process-tree cleanup and natural timer behavior still need runtime
acceptance. No success is inferred from Docker exit zero without a matching event.

Keep current 17:30 / 21:15 Shanghai host scheduling. The template
`ops/systemd/cn02-p10-container@.service.example` is not installed or enabled. A
switch must quiesce the original writer, retain its exact service/config/environment,
verify the role and image, then replace only its ExecStart. Do not enable both
schedulers. Rollback stops the named new job, retains state/failure evidence, and
restores the prior pinned host entry after verifying no live writer remains.

Before activation: complete image build, import/CLI smoke in a non-root read-only
container, identical-input Daily semantics, supported Weekly semantics, two role
locks, stop/timeout/child cleanup, source/path isolation, mail retry/dedup with a fake
endpoint, and actual resource/space measurements. All tests must use disposable
synthetic data or separately approved isolated inputs. Containerizing the current
contract grants no new provider calls, research access, full Weekly admission or
production consumer authority. Record unavailable checks explicitly in the handoff.
