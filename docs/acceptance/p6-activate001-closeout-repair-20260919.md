# P6-ACTIVATE-001 Closeout Repair Acceptance — 2026-09-19

| Field | Value |
| --- | --- |
| Scope | post-activation closeout repair after independent review |
| Production activation | enabled |
| Official collection | `0 / 66` sessions, `0 / 3` independent blocks |
| API image | `quanthus-platform-kernel:git-6a6ff44` |
| Replay Worker image | `quanthus-replay:git-6cb72eb` |
| P6 runtime contract | compliant |
| First official cycle | not yet available |
| First official reproduction | not yet due |
| P6 final review | existing `P6-REVIEW-001` / GitHub #120 |
| Authority | paper-only; notification, broker, live capital, arbitration 2022-2024 and permanent blind 2025 remain unauthorized |

## Review findings repaired

An independent read-only review of the initial activation closeout found four material issues. This
repair closes them without changing the frozen strategy, proposal, observation budget, scorecards,
paper policy, or prospective evidence already published.

### 1. Frozen P6 design binding restored

The original closeout had edited
`docs/design/lld/08-validation-governance/p6-forward-validation.md`, even though
`config/forward-validation/entry/p6-prospective-mvp-entry.v1.json` freezes that file by SHA-256.

The design file has been restored byte-for-byte to the accepted frozen version:

```text
sha256:2336837cd5023f2aec475b8185df6a824566a9f1a779d13c477c6b0309cc3f67
```

The entry proposal was not re-versioned and no frozen semantic contract was relaxed.

### 2. First-official-cycle independent reproduction is now executable

The existing `quanthus.p6-forward-cycle.v1` Replay Worker now admits a bounded
`official_reproduction` mode. It uses the exact first official-cycle input but independently
rebuilds the cycle into a separate reproduction artifact inventory.

The reproduction:

- uses the existing replay queue and Worker; no new transport, queue, or registry is introduced;
- produces `official_evidence=false` and therefore never increments the 66-session budget;
- compares the primary and independently rebuilt forward manifest, official artifact, and
  prospective projection identities;
- fails closed on `same_release_reproduction_semantic_mismatch`;
- blocks admission of a second official market session until the first-cycle reproduction passes.

Therefore the frozen `P6-ACTIVATE-001` acceptance condition can complete automatically when the
first legal post-activation READY session arrives, while historical data still cannot satisfy the
gate.

### 3. Production control-plane source is versioned

The previously host-only production adapter contract is now preserved under:

```text
ops/production-control/quanthus-mcp-production.py
ops/production-control/compose.override.yaml
ops/production-control/quanthus-mcp-production.service
ops/bin/verify-quanthus-production-control
```

The repository source and current OCI root-owned installation compare byte-identically. Installation
remains an explicit privileged operations action; ordinary application deployment does not gain a
root shell or arbitrary Docker/filesystem authority.

### 4. Product/Health activation projection uses canonical validation

The read-only Forward Validation projection no longer implements a weaker activation check. It now
uses the canonical prospective-runtime, owner-mandate, and activation-decision validators.

A mutation of runtime, mandate, decision digest, authority, or another canonical activation binding
fails as `activation_projection_drift` rather than being displayed as enabled.

The Platform API image remains deliberately lean. It includes only the validation modules and
read-only configuration artifacts required to validate the activation binding; DWD providers,
backtest execution, official-cycle execution, and reproduction execution remain outside the API
image.

## Pre-evidence maintenance rebind

The repair changed Replay Worker code before any official prospective observation existed. The
controller therefore permits an append-only maintenance rebind only under the following conditions:

1. zero official-cycle Jobs exist;
2. the new exact Worker revision reruns the real disabled rehearsal;
3. platform health/ready are green;
4. every required DWD/reference mount is read-only;
5. the rollback receipt again proves zero pre-activation official Jobs;
6. a new immutable enabled decision is published for the maintenance revision.

Once any official-cycle Job exists, revision drift fails closed as
`p6_post_evidence_revision_drift`; automatic maintenance rebind is no longer allowed.

The deployed Replay Worker revision `6cb72eb` completed its revision-specific disabled rehearsal
successfully. No official-cycle or reproduction Job existed at repair closeout.

## Production verification

Final production observation after the repair:

```text
API health:                    200 / healthy
API ready:                     200 / ready
Forward validation state:      prospective_activation_enabled
Admission:                     enabled
Collection:                    0 / 66
Independent blocks:            0 / 3
First official market date:    null
Last admitted DWD release:     null

Replay Worker:                 quanthus-replay:git-6cb72eb
P6 controller:                 enabled
P6 runtime contract:           compliant
Base V2.1:                     read-only
Correction:                    read-only
Frozen research bundle:        read-only
/data/dwd-daily:               read-only

Official-cycle Jobs:           0
Official-reproduction Jobs:    0
```

The API packaging repair was additionally validated by building the actual lean Platform image and
executing the canonical `load_prospective_runtime_config` plus
`load_owner_activation_mandate` path inside that image.

## Regression verification

The final read-only repair review completed with:

```text
P6/platform focused tests:     106 passed / 0 failed
Ruff:                          PASS
git diff --check:              PASS
production-control drift:      PASS
frozen P6 design binding:      PASS
canonical API image smoke:     PASS
```

## Remaining gate

This repair does not prematurely close `P6-ACTIVATE-001`.

The work item closes only after:

1. the first legal post-activation READY trading-session release runs as one official cycle; and
2. the independent reproduction of that exact first cycle passes.

After that, normal collection continues toward `66 / 66`, followed by the frozen maturity tail
and `P6-REVIEW-001` / GitHub #120.

No repair finding changes the original P6 claim ceiling or authorizes P7.
