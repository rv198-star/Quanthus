# ADR 0010: Extract and Recompose a Platform Kernel from QuantDinger

- Status: Accepted
- Date: 2026-08-12
- Decision id: `PK-BOUNDARY-001`
- Design status: approved
- Delivery gate: PK1-PK4 accepted; first-cutover PK5 not applicable; PK6 open
- Gate source: `config/platform-kernel-gates.v1.json`
- Source baseline: QuantDinger `v5.0.15` / `e64e1c227bf3174e441a42143620179b286387e1`

## Context

Quanthus has strong domain foundations for A-share data, point-in-time visibility, replay,
strategy governance, decisions, and execution semantics. It does not yet have an equally complete
multi-user platform shell. QuantDinger supplies mature examples of authentication, administration,
API conventions, jobs, observability, deployment, and operator interfaces, so discarding all of it
would repeat expensive platform work.

QuantDinger's code organization is layered, but its deployable capabilities are not independent
plugins. Routes are centrally and unconditionally imported, startup hooks mix product services,
one SQL bootstrap owns 78 tables, authentication includes billing concepts, and one dependency set
carries platform, AI, market, and broker packages. Its desktop and mobile clients are delivered
from separate source repositories. Copying directories or disabling features with environment
flags would retain hidden runtime, schema, dependency, security, and test surfaces.

## Decision

### 1. Extract a kernel; do not adopt the upstream product topology

Quanthus will use the fixed QuantDinger source baseline as an extraction input. It will not make a
long-lived full Fork the product mainline and will not merge the complete upstream tree. The
extraction begins by adding an explicit feature manifest and composition root, then admits only
approved routes, startup hooks, process roles, migrations, dependencies, and Web navigation.

Code moved from upstream must retain required Apache-2.0 notices and modification markings.
Quanthus naming and assets remain independent. Public or private repository status is irrelevant
to this decision; source availability, license compatibility, and provenance remain mandatory
engineering inputs.

### 2. Preserve Quanthus authority

The Platform Kernel may own identity, access control, request handling, generic job orchestration,
operational state, observability, and presentation. It never owns or redefines:

- market-data acquisition authority or canonical datasets;
- PIT visibility, snapshot, and knowledge-cutoff semantics;
- replay, matching, ledger, or accounting semantics;
- strategy, label, feature, expert, or decision governance; or
- execution authorization, broker state, or actual-fill truth.

Platform endpoints call versioned Quanthus application ports. Direct imports of QuantDinger data
providers, backtests, strategies, brokers, or trading workers are a conformance failure.

### 3. Use four capability dispositions

Every audited capability receives exactly one disposition in LLD 11 and the machine-readable gate:

- `keep`: extract platform behavior, then conform it to Quanthus composition and naming;
- `replace`: retain the user need but implement it through Quanthus-owned semantics;
- `defer`: exclude it from the first kernel until a named consumer and activation gate exist;
- `remove`: exclude routes, code, startup, processes, schema, dependencies, tests, and navigation.

`Keep` never means blind file copying. `Remove` never means unrecoverable deletion.

### 4. Sequence the work through seven gates

The orthogonal Platform Kernel milestone uses `PK-*` work items rather than becoming P8. Its gates
are PK0 Boundary Freeze, PK1 Minimal Boot, PK2 Structural Slimming, PK3 Quanthus Integration, PK4
Operator Web, PK5 Optional Authoring, and PK6 Cutover and Recovery. PK0 was the only gate opened at
milestone creation. PK1-PK4 were accepted in sequence on 2026-08-12; the frozen default records
PK5 as not applicable to the first cutover and opens PK6 recovery work. Failure of a gate prevents
expansion into the next migration surface.

The first desktop Web path is a Quanthus-owned shell. QuantDinger-Vue may only become a later
selective extraction input after its exact revision and license are admitted; it is not a current
dependency. Mobile remains removed. Optional strategy/indicator authoring cannot begin before PK4
acceptance and a separate activation decision.

### 5. Recover by provenance and contracts, not dead code

Removed and deferred code does not remain disabled in the main branch. Recovery relies on the
pinned upstream tag and commit plus a versioned package containing source-path inventory, API and
schema contracts, dependencies, process roles, tests, license notices, local patch provenance, and
a reconstruction recipe. MCP, if later needed, is rebuilt against the current Quanthus API rather
than restoring its old hard-coded domain surface.

## Consequences

Positive consequences:

- mature platform mechanics can be reused without transferring domain authority;
- the first boot and each later gate have a much smaller test and security surface;
- removed features are genuinely absent from runtime and UI; and
- future upstream reuse remains possible from explicit provenance and contracts.

Costs and constraints:

- the first work is structural decomposition rather than visible feature delivery;
- auth must be detached from credits, VIP, membership, and referral behavior;
- PostgreSQL bootstrap and dependencies must be split before deletion claims are credible;
- any later QuantDinger-Vue extraction waits for source and license admission; and
- upstream upgrades are selective audits, not automatic merges.

## Rejected alternatives

### Continue directly on a full Fork

Rejected because upstream product modules, schema, workers, and UI assumptions would remain the
organizing center. Ongoing merge work would repeatedly reintroduce features Quanthus intentionally
does not own.

### Copy individual directories immediately

Rejected because route registration, application bootstrap, migrations, dependencies, and tests
cross those directory boundaries. The apparent module boundary is not the runtime boundary.

### Build the whole platform from scratch

Rejected because authentication, MFA, security logging, API conventions, observability, jobs, and
deployment patterns already provide useful tested material.

### Preserve removed modules behind flags

Rejected because disabled code still carries dependencies, migrations, vulnerability surface,
upgrade obligations, accidental reachability, and tests. A provenance-backed restoration package
is cheaper and more honest.

### Migrate QuantDinger strategies and AI decisions with the shell

Rejected because they compete with Quanthus domain authority. Upstream strategies may later enter
as ordinary proposals and must pass Quanthus replay and governance. AI authoring remains an
optional draft producer; AI decisions never bypass the governed path.
