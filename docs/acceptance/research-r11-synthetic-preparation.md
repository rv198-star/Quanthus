# R11 C13/C15 Synthetic Preparation Acceptance

Date: 2026-11-16  
Task: #690  
Contract: `config/research/r11/research-contract.v1.0.1.json`  
Semantic digest: `sha256:e09576758b35283cc1971e8f741d87abab26601964c8315e3e2b7db3d76823aa`

This receipt covers the bounded shared policy core for P11 C13/C15 and P12 W3/W4. It does not adopt a daily account policy, read market rows, qualify A02 for daily decisions, or replace the later complete R11 comparison.

## Implemented behavior

- Candidate identity persists across observation, reservation and actual position evidence. The original T+1 window is not extended and a same-event reobservation cannot rearm entry.
- Expired recommendation, actual order and actual position are separate fields. A late fill can retain actual held state while the original recommendation remains expired.
- Fixed buckets keep N, locked B, reserve and non-positive adjustment distinct. Same-batch settlements are sorted by bucket id and share reserve deterministically.
- Failed cycle preparation returns the exact input FundingState. Duplicate commands return the stored receipt without a second transfer; command-body conflicts and revision conflicts fail closed.
- Late fees consume reserve first and record the remainder as negative adjustment without changing a later cycle's locked B.
- Quantity is the largest whole lot under target budget, tradeable cash and stock/industry/total limits using conservative post-fill equity. Unrecognized pending-buy fees and adverse price-bound loss reduce E_floor; recognized payables and pending sells do not create a second release.
- Missing required input blocks dependent new risk and preserves existing-position exit handling.

## Synthetic evidence

- `config/research/r11/lead-golden-cases.json`: 21 immutable independent cases.
- `tests/unit/test_product_daily_r11.py`: goldens, independent brute-force maximum quantity, reversed and duplicate commands, settlement preconditions, late-profit rejection, reason priority and three reconstructed stories.
- `tests/contract/test_product_daily_r11.py`: selected-contract binding, frozen authority, A02 inconclusive retention, golden identity and no daily-adopted state.
- P2 seam: a synthetic `BacktestEngine` buy, held position and sell close into `UNIT_SETTLEMENT`; the recorded profit transfer is then represented as C15 reserve without changing the original P2 policy.
- `scripts/r11_synthetic_verify.py`: runs the focused suite and emits expected/actual golden JSON, story digests and a machine receipt.

## Boundary and follow-up

The package has no account database, HTTP/UI, report correction engine, market-row reader, paid reference, training, historical capital replay, daily serving or production deployment. P12 must still supply actual account limits, atomic persistence/CAS, report/fill correction and OPS/USE evidence. P11 must still qualify A02's daily semantic authority and the development reader's per-decision PIT publication purpose before a complete historical comparison can be adopted.

The later P11 research adaptation and cold complete comparison is estimated at 46–92 new active hours, excluding the existing P12 W1–W4 engineering estimate and P10 data work. Compute and storage are not frozen until the exact historical manifest, universe, fees and replay scope are supplied.
