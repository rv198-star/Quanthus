# Catalog field-focus lineage repair acceptance — 2026-09-07

| Field | Value |
| --- | --- |
| Work item | `P3-DICT-002-C` / #230 |
| Finding | field-focused lineage could return only the selected DWD field even when table/process evidence existed |
| Disposition | ACCEPTED for implementation requalification; production release remains under `P3-DICT-002-D` |
| Data / formula / Facts / execution change | NONE |

## 1. Finding and correction

The original productization release correctly kept `field_of` / `contains` out of the processing
graph, but ODS -> DWD coverage was mostly dataset/table-grain while only selected research chains had
field-grain mappings. Therefore a DWD field without an explicit field edge could appear as a
one-node focus graph. The UI warning was truthful but insufficient: a one-node graph was still an
unhelpful and misleading product result.

The repair does **not** infer field mappings from table membership. Every DWD field focus now returns
one explicit proof level:

- `field_proven`: exact evidence-bound processing edge(s) directly use or produce the field;
- `table_context_only`: no exact field mapping is registered, so the field graph remains unmodified
  and a separate parent-table processing context is returned with an explicit non-field-mapping
  warning;
- `field_unregistered`: neither field proof nor parent-table context is available.

The table context is separate response data. Its edges never use the selected field as an endpoint,
so table-level source participation cannot silently become a fabricated field dependency.

## 2. Mandatory counterexample

`dwd_adjustment_factor.effective_from` now has exact field-level evidence:

```text
adjustment_factors_pit.effective_date
  -> materialize_adjustment_factor_rows / _effective_start
  -> dwd_adjustment_factor.effective_from
     -> close_adjustment_factor_intervals
        -> dwd_adjustment_factor.effective_to
        -> dwd_adjustment_factor_input.effective_to
     -> P4 Event Library factor_boundary
        -> factor_effective_from
        -> adjustment_effective
```

The source and downstream edges are bound to the exact repository files and SHA-256 values in
`config/catalog-lineage.v1.json`. Tests verify those file digests and locators rather than accepting
name similarity as lineage evidence.

## 3. Systematic DWD field coverage

After the repair the deterministic projection reports:

```text
DWD fields total:       524
field_proven:            10
table_context_only:     514
field_unregistered:       0
registered graph edges: 326
```

A test iterates all 524 DWD fields. Every field must either have at least one exact processing edge
or a non-empty, separately marked parent-table context. No field may silently fall back to an
unexplained one-node result.

This count is deliberately honest: the repair does **not** claim 524 complete field-level lineage
chains. It makes the proof boundary visible and useful while exact field mappings are added only
from reviewed evidence.

## 4. UI behavior

The field-focus view now labels its proof level before the graph. `field_proven` shows the exact
processing graph. `table_context_only` shows the selected field separately and then an
"所属表已证明的加工上下文" section, which explicitly states that the table context does not prove
that the field uses every shown source or downstream.

The global lineage view also publishes the 524-field coverage split so the product cannot present
partial field lineage as complete coverage.

## 5. Verification

Focused Catalog tests:

```text
49 passed
catalog snapshot --check: PASS
```

Documentation / traceability checks after reopening the work items:

```text
38 passed
```

Web verification:

```text
npm run lint: PASS
npm test: 18 passed
Catalog-scoped strict TypeScript check: PASS
Chromium E2E: 3 passed
  - 1440px
  - 390px
  - anonymous login boundary
```

The browser E2E explicitly checks both the exact `effective_from` chain and a
`dwd_adjustment_factor.factor_value` table-context-only fallback.

Full repository gate:

```text
make check PYTHON=/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python
1588 passed
7 skipped
```

The seven skips are the existing unconfigured PostgreSQL test contexts and unmounted production
expert artifacts; they are not counted as passes.

## 6. Claim ceiling

This acceptance repairs field-focus semantics and adds one exact adjustment-factor field lineage
family. It does not claim complete field-level provenance for all 524 DWD fields, actual historical
Run consumption, or any new market-data, strategy, model, Shadow, paper or execution authority.
`P3-DICT-002-D` remains responsible for commit/push, production API/Web deployment and final smoke.
