# Structure Facts v2 B1 contract checkpoint (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-004` |
| Parent design | `../design/structure-facts-v2.md` |
| Baseline revision | `9d726c1e28a6a02eab2c3d8dc7c8148f7c891a7f` |
| Delivered slice | B1 naming/boundary/time-envelope contracts and coverage ledger |
| Whole Facts v2 | OPEN; runtime adapter/build/qualification NOT STARTED |
| Dictionary issue | `rv198-star/Quanthus#215`, OPEN; non-blocking independent backlog |
| Production deployment | NOT PERFORMED |
| New DWD/backtest/model Jobs | NONE |

## Scope and authority

The owner requested implementation of reviewed ontology boundaries, semantic/category naming,
separate dictionary maintenance, explicit temporal modeling and a staged coverage blueprint.
ADR 0014 and the parent design implement that decision as a time-first domain semantic model.
No graph database, ontology reasoner, new warehouse layer or runtime Worker was introduced.

The new `structure_semantics.py` resolves registered names and checks local temporal envelopes.
Its eligibility result does not verify source authenticity or grant partition, quality, trading or
historical-PIT authority. Exact dependency receipt verification and runtime as-of joins remain B2.
All v2 catalog concepts are reserved definitions, not promoted evaluators. Legacy links remain
related-not-equivalent. V1 IDs/formulas/builders and accepted Run artifacts were not modified.

## Verification actually executed

Commands used the existing project test environment:
`/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python` with `PYTHONPATH=src`.
The system Python lacked pytest/ruff; that environment-only invocation did not run tests and is
not counted as verification. No package installation or environment-permission change was made.

- New namespace/time/blueprint tests: **27 passed**.
- New tests plus document traceability: **60 passed**.
- Ruff over modified Python contracts/tests: **PASS**.
- Planning dry-run: **PASS**, 122 uniquely owned work items.
- `git diff --check`: **PASS**.
- Repository `make check`: **1454 passed, 7 skipped**, 187.56 seconds in the test phase.

The seven skips are the existing production-artifact and PostgreSQL-DSN dependent tests; this does
not claim a new PostgreSQL or production integration acceptance.

Counterexamples exercised: exact prefix boundaries versus fuzzy matches, reserved-name/runtime
separation, duplicate/extra catalog fields, silent legacy equivalence, unknown publication time,
public versus actual platform availability, late source arrival after T close, source correction
version selection at a half-open interval boundary, delayed confirmation, later physical rebuild,
timezone equivalence and a non-cyclic coverage graph with no dictionary prerequisite.

## Bounded conformance review

This review was performed by the implementing assistant, not external independent expert sign-off.
The review checked the main contracts against the earlier quant/modeling/warehouse findings:

- references are not asserted to be strong support; daily ranges do not prove intrabar ordering;
- disclosures and market context are parallel evidence, while company-action/identity/calendar
  dependencies are retained;
- event time, confirmation, public availability, platform knowledge and physical computation are
  not conflated;
- current retrospective provider limits remain explicitly stated rather than upgraded by hashes;
- the blueprint separates defined, implemented and verified scope, and names remaining gaps;
- the local time checker explicitly cannot stand in for full source-evidence closure.

These are design/contract findings. No new real-market performance, source-timestamp completeness,
full v2 acceptance, model quality or trading value was evaluated.

## Independent dictionary work

The existing repository planning GitHub API helper was used only to search for `P3-DICT-001`,
create one requested issue after no match, and read back its title/state/body. The resulting issue
is #215, OPEN, titled '维护数据与指标字典（独立排期，不阻塞 Facts v2）'. No repository-wide issue
synchronization, label/milestone changes or unrelated issue mutations were performed.
The repository scope page is `../work-items/data-indicator-dictionary.md`.

## Next boundary

The coverage ledger keeps B1 as the current delivered slice. B2 input-readiness and evidence-view
admission precede a small B3-B5 reference/interaction/episode runtime slice. Parameter choices,
Real-DWD scope and resource budget must be frozen before new result-bearing qualification.
B6/B7 expansions, full dictionary delivery and formal expert/economic work are not automatically
included. No new approval is manufactured from the presence of their names in the blueprint.
