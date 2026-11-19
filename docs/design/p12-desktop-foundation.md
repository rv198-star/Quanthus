# P12 desktop foundation (G1)

Status: engineering framework on synthetic accounts only. No daily, simulated or real account
adoption is granted by this code; production kernels expose the P12 routes behind RBAC and answer
`P12_RUNTIME_NOT_CONFIGURED` until an adopted runtime is assembled.

Authority: P12 D1 engineering contract v1.1 (sections 1A-1C, 4, 5) and the work-package matrix
(AR01-AR06). This page records where each layer lives and how the next work plugs in.

## Module map

| Layer | Location | Public entry | Owns |
| --- | --- | --- | --- |
| Desktop | `product-web/src/p12/` (Vue shell per ADR 0011) | routes `/p12`, `/p12/records`, `/p12/funding` | presentation, input checks, display formatting |
| Transport contract | `schemas/p12/v1/openapi.json` | `p12.desktop.v1` | request/response shapes, decimal text, unknown values, reason codes, labels |
| Generated artifacts | `src/quanthus/platform/p12/contract_generated.py`, `product-web/src/p12/contract.generated.{js,d.ts}` | `scripts/p12_contract_codegen.py [--check]` | never edited by hand |
| Entry adapters | `src/quanthus/platform/p12/` | `http.P12Endpoints`, `cli`, `transport` | auth binding, DTO mapping, error mapping |
| Assembly | `src/quanthus/platform/p12/assembly.py`, `local_server.py` | `build_synthetic_runtime` | the only place synthetic accounts are bound |
| Application | `src/quanthus/product_daily/application/desktop/` | `P12DesktopApplication` | use-case orchestration, scope check, adoption gate, read models |
| Capabilities | `src/quanthus/capabilities/{accounting,execution,funding,risk,decision_access}` | package `__all__` | business rules (one implementation each) |
| Shared | `src/quanthus/shared/` | `values`, `fact_contracts` | decimal text, MaybeDecimal, AccountScope, VersionRef, FactError |
| Infrastructure | `src/quanthus/platform/adapters/{product_daily_facts_sqlite,p12_synthetic_fixture}.py` | port implementations | SQLite two-transaction fact store; sealed synthetic fixture |

Dependency rules are machine-checked from `config/architecture/p12-module-boundaries.v1.json` by
`tests/unit/test_p12_module_boundaries.py`.

Rule ownership: quantity solving and exposure limits stay in `product_daily.risk.evaluate_quantity`
(wrapped by `capabilities.risk`); funding partitions and settlement stay in `product_daily.funding`
(wrapped by `capabilities.funding`, which never writes); ledger postings and projections stay in
`capabilities.accounting`; fact validation in `capabilities.execution`; PIT validation of market
decisions in `capabilities.decision_access`. The application layer only composes them.

## Account store rules (repair 02)

- A fill that happened before the takeover snapshot (`occurred_at` earlier than the baseline
  `as_of`) keeps its receipt and stays `NEEDS_RECONCILIATION` with
  `BASELINE_COVERAGE_UNKNOWN`; nothing is posted (`capabilities.execution.baseline_coverage_gap`).
- Each account store is bound once to its composition, decision source and execution profile
  (`P12DesktopApplication.bind_configuration`). Reopening it with a different configuration fails
  with `CONFIGURATION_BINDING_MISMATCH`; a new store may use a new policy. Stores created before
  bindings existed are adopted only when every plan-linked report matches the plan the presented
  configuration publishes. A persistent package library remains D1-B work.
- Taking over a store that already posted a fact dated before its baseline (possible only for
  stores written by 66ddf73d) is refused with `BASELINE_COVERAGE_UNKNOWN`, naming the reports and
  receipts; the store's data is left unchanged.
- A package and its plans are hidden until `published_at`; before then the records read also
  hides dependent-plan states, and a cash-only baseline reports its security as `CASH`. Under `close-to-next-open-v1` an entry
  window must lie inside the next trading session (09:30-15:00, exchange time); otherwise no plan
  is published and the opportunity shows `EXECUTION_WINDOW_INVALID`
  (`capabilities.decision_access.execution_window_issue`).
- Every read model is built from one `SyntheticFactsPort.snapshot` (projection and history in one
  SQLite read transaction), so a read shows either the complete old or the complete new state.

## Contract workflow

1. Edit `schemas/p12/v1/openapi.json` (closed objects, every property required, null for unknown).
2. Run `python scripts/p12_contract_codegen.py`; commit the three generated files.
3. `--check` and `tests/platform/test_p12_desktop_http.py` fail on drift, unknown reason codes or
   routes that differ from the manifest.

Every outbound DTO is validated before it leaves Python; the browser validates every response again
with the same component schemas (`product-web/src/p12/validate.js`).

## Extension points

| Need | Port / place | Current behaviour |
| --- | --- | --- |
| Adopted daily decisions (D1-B) | `DecisionPackageSource` | only the sealed synthetic fixture; other modes raise `POLICY_NOT_ADOPTED` |
| Funding cycles bound to facts (W3) | `capabilities.funding` + facts UnitOfWork | partition shown until the first fact, then `FUNDING_CYCLE_NOT_BOUND` |
| Plan reassessment / reservations | new use cases on `P12DesktopApplication` | affected plans become `NEEDS_REASSESSMENT`; no reservation or hold yet |
| Sells, fees, order observations, cash movements, reconciliation | `capabilities.execution` + `SyntheticFactsPort` | confirmed buys against a published plan, CAS corrections |
| Daily Job | call `P12DesktopApplication` from a Job adapter | not implemented |
| Production runtime | assembly bound to adopted accounts and PostgreSQL | not assembled; routes return 503 |

## Local run

```bash
cd product-web && npm ci && npm run build && cd ..
PYTHONPATH=src .venv/bin/python -m quanthus.platform.p12.local_server --state-dir /tmp/p12-local
# open http://127.0.0.1:8765/p12 ; users and one-off passwords are in /tmp/p12-local/local-users.json
PYTHONPATH=src .venv/bin/python -m quanthus.platform.p12.cli --state-dir /tmp/p12-cli workbench
```
