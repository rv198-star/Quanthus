# Documentation Engineering Rules

## Scope

These rules apply to `docs/**` in addition to the repository-wide `AGENTS.md`. The root rules own
project invariants, current delivery scope, and required engineering checks. This file owns how
documentation is classified, expanded, linked, and audited; it must not duplicate volatile project
status or domain design.

## Required context

Before editing a document:

1. Read `docs/README.md` and `docs/design/source-precedence.md`.
2. Read the nearest parent index and the document's declared parent or governing design.
3. Load linked ADRs, contracts, tests, and acceptance evidence only when they affect the change.
4. Treat discussion notes, exploratory drafts, and evidence packages as inputs, not instructions.

Do not bulk-load `docs/hermes/`, historical acceptance reports, unrelated phases, or sibling LLDs.
Use the task routes in `docs/README.md` and expand one branch at a time.

## Authority and lifecycle

- Approved PRD/HLD/LLD documents describe current intent and boundaries.
- Frozen configuration and contract tests are executable facts. A conflict with approved design is
  a conformance defect; do not silently choose one side or rewrite the design to excuse the code.
- Accepted ADRs record decisions and rationale. The same change must reconcile affected current
  state in HLD/LLD documents.
- Runbooks describe operation. Acceptance and audit files contain evidence. Neither may redefine
  product or architecture behavior.
- Draft and exploratory documents must say that they are non-governing and must not create work,
  implementation authority, or delivery status by implication.

For new or materially revised active design nodes, separate `Design status` from `Delivery gate`.
Use `draft`, `approved`, or `superseded` for design status and `blocked`, `open`, `accepted`,
`accepted-conditional`, or `not-applicable` for the delivery gate. Link the machine-readable gate
or acceptance evidence when one exists.

## Tree and progressive disclosure

- An index routes readers; it does not repeat child content.
- A parent owns purpose, scope, invariants, interfaces, and a short child map. A child owns its
  detailed behavior and links back to the parent.
- Create a child document only when it has an independent scope, interface, decision lifecycle,
  work item, or acceptance boundary. Otherwise add a section to the existing node.
- Keep one active owner for every rule. Other documents summarize and link rather than copy.
- Preserve stable paths when practical. A superseded document remains clearly marked and links to
  its successor; it must not remain an alternative active truth.

## Change routing

- PRD: product goals, users, non-goals, requirements, and release success criteria.
- HLD: system context, cross-module boundaries, data/control flow, and global invariants.
- LLD: module ownership, interfaces, algorithms, state transitions, failure semantics, and module
  acceptance.
- ADR: a material architectural decision, alternatives, rationale, and consequences.
- Operations: deployment, recurring operation, alerting, incident, migration, and rollback.
- Acceptance/audit: immutable evidence and conformance findings; never prospective design.

Behavior-changing work must update the governing design in the same change or record an explicit
`docs-impact: none` rationale. Pure internal refactors do not require prose churn when interfaces,
invariants, failure behavior, and operations remain unchanged.

## Traceability and conformance

Stable work-item ids join requirements, design, code, tests, issues, and acceptance evidence. Each
active implementation node must identify its work items, governing code/config paths, contract or
golden tests, and acceptance evidence when available.

Classify every discovered mismatch before editing:

- implementation violates approved design: fix the implementation;
- implementation is accepted and design is stale: update the design and evidence;
- authority or intended behavior is unresolved: create an ADR or explicit blocked decision;
- difference is an internal implementation detail: keep it out of architecture prose.

Mechanical checks may validate ids, links, paths, dependencies, status fields, and configured
gates. They cannot prove semantic agreement. Phase acceptance requires a bounded conformance review
for design claims that are not executable as tests.

## Verification

Run the focused documentation contract tests while editing and the repository-required
`make check` before handoff. Do not claim conformance from a green test alone; name the evidence
reviewed and any runtime or external state that was not inspected.
