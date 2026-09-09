# P3-STAGE-001-J development capacity census result — 2026-09-09

## Disposition

**ACCEPTED as development-only population-sizing evidence.**

This evidence does not qualify the stage candidate, does not reopen 2022-2024 arbitration, does not select or read 2025 permanent-blind, and does not authorize any holdout execution.

## Run identity

- Job: `stage_5c4799ffdbcb4f899969c186249d625f`
- Code revision: `16a291ab645e653a171dcfd96d4357461fae1e1a`
- Config: `config/stage-learning/sample-capacity-census.v1.json`
- Run: `stage-capacity-census-20260909T084024Z`
- Manifest: `sha256:3dae03eefbc4769b9116c3f65b6e562cc6f1047cfb7bb3f0aecc0f25c61d504e`
- Development-only eligible population: 3,659 securities
- Maximum runtime population: 512 securities
- Admissions: 313,344
- Structure episode observations: 300,509
- Arbitration read: false
- Permanent-blind read: false

The Worker Job itself ended with `exit_code=1` only because the CLI attempted to serialize a Python `date` object with plain `json.dumps` after the immutable artifact had already been fully written. The artifact independently verifies. The serialization defect is fixed separately and does not alter the census result.

## Capacity curve

| Cohort | Started | Early-held | Loss | Reclaimed | Mature-held | All target caps |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 64 | 167 / 200 | 180 / 180 | 100 / 100 | 80 / 80 | 26 / 80 | NO |
| 128 | 200 / 200 | 180 / 180 | 100 / 100 | 80 / 80 | 80 / 80 | YES |
| 256 | 200 / 200 | 180 / 180 | 100 / 100 | 80 / 80 | 80 / 80 | YES |
| 512 | 200 / 200 | 180 / 180 | 100 / 100 | 80 / 80 | 80 / 80 | YES |

The first nested population satisfying every target cap is 128 securities.

The raw unique mature-held capacity scales materially with cohort size:

- 64 securities: 95
- 128 securities: 196
- 256 securities: 390
- 512 securities: 704

Therefore the F shortfall was not evidence that mature-held events are intrinsically too rare in the market. It was primarily a population-sizing problem combined with the one-context-per-episode and ordered non-reuse sampling contract.

## Successor population decision

The successor protocol freezes **512 securities** as the cohort size for a future explicitly authorized holdout evaluation.

Rationale:

1. 128 is only the observed minimum in this development window.
2. 256 has ample reserve, but the RX6800 showed no material resource-pressure concern at 512.
3. Semantic review remains capped at 640 contexts, so expanding the cohort does not multiply panel-review cost; it mainly increases the deterministic prerequisite Structure build.
4. 512 supplies a large rare-stratum reserve and reduces the chance that a different market period falls below target caps.
5. This freezes only the cohort **size**. Specific securities, seed, cohort date, and holdout partition must still be frozen before any future holdout read.

## Claim limits

- This is development-only sample-capacity evidence, not qualification evidence.
- 2022-2024 remains consumed and cannot be called fresh again.
- 2025 remains unopened and unselected.
- No specific future holdout securities are selected by this result.
- The 640 target caps and semantic hard gates are unchanged.
