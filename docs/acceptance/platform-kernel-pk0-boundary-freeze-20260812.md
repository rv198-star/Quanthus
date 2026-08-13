# Platform Kernel PK0 Boundary Freeze Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-08-12 |
| Delivery gate | `config/platform-kernel-gates.v1.json` |
| Governing work | `PK-BOUNDARY-001` |
| Source baseline | QuantDinger `v5.0.15` / `e64e1c227bf3174e441a42143620179b286387e1` |
| Consequence | PK1 Minimal Boot open at `PK-COMPOSE-001` only |

## 1. Decision

PK0 passes its boundary criterion. All 29 audited capabilities have exactly one disposition, a
target owner, source scope, and restoration condition. The user accepted the six proposed defaults
on 2026-08-12. This acceptance freezes migration scope; it does not admit or implement upstream
business code.

Quanthus remains the sole authority for market data, PIT visibility, snapshots, replay and
accounting, strategy and feature governance, decisions, execution authorization, and fill truth.
QuantDinger remains a pinned engineering source for the platform shell only.

## 2. Accepted Defaults

| Decision | Accepted value | Activation boundary |
| --- | --- | --- |
| Desktop Web | Build a Quanthus-owned Web shell | QuantDinger-Vue extraction requires a later fixed-revision license and provenance review |
| OAuth | Defer; use local password login plus MFA first | Named provider requirement and threat review |
| Redis/Celery | Keep `JobRunner` as a port; omit both transports initially | Named consumer plus load, durability, or HA evidence |
| Agent Token/Audit | Defer | Approved non-human consumer and least-privilege scope review |
| Email/Webhook | Defer behind the P6 notification transport gate | P6 gate plus a named notification consumer |
| Strategy/Indicator Authoring | Defer to optional PK5; first cutover may record `not-applicable` | PK4 acceptance and a separate draft-only activation decision |

The Web choice resolves the former external-source blocker by removing QuantDinger-Vue from the
first product path. It does not declare that repository's unverified revision or license accepted.

## 3. Frozen Inventory

| Disposition | Count | Meaning at PK0 |
| --- | ---: | --- |
| `keep` | 8 | Extract platform behavior and conform it to Quanthus ownership and composition |
| `replace` | 4 | Preserve the operator need through a Quanthus-owned implementation or port |
| `defer` | 6 | Exclude from the first kernel until its named activation condition is met |
| `remove` | 11 | Exclude code, routes, startup, schema, dependencies, processes, tests, and navigation |

The exact capability rows and restoration conditions are frozen in
`config/platform-kernel-gates.v1.json`. Removed and deferred modules are recovered from provenance,
contracts, and reconstruction instructions rather than dead code in the main branch.

## 4. Gate Consequence

`PK-BOUNDARY-001` is accepted and PK1 is open. Only `PK-COMPOSE-001` is actionable. `PK-BOOT-001`
and `PK-OBS-001` remain blocked by the composition root, and all later stages retain their declared
predecessors. No source migration, dependency installation, database migration, service startup, or
Web implementation was performed while closing PK0.

## 5. Verification

- `tests/contract/test_platform_kernel_planning.py`: 4 passed.
- Focused Ruff checks for the Platform Kernel planning and traceability files: passed.
- `config/platform-kernel-gates.v1.json`: parsed successfully as JSON.
- Offline GitHub planning preview: 11 epics, 8 milestones, 86 tasks, and 14 LLD 11 work items;
  `PK-COMPOSE-001` is the only ready PK item and `PK-BOUNDARY-001` is closed.
- TPlan Mission schema check: passed; PK0 nodes are completed and `PK-COMPOSE-001` is active.

Repository-wide verification is separately limited by pre-existing unreadable P4 event-library,
test, configuration, and acceptance files owned by `nobody:nogroup`. The combined document test
cannot import `quanthus.event_library.rule_card`, and `make check` cannot complete compilation.
This access-control condition was not introduced by Platform Kernel work. It neither supplies nor
invalidates PK0 boundary evidence, and it must not be treated as acceptance of any PK1 runtime
behavior.
