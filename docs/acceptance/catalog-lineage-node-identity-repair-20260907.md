# Catalog lineage exact-node identity / table-context readability repair — 2026-09-07

| Field | Value |
| --- | --- |
| Work items | `P3-DICT-002-C` / #230; parent #232 |
| Trigger | User review of the rendered `dwd_adjustment_factor` lineage graph |
| Disposition | PASS for repository implementation; production release remains under `P3-DICT-002-D` |
| Data / formula / execution authority | unchanged |

## 1. Finding

The previous field-lineage repair correctly separated `field_proven` from `table_context_only`, but
its table-level graph was still not useful to a human reader. Distinct declared sources such as
`adjustment_evidence_delta` and `factor_evidence_delta` were both rendered as the same generic
"复权因子来源 · 日发布工件" label, while redundant generic "日发布工件规范化" process nodes
occupied a second column. The exact source/table/field identity was hidden from the graph itself.

This was a presentation/graph-model defect, not evidence that the sources were identical.

## 2. Repair

- Every lineage node now has a mandatory `technical_id` in addition to its business-readable label.
  Catalog entries use their exact canonical IDs; declared logical artifacts and projection fields
  retain their exact registered identities.
- The SVG node renders business label, exact technical ID, then grain/role. The exact identity is
  also part of its accessible name and full tooltip.
- Dataset-level ODS / declared-source relationships now connect source directly to the DWD target
  table. They no longer create a generic process box whose only content was "规范化". Process nodes
  remain for meaningful field/definition transformations where the transformation itself is useful
  evidence.
- The adjustment-factor sources receive distinct readable labels while retaining exact IDs:
  `adjustment_factors_pit`, `adjustment_evidence_delta`, `factor_evidence_delta`, and
  `adjustment_evidence`.
- Global-map drill-down lists also show exact technical IDs rather than only display names.

## 3. Mandatory counterexample

A depth-2 focus on `dwd_table:dwd_adjustment_factor` now contains exactly distinguishable source
identities and no generic process nodes:

```text
带时间版本的复权因子来源       adjustment_factors_pit
日发布复权证据增量             adjustment_evidence_delta
日发布因子证据增量             factor_evidence_delta
周发布复权证据                 adjustment_evidence
                               ↓
复权因子                       dwd_adjustment_factor
```

The table-level arrows are declared dataset processing context. They do not claim that every field
of the target table depends on every source. `dwd_adjustment_factor.effective_from` continues to use
the separate proven field-level chain introduced by the previous requalification.

## 4. Coverage and semantics

The graph currently contains 259 registered processing edges after removing redundant dataset-level
process boxes. DWD field proof classification remains unchanged and explicit:

```text
DWD fields:          524
field_proven:         10
table_context_only:  514
unregistered:           0
```

Removing generic process nodes does not remove source evidence: each direct dataset edge retains the
same exact authority path, SHA-256 and contract locator. `contains`, `field_of` and conceptual
relationships still do not become processing edges. Static declared lineage remains different from
actual Run consumption evidence.

## 5. Verification

Focused Python Catalog/API regression:

```text
51 passed
```

Web verification:

```text
npm run lint: PASS
npm test: 18 passed
Chromium Playwright: 3 passed
  - 1440px
  - 390px
  - anonymous boundary
```

Focused documentation / Catalog requalification:

```text
68 passed
```

Full repository gate:

```text
make check PYTHON=/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python
1590 passed
7 skipped
```

The seven skips are the existing unconfigured PostgreSQL fixtures and unmounted production expert
artifacts; they are not counted as passes.

## 6. Claim ceiling

This repair does not claim full exact field lineage for all 524 DWD fields. It makes the existing
proof level honest and the visible graph useful: exact identities are always shown, and dataset
context is not padded with generic boxes. It changes no ODS/DWD data, Feature formula, Label rule,
Facts value, expert mapping, P2 behavior, Worker, Shadow/paper/live authority or historical Run.
