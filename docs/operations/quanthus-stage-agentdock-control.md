# RX6800 AgentDock Restricted Stage Control

## Scope

This runbook defines the narrow cross-user control surface used when the RX6800 AgentDock service
needs to operate the governed Quanthus Stage Worker owned by the `qiuyu` user.

The helper is `ops/bin/quanthus-stage-agentdock-control`. It is intentionally not a generic shell,
Git wrapper, service manager, or Stage Worker passthrough.

## Allowed operations

The installed helper accepts exactly four commands:

- `deploy <exact-main-revision>`: fetch GitHub `main`, require the supplied 40-character revision to
  equal the fetched `main` head exactly, detach the control checkout at that revision, restart the
  existing user Stage Worker, verify the runtime receipt, and run `doctor`;
- `doctor`: run the governed Stage Worker doctor only;
- `submit-p011-d1 <exact-revision>`: require both the control checkout and runtime receipt to equal
  the supplied revision, then submit only
  `quanthus.stage-orderly-consolidation-joint-d1-prepare.v1`;
- `status <stage_job_id>`: return status for one syntactically valid `stage_<32hex>` job id.

There is no generic `submit`, `cancel`, `serve`, arbitrary Git ref, arbitrary Stage kind, shell-command
forwarding, file mutation, or environment-controlled path surface.

## One-time installation

Run these commands from the normal `qiuyu` RX6800 shell after the repository revision containing the
helper has been checked out:

```bash
sudo install -o root -g root -m 0755 \
  ~/quanthus-code/Quanthus/ops/bin/quanthus-stage-agentdock-control \
  /usr/local/sbin/quanthus-stage-agentdock-control

printf '%s\n' \
  'agentdock ALL=(qiuyu) NOPASSWD: /usr/local/sbin/quanthus-stage-agentdock-control *' \
  | sudo tee /etc/sudoers.d/quanthus-agentdock-stage >/dev/null

sudo chmod 0440 /etc/sudoers.d/quanthus-agentdock-stage
sudo visudo -cf /etc/sudoers.d/quanthus-agentdock-stage
```

The installed executable is root-owned so the `agentdock` service account cannot replace the helper
it is authorized to invoke. The sudo rule changes only the target identity for this one executable;
it does not grant `agentdock` a general `qiuyu` shell or unrestricted `sudo`.

## Operational sequence

For P3-STAGE-011-D1 the caller must use:

```text
deploy <exact current GitHub main SHA>
        -> doctor succeeds and runtime receipt matches
        -> submit-p011-d1 <same SHA>
        -> status <returned job id>
```

Repeated `submit-p011-d1` at the same revision is governed by the Stage Worker's immutable one-shot
budget marker and returns the existing Job. A different revision is rejected after the budget marker
exists. This helper does not create retry authority.

## Boundaries

- GitHub remains the only normal code-distribution path.
- The helper never opens preserved 2021 evaluation data or 2022-2025 data.
- It does not bypass Stage Worker job-kind admission, immutable budget markers, artifact verification,
  or candidate-blind review gates.
- Research authority remains owned by repository contracts, not by this operating channel.
