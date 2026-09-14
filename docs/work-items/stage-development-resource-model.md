# P3-STAGE-013 — Era × Board Exposure-Aware Development Reservoir

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | `P3-STAGE-012 / CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY` |
| Machine decision | `../../config/stage-learning/stage-development-resource-model.v1.json` |
| Four-party review | `../reviews/stage-development-resource-four-party-review-20260914.md` |
| Current state | `READY_METADATA_ONLY_CAPACITY_CENSUS`; no result-bearing market-data authority |

## Objective

Repair the development-resource accounting model before another Stage experiment. The successor does not add a random replacement pool. It counts reusable research capacity by:

`economic identity × non-overlapping time window × listing board at time × evidence role`.

The design preserves identity-global exclusion for the exact protected 2021 evaluation plan, while development exposures consume only their exact windows and are never misrepresented as identity-independent evidence.

## Selected structure

- **Time:** institutional/rule-based eras, not retrospective bull/bear labels.
- **Listing board:** hard stratification from PIT `dwd_listing.board_code` using MAIN (`SSE_main` + `SZ_main`), ChiNext and STAR.
- **Exposure:** L0/L1 metadata/reservation is reusable; L2-L4 consume a development window; L5 preserved evaluation remains globally excluded.
- **Economic/theme sector:** not a hard historical axis in v1 because authoritative PIT sector history is incomplete. Current/static industry mapping cannot be promoted to historical truth.
- **Recent partitions:** 2021 preserved evaluation remains unread; 2022-2024 remain closed; 2025 permanent blind remains closed.

## Work sequence

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-013-A | Metadata-only era × board × exposure capacity census | P3-STAGE-012 closeout | engineering accepted; exact one-shot metadata Job ready after RX6800 deployment; counts only, no price/morphology/labels/scoring; `../acceptance/stage-development-resource-capacity-census-engineering-20260914.md` |
| P3-STAGE-013-B | Freeze exact successor board-era quotas and reusable-window policy | P3-STAGE-013-A | blocked on census; must freeze before result-bearing read |
| P3-STAGE-013-C | Build successor development preregistration | P3-STAGE-013-B | blocked; no inherited 512 gate unless re-derived from actual role/capacity need |
| P3-STAGE-013-D | Result-bearing successor development | P3-STAGE-013-C | blocked; requires separately accepted preregistration and runtime contract |

The A execution contract is now frozen before its first metadata census. It uses exact 61-open-session non-overlapping windows, the PIT listing-board axis, exact bound predecessor exposure artifacts and an immutable 160-identity L5 exclusion. The Worker/helper change adds only the one-shot `quanthus.stage-development-resource-capacity-census.v1` route.

## P3-STAGE-013-A required census output

The census must be deterministic and metadata-only. It reports:

1. unique economic identities by era × board family;
2. non-overlapping usable time windows by era × board family;
3. exposure counts at L0-L5;
4. capacity released by replacing whole-identity burn-down with disjoint-window development reuse;
5. exact preserved-2021 overlap and remaining capacity after preserving its identity-global exclusion;
6. capacity-loss reasons, including listing age/history insufficiency, board absence in an era, identity ambiguity, and protected-evaluation overlap.

The census must **not** read price paths, Structure morphology, atomic references, candidate outputs, labels, future outcomes, or preserved evaluation rows.

## Quota freeze rule

Exact quotas are not guessed from P012's failure. They are frozen only after A reports metadata capacity and before any result-bearing data is read.

The allocator must be representation-first:

- every structurally present board-era cell is represented before extra density is assigned to already represented cells;
- MAIN cannot dominate only because it has the largest population;
- additional capacity is allocated deterministically from metadata-only cell capacity;
- no random append is allowed to reach a target count;
- repeated identities across disjoint development windows are reported separately and never counted as identity-independent validation.

## Board/time boundary

The initial development reservoir ends at 2020-12-31. V1 uses three institutional eras:

1. `PRE_STAR`: 2012-01-01 through 2019-07-21;
2. `STAR_PRE_CHINEXT_20PCT`: 2019-07-22 through 2020-08-23;
3. `STAR_AND_CHINEXT_20PCT`: 2020-08-24 through 2020-12-31.

These boundaries represent listing-board availability and versioned trading-rule structure. They do not claim that each interval is a homogeneous bull/bear market.

## Why 2024 technology leadership is not itself a sampling label

The observed importance of technology/growth boards is a valid reason to demand board/regime robustness. It is not authority to retrospectively choose 2024 technology winners as development cases. V1 therefore changes the sampling architecture—board-at-time becomes explicit—without opening 2022-2024 or using an outcome-defined market-regime label.

## Hard boundaries

- P012 G1 remains failed and immutable; no rerun or reinterpretation.
- No candidate/profile/threshold/gate changes occur in P3-STAGE-013-A.
- P008 relocation remains frozen.
- Exact preserved-2021 plan remains unread and globally identity-protected.
- 2022-2024 remain closed pending separate authority.
- 2025 remains permanent blind.
- No daily/strategy/trading/production runtime is opened.

## Exit

P3-STAGE-013-A may close only with a reproducible metadata capacity matrix and exposure ledger sufficient to decide exact quotas. If one or more board-era cells are structurally scarce, that scarcity is reported and resolved in B before any result-bearing read; it is not patched by random replacement after outcomes are seen.
