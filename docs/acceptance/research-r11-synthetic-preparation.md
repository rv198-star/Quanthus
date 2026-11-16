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

## Internal acceptance repair 1.1.0

The original synthetic receipt was preserved after the research lead reproduced seven functional defects and additional strict-input/oracle gaps. Implementation `1.1.0` fixes the complete R1–R10 batch:

- completed settlement history authenticates old-cycle late fees/refunds after a bucket enters a new cycle;
- every reserve inflow first extinguishes negative adjustment;
- an unsettled closed cycle cannot be reused;
- canonical command operation and business payload are bound to the immutable receipt;
- transient candidate blocks are reevaluated inside the original window while expiry remains terminal;
- entry consumes the original expert runner's explicit action;
- composite entry stores one atomic replayable result and no prepare-only commit;
- nonpositive E_floor is directly blocked;
- boolean, integer, Decimal and cash-quantum boundaries reject malformed or binary values;
- every frozen golden key is compared independently, including corrected Q02 exposure, distinct F07/F08 old/new cycles and three causal state traces.

`config/research/r11/repair-dispositions.v1.json` records the original/new actual and regression witness for each finding at implementation 1.1.0. `ds-return-002` explicitly returned a partial package: the fresh final Linux check was NOT_RUN after its wall guard expired. The research lead subsequently found and repaired three further boundaries in 1.1.1; its new Linux receipt is a separate acceptance and the original receipts remain historical.

## Lead internal acceptance 1.1.1

The entry size now intersects the configured fraction with the original expert maximum. Composite entry returns balanced funding postings and binds them into its immutable result; replay emits no new postings. The verifier fails when either required actual-evidence file is missing, and its final passed flag includes expected/actual validation. Five independent regressions cover these boundaries. The frozen 21-case JSON is unchanged.

Tested source: GitHub `deb4422ef5ae2994328729d89cdbefc3daa835fd`, tree `6380459859db2f41ddc2071ab468928c6ee6abde`. CN02 reconstructs the exact tree from authenticated GitHub Git objects plus its already verified base objects. Fresh CN02/Python3.10 make check passed: 9388 passed, 35 skipped, 63 warnings; wall865.633s, CPU848.62s, RSS611296KiB. Ruff and five-module mypy (--follow-imports=skip) passed. Exact native log SHA256: `23d556841c47ac4c20a29c0ae757640c56b3effdde82ca04bbcd5214a77257ec`. The convergence package retains optional-dependency skip reasons and all failed attempts. Final independent audit remains pending.
