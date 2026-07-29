# P3 Capital Reservation Competition Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Deterministic candidate competition for explicitly available empty units |
| Governing design | `docs/design/lld/05-position-execution.md` |
| Governing work | `P3-RISK-001` |
| Dependency evidence | `docs/acceptance/p3-concentration-capacity-slice-20260728.md` |

## 1. Decision

`P3-RISK-001` passes a second bounded slice. Already-governed entry candidates compete in the
frozen aggregate-score-descending/security-code-ascending order for caller-supplied units whose P3
lifecycle and P2 ledger both prove they are empty. Each accepted result becomes a typed reservation
and immediately participates in later same-batch capacity checks.

The slice assigns no cash amount and has no execution authority. It does not create a Signal,
OrderIntent, Order, Fill, position transition, or notification.

## 2. Risk correction

An accepted same-day reservation now carries level-one industry identity and mapping version.
Industry capacity counts active positions plus accepted reservations, preventing multiple
same-batch candidates from independently observing stale capacity and exceeding the frozen limit
before their fills exist. Daily reservation capacity remains enforced by the same evaluator.

## 3. Accepted behavior

- Candidate input order cannot change priority; equal scores resolve by security code, then stable
  opportunity and decision identity.
- Available unit input order cannot change assignment; empty units sort by unit identity and
  lifecycle digest.
- A concentration-rejected candidate does not consume the considered unit, so the next candidate
  may compete for it.
- An accepted reservation consumes its unit plus its industry and daily capacity for every later
  candidate in the batch.
- Mixed mapping versions, duplicate identities, active/reserved unit overlap, non-empty ledgers,
  and non-empty lifecycle stages fail closed.
- Every decision and the batch declare `execution_authorized=false` and serialize canonically.

## 4. Evidence map

| Concern | Evidence |
| --- | --- |
| Reservation-aware capacity | `src/quanthus/concentration.py` |
| Candidate competition and unit assignment | `src/quanthus/capital_allocation.py` |
| Capacity regression cases | `tests/unit/test_concentration.py` |
| Allocation and determinism cases | `tests/unit/test_capital_allocation.py` |

## 5. Residual boundary

The number and notional of available units remain explicit caller inputs. Trial size, market-wide
maximum exposure, funding reservation, concrete score/action mapping, and multi-unit execution are
still governed by `P3-POLICY-001`, `P3-AGG-002`, and `P3-POS-002`. No values for those policies are
introduced by this slice.

## 6. Verification

- Focused concentration, allocation, and strategy replay suite: passed 23 tests.
- Focused Ruff, formatting, and diff checks: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 279 tests.
