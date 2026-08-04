# ADR 0007: DWD Maintenance Baseline and P4 Handoff

- Status: Accepted
- Date: 2026-08-04
- Decision id: `META-DATA-DWD-002` / `P4-EVENT-001`
- Design status: approved
- Delivery gate: DWD maintenance active; P4 implementation open; generic DWS deferred
- Supersedes: ADR 0005 as the current operating topology; ADR 0005 remains historical migration evidence

## Context

The July rebuild has produced the first product-bounded warehouse baseline rather than the earlier
strategy-shaped trial slice. `V2 Build202607` contains an immutable ODS Base through 2026-07-31 and
an immutable DWD Base with all 33 approved objects. The DWD manifest records 468,268,180 rows and
14,842,590,066 payload bytes. It is the seed for later immutable daily and weekly increments; the
Base itself is never mutated.

The earlier dual-lane design required CN01 and Hermes to produce paired DWD/DWS candidates for OCI
arbitration, followed by generic DWS, ADS, and release-set publication. That design was useful for
testing publication mechanics, but it no longer matches the accepted operating path or the next
product need. Generic DWS has no approved consumer requirement, and the old operational ADS is a
strategy-specific trial slice rather than the next research input.

P3 is accepted as a capability fixture. The next falsifiable product question is which observable
states, present before an event entry, distinguish successful, neutral, failed, and unexecutable
candidate events. That question belongs to P4 and can consume exact DWD snapshots directly.

## Decision

### 1. Current data baseline

The two authoritative Base identities are declared by `config/releases/v2-build202607.json`:

- `ods-base-202607-v2`, logical role `ODS Base V2 Build202607`;
- `dwd-base-202607-v2`, logical role `DWD Base V2 Build202607`.

Both are read-only inputs. Daily processing writes immutable target-date ODS releases and DWD
increments. Weekly processing writes immutable correction releases and corresponding DWD
correction increments after its separate implementation and acceptance gate. No recurring job may
rewrite either Base.

### 2. Node responsibilities

- CN01 and Hermes run the same four-lane ODS controller and build their own READY-gated DWD daily
  increment from the node-local Base.
- CN01 is the source for OCI's current DWD mirror. OCI verifies and installs CN01's immutable DWD
  increment; it does not run ODS, generic DWS, or ADS production in the current profile.
- Node-local completion and failure notifications identify the node and stage. A timer start is not
  completion evidence; four verified ODS READY releases are the DWD gate.
- The old fixed-time DWD trigger, Hermes-to-OCI ODS pull, dual-lane epoch/arbitration, paired
  DWD/DWS publication, and old ADS publication are not current execution paths.

### 3. Generic DWS is demand-gated

The accepted ten-dimension, thirteen-fact DWS constellation remains a valid design option, but its
implementation is deferred until at least one concrete cross-domain consumer demonstrates that a
shared aggregate or conformed dimensional projection is needed. P4 does not wait for generic DWS.
It may build a purpose-specific, manifest-bound `event_library` projection from DWD without
turning that projection into a general warehouse layer.

### 4. P4 becomes the product-development mainline

P4 first freezes a candidate-source hypothesis that uses only information knowable at T close.
It then scans historical DWD snapshots forward, creates deterministic candidate events, checks
whether entry is executable at T+1, labels the fixed future observation window, and produces the
comparative report. The P3 capability fixture is reusable infrastructure, not the default P4
candidate rule or a strategy lineage.

Data-foundation work after this decision is maintenance: daily/weekly continuity, corrections,
quality exceptions, restore evidence, and source-driven schema changes. Such work may block an
affected date or dataset but does not keep P4 globally blocked when an exact accepted DWD snapshot
is available.

## Consequences

Positive consequences:

- the issue tracker reflects deployed operation rather than an unexecuted migration architecture;
- DWS construction is justified by an actual consumer instead of warehouse completeness alone;
- P4 can begin from a stable, full-domain DWD baseline; and
- OCI storage and compute are reserved for the verified DWD mirror and later approved consumers.

Costs and constraints:

- Hermes and CN01 DWD outputs may differ when their ODS evidence differs; the current profile does
  not claim OCI arbitration between them;
- weekly DWD correction processing remains a separate maintenance deliverable until accepted;
- a future generic DWS requirement needs a new issue and explicit activation of the retained
  constellation design; and
- historical ADR 0005 packages and acceptance records remain retained evidence but must not be
  mistaken for current operating authority.

## Rejected alternatives

### Finish generic DWS before P4

Rejected because no current P4 question requires all ten dimensions and thirteen facts. Building
them now would spend capacity without a falsifiable consumer contract.

### Continue dual-lane OCI arbitration

Rejected for the current profile because the deployed path already gives each acquisition node a
complete local ODS-to-DWD chain and gives OCI one verified CN01 mirror. Reintroducing arbitration
requires a new availability or publication requirement.

### Reuse the old ADS as the P4 event library

Rejected because it is a retained strategy-specific trial slice. P4 must bind its own candidate
rule, snapshot, executable-entry policy, label card, and split policy.
