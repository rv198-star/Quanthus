# P3-STAGE-024-B Cross-Era Clustered-Identity Admission Engineering Acceptance

Date: 2026-09-16
Scope: engineering and synthetic/contract validation only
Result: PASS

## Accepted revision scope

P024-B implements only the runtime required by the preregistered P024-A contract. It binds the exact
immutable P023 no-pass artifact and can consider only early-STAR fallback rows whose P023 attempt was
`SKIPPED_REALIZED_IDENTITY_CONFLICT` with `market_read=false`. Completed P023 readiness observations
are immutable and cannot be rerun.

The runtime preserves one economic identity per Era × Board cell. A repeated identity in another cell
is eligible only when the exact windows are pairwise non-overlapping. Repeated rows are persisted as
one explicit identity cluster; final artifacts report both row count and unique-cluster count and the
independent-identity evidence count is the unique-cluster count, never the row count.

The RX6800 Stage Worker contract adds a one-shot, idempotent
`quanthus.stage-clustered-identity-admission.v1` Job kind plus the bounded AgentDock
`submit-p024 <exact-revision>` control. A different revision after budget reservation is rejected as a
retry/replacement. P024 remains semantic-blind.

## Verification

Focused validation before repository-wide acceptance:

- clustered-admission runtime, Worker one-shot/idempotency, AgentDock control and P013-P024 planning
  contracts: `175 passed`;
- overlap candidates are skipped without a market read;
- synthetic non-overlapping cross-era reuse activates at most one identity per cell and counts the two
  rows as one independent cluster;
- planning keeps P020-C/D/E blocked and does not expose Stage1/12 semantic authority.

Repository-wide `make check` then completed with:

- `2515 passed`;
- `7 skipped` because production expert artifacts or disposable PostgreSQL acceptance services are not
  mounted/configured in this DevSpace verification environment;
- `0 failed`;
- planning sync check passed.

The temporary DevSpace validation venv was aligned to the repository-frozen dual-lane runtime
(`duckdb 1.5.5`, `pyarrow 23.0.1`, `polars 1.43.0`) so unrelated runtime-identity tests exercised their
frozen contract rather than the verifier's package resolver state. No repository dependency declaration
was changed.

## Authority ceiling

This acceptance authorizes only deployment of the exact accepted P024 engineering revision to the
RX6800 Stage Worker and one P024 Job under the frozen budget. It does not authorize Stage1/12 review,
threshold search, candidate scoring, P020-C execution, preserved 2021, 2022-2024, 2025, daily/strategy
output or trading.

A runtime PASS can only open a separately frozen P020-C preregistration. A runtime shortfall cannot
open P025 automatically; it returns to four-party portfolio review.
