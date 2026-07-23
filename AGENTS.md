# Quanthus Engineering Rules

## Invariants

- Preserve point-in-time semantics. Features may only use information available at the
  declared `knowledge_cutoff`.
- Daily signals are produced after the T close and may first execute at the T+1 open.
- Model or representation outputs never bypass the expert and veto decision path.
- Missing or invalid data blocks new entries and raises an alert. It is not structural
  damage and must not force a sale.
- Every decision, rejection, fill, policy version, and data snapshot must be auditable.
- Do not silently change thresholds. Update the versioned policy and contract tests.

## Current scope

- P1 may be implemented. C1, C2, and S1 block P1 acceptance.
- P2 must not begin until the Chapter 01 acceptance gate is marked passed.
- Do not implement the full expert set or training layer during the P1 vertical slice.

## Development

- Support Python 3.10 or newer.
- Keep production logic under `src/quanthus`; notebooks may only call production modules.
- Do not commit market data, credentials, run artifacts, or local databases.
- Prefer deterministic ordering and explicit reason codes over implicit behavior.
- Run `make check` before a handoff.
