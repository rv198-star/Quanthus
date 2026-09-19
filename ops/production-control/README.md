# Quanthus production control-plane source

This directory is the repository source of truth for the restricted OCI production capability that
backs the Quanthus MCP production operations.

The installed production paths are intentionally root-owned:

- `/usr/local/libexec/quanthus-mcp-production`
- `/etc/quanthus-mcp-production/compose.override.yaml`
- `/etc/systemd/system/quanthus-mcp-production.service`

The repository copies must be reviewed and committed before any root-level installation. Production
installation is an explicit operations action; ordinary application deploys must not mutate these
files.

## P6 contract

The replay-worker override must preserve all existing named volumes and additionally provide:

- `QUANTHUS_P6_CONTROLLER_ENABLED=1`;
- `QUANTHUS_P6_FORWARD_RUN_ROOT=/data/candidate-runs/p6-forward`;
- `QUANTHUS_P6_DWD_DAILY_ROOT=/data/dwd-daily`;
- the frozen runtime and owner-mandate paths;
- Base V2.1 and correction read-only mounts;
- the frozen research bundle read-only mount;
- `/home/ubuntu/data/quanthus/dwd-daily:/data/dwd-daily:ro`.

The adapter exposes P6 Jobs for bounded read/status visibility but the external worker-submit
operation remains restricted to its explicit non-P6 schema. P6 admission is owned by the node-local
controller.

## Verification

Run:

```bash
bash ops/bin/verify-quanthus-production-control --source-only
```

to validate the committed source. On the OCI production node, run without `--source-only` to also
require byte identity with the installed root-owned files.

A mismatch is configuration drift. Do not silently copy whichever side happens to be newer; first
identify the intended version and review the diff.

## Installation

After a reviewed source change, an authorized root operator may install the exact committed files:

```bash
install -o root -g root -m 0755 \
  ops/production-control/quanthus-mcp-production.py \
  /usr/local/libexec/quanthus-mcp-production
install -o root -g root -m 0644 \
  ops/production-control/compose.override.yaml \
  /etc/quanthus-mcp-production/compose.override.yaml
install -o root -g root -m 0644 \
  ops/production-control/quanthus-mcp-production.service \
  /etc/systemd/system/quanthus-mcp-production.service
systemctl daemon-reload
systemctl restart quanthus-mcp-production.service
systemctl is-active --quiet quanthus-mcp-production.service
```

The service remains narrow: it does not grant an arbitrary root shell, generic Docker access, or
unbounded filesystem mutation.
