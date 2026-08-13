# Platform Kernel Runbook

This runbook operates the accepted PK1-PK4 Platform Kernel governed by ADR 0010, LLD 11, and
`config/platform-kernel-gates.v1.json`. Optional Authoring is not applicable to the first cutover;
PK6 recovery and cutover work remains open.

## Runtime Boundary

The admitted process topology is one Web process, one API process, PostgreSQL, and one Replay
Worker with concurrency one. The Web/API is control plane only and cannot execute candidate replay.
The Worker polls PostgreSQL, claims generation-fenced leases, and runs each attempt in an isolated
child process. Redis, Celery, a Worker registry, an event stream, a separate scheduler, trading
workers, MCP, AI, notification transport, and mobile clients are absent.

The API reads immutable attribution releases and candidate Runs from read-only mounts. PostgreSQL
stores users, sessions, security audit, schema history, bounded Job metadata, Run index, and active
evidence pointers. The Replay Worker alone receives read-only DWD/correction/reference mounts and a
writable candidate Run volume. Never mount warehouse write credentials or broker state into either
process.

## Preflight

1. Use a clean reviewed repository revision and install `.[platform]` or run
   `make platform-image` plus `make platform-web-image`. The repository-owned builders create exact
   temporary contexts so
   unrelated or restricted repository paths never enter the image or block a legacy Docker
   builder before `.dockerignore` is applied.
2. Generate a long random PostgreSQL password outside the repository.
3. Generate the MFA key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
4. Set an absolute, read-only attribution publication root and build the Web image from reviewed
   `web/` source. Never deploy a prebuilt upstream Web image.
5. Confirm `config/platform-features.v1.json` and `config/platform-kernel-gates.v1.json` come from
   the same reviewed revision.

Create a node-local environment from `ops/platform/platform.env.example`; never commit it.

## Clean Database Acceptance

Point `QUANTHUS_TEST_POSTGRES_DSN` at a disposable PostgreSQL database whose role may create and
drop a temporary schema, then run:

```bash
make platform-postgres-check
```

The test creates a unique schema, applies `identity-0001` and `jobs-0001`, proves a second upgrade
is a no-op, checks readiness, requires the exact five-table set including the migration ledger,
checks the exact two ledger rows, and drops the schema. A skip is not a pass.

When a host has Docker but no local PostgreSQL or Python Platform dependencies, run the isolated
acceptance stack instead:

```bash
make platform-docker-acceptance
```

This uses the fixed Compose project `quanthus-pk-acceptance-20260812`, an internal-only network,
a PostgreSQL 16 tmpfs data directory, a read-only repository mount, and no published database
port. It runs the same clean/upgrade migration assertions plus the retained HTTP workflow, writes
JSON and JUnit evidence under ignored `artifacts/platform-kernel/`, then removes only that Compose
project's containers, network, and temporary volumes. A new run removes the two prior generated
evidence files before starting, so a failed run cannot reuse stale pass evidence.

The HTTP portion launches the same one-worker Gunicorn app factory used by the production image;
requests reach it through a real loopback socket inside the read-only acceptance container. The
test does not substitute an in-process WSGI caller for this runtime gate.

## Start And Bootstrap

With the three variables in the node-local environment loaded:

```bash
make platform-image
make platform-web-image

docker compose --env-file /absolute/path/platform.env \
  -f ops/platform/compose.yaml up -d postgres

docker compose --env-file /absolute/path/platform.env \
  -f ops/platform/compose.yaml run --rm api quanthus-platform migrate

docker compose --env-file /absolute/path/platform.env \
  -f ops/platform/compose.yaml run --rm api \
  quanthus-platform bootstrap-admin --username ADMIN_NAME --password ADMIN_PASSWORD

docker compose --env-file /absolute/path/platform.env \
  -f ops/platform/compose.yaml up -d api replay-worker web
```

Use a secret carrier for the bootstrap password in production rather than shell history. The
bootstrap command is intentionally idempotent only by refusing a duplicate username; inspect the
existing operator instead of overwriting it.

## Verification

Require all of the following:

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/openapi.json
curl --fail http://127.0.0.1:8080/metrics
curl --fail http://127.0.0.1:3100/ready
curl --fail http://127.0.0.1:3100/operator
```

Then perform one login/logout, MFA enrollment/challenged-login/disable cycle, least-privilege user
creation, authenticated gate query, candidate replay submission, job poll to `succeeded`, duplicate
request-id submission, active Run/evidence verification, and logout. Confirm the OpenAPI document and Web
bundle contain no removed/deferred route, PostgreSQL contains no table outside the exact Platform
Kernel allowlist, and desktop/mobile pages have no accessibility or horizontal-overflow failure.

For OCI, keep host ports 8080 and 3100 bound to loopback. The Web service alone joins the external
`traefik-net`; Traefik publishes `https://quanthus.oci.061718.xyz`, terminates TLS through the
`letsencrypt` resolver, and forwards to Web container port 3100. PostgreSQL and API must not join
`traefik-net` or receive a Traefik router. The Web proxy keeps browser API calls same-origin and
forwards only admitted paths. Tailscale Serve may remain as a tailnet-only fallback; do not enable
Tailscale Funnel while the Traefik public edge is active. The public router excludes `/metrics`;
scrape metrics from the host-loopback or tailnet carrier instead.

Verify both the public edge and the network boundary:

```bash
curl --fail https://quanthus.oci.061718.xyz/operator
curl --fail https://quanthus.oci.061718.xyz/ready
test "$(curl --output /dev/null --silent --write-out '%{http_code}' \
  https://quanthus.oci.061718.xyz/metrics)" = 404
docker network inspect traefik-net
docker inspect quanthus-platform-api-1 quanthus-platform-postgres-1
```

The first two calls must return success, `traefik-net` must contain the Web container, and neither
API nor PostgreSQL may list that network. Treat certificate errors, a default Traefik certificate,
or an API/database container on `traefik-net` as a failed deployment.

## Failure And Recovery

Create a self-verifying recovery package only from a clean revision equal to `origin/main`:

```bash
QUANTHUS_RECOVERY_PACKAGE=/srv/quanthus/recovery/pk6-YYYYMMDD-HHMMSS \
  make platform-recovery-backup
```

The mode-0700 package contains a Git bundle, exact API/Web/Replay/PostgreSQL images, a consistent
PostgreSQL custom dump, sealed candidate Runs, attribution releases, Compose/config/license
inventory, expected state, and SHA-256 manifest. It excludes node-local passwords and MFA keys,
failed `.staging` attempts, and the external 14 GB DWD authority. Keep the node-local mode-0600
environment through a separate secret-backup mechanism.

Prove restoration in an isolated project whose name starts with `quanthus-pk6-restore-`. The
restore topology has separate volumes, loopback-only ports 18080/13100, no Replay Worker, and no
Traefik network, so it cannot consume work or receive public traffic:

```bash
QUANTHUS_RECOVERY_PACKAGE=/srv/quanthus/recovery/pk6-YYYYMMDD-HHMMSS \
QUANTHUS_RESTORE_PROJECT=quanthus-pk6-restore-drill \
QUANTHUS_PLATFORM_ENV=/home/ubuntu/.config/quanthus/platform.env \
QUANTHUS_RECOVERY_RECEIPT=artifacts/platform-kernel/recovery-receipt.txt \
  make platform-recovery-restore
```

After inspecting the receipt and restored UI, remove only that explicit drill project with
`docker compose --project-name quanthus-pk6-restore-drill --env-file ... --env-file ... -f ... down -v`.
Never point the restore script at the `quanthus-platform` production project or production volumes.

- Migration failure: the transaction rolls back and no ledger row is authoritative; diagnose and
  rerun the same migration. Never hand-edit the ledger.
- Readiness failure: keep the API out of service and inspect PostgreSQL connectivity; liveness does
  not imply readiness.
- Interrupted replay: the Worker renews its lease every 30 seconds. After the five-minute lease
  expires, the same or another temporary Worker may reclaim it with a higher `claim_generation`.
  The old process must terminate its child and cannot publish.
- Interrupted publication: a Run sealed for the same Job may be reused only after manifest, result,
  path-containment, candidate-count, and all evidence checksum validation. PostgreSQL registration
  still requires the current generation. A mismatch is a terminal failed Job, not a repair signal.
- Job cancel: cancellation is cooperative. The Supervisor terminates and then kills the child by
  the configured deadline. Poll until `canceled`; do not treat
  `cancel_requested=true` as terminal.
- Rollback before PK6 acceptance: stop the new Web and API services and retain the database volume and immutable artifacts for
  diagnosis. No old product path is shut down by this checkpoint.

Do not add a second always-running Replay Worker, a new queue transport, or another deferred
capability without its measured ADR 0012 activation trigger and matching contracts, permissions,
deployment, and recovery tests. A second Replay Worker is permitted temporarily for competition
and stale-generation acceptance, then must be removed.
