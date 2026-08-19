# P4 Event V2.1 Source Recovery Checkpoint

| Field | Value |
| --- | --- |
| Evidence status | implementation checkpoint; `P4-EVENT-002` remains open |
| Evidence date | 2026-08-19 |
| Governing work | `P4-EVENT-002` / GitHub Issue #74 |
| Governing design | `docs/design/lld/06-event-library.md` |
| Candidate rule | `p4-event.price-volume-state-transition@1.0.0` |
| Candidate semantic digest | `sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50` |
| Source policy | `p4-event-source.direct-dwd-v2.1-retrospective@2.0.0` |
| Source semantic digest | `sha256:3b4992128ee40f8896e8b703806ea19b67da71e2ce60567a5f95021ee9a64489` |
| Delivery partition | development only, 2012-2021 |

## 1. Recovery decision

The 2026-08-06 source checkpoint remains valid evidence for why the original Draft PR #77 could
not run against DWD Base V2. Issue #78 has since closed with the independently audited
`dwd-base-202607-v2.1` successor, and correction-001 closes the bounded zero-activity tradability
and company-action findings discovered by external strategy conformance.

The recovered #74 implementation therefore replaces the obsolete V2 source binding with the
current immutable authority:

- Base package: `dwd-base-202607-v2.1`;
- Base manifest: `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b`;
- Base content: `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9`;
- correction: `dwd-base-202607-v2.1-correction-20260813-001`;
- correction manifest: `sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde`;
- correction content: `sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da`;
- direct provider contract: `config/replay-data-provider.development.v2.json`.

The old dual-lane DWD/DWS, operational or portable ADS, historical replay bundles as P4 authority,
release sets, and mutable pointers remain forbidden.

## 2. Historical visibility boundary

DWD V2.1 is a reproducible present-day historical reconstruction. Its raw historical
`knowledge_from` does not prove that each row existed with the same content on the original market
date. The source recovery does not erase or reinterpret that fact.

`p4-economic-availability-development-v1` is a separate purpose-specific P4 projection. It retains
raw DWD `knowledge_from` in lineage and supplies a table-specific economic-availability coordinate
only inside the authorized development research partition:

| Source table | P4 usage | Development availability rule |
| --- | --- | --- |
| `dwd_security_bar` | candidate signal | session close |
| `dwd_listing` | eligibility | effective interval must already cover session close |
| `dwd_security` | eligibility | effective interval must already cover session close |
| `dwd_trading_calendar` | session coordinate | session close |
| `dwd_tradability_state` | eligibility | session close after correction overlay |
| `dwd_price_limit_regime` | T+1 execution evidence | effective interval at execution open |
| `dwd_adjustment_factor` | candidate exclusion only | conservative effective-date exclusion |

This projection keeps `per_decision_publication_proven=false`. It is not admissible for arbitration,
permanent blind, simulation, governance admission, or execution authorization. It also does not
change the already approved candidate rule, threshold, feature, split, isolation, or concentration
digest. A future source change that changes the logical candidate-input contract requires a new
candidate rule version and renewed human approval.

## 3. Recovered implementation

The old Draft PR #77 pure implementation is retained rather than rewritten from scratch:

- `src/quanthus/event_library/scan.py` performs chronological outcome-blind candidate generation;
- exact rational arithmetic avoids hidden float threshold drift;
- evaluated rows, raw triggers, same-security isolation, concentration retention, split rejection,
  and final candidates reconcile deterministically;
- future returns, labels, fills, and post-T features are not accepted as scan inputs;
- T+1 executable/not-executable classification reuses the accepted P1-to-P2 adapter;
- `NON_TRADING_OBSERVED` now shares the same `security_not_trading_observed` buy-cancel reason as
  the current P2 engine; and
- no order or fill is created by the P4 entry partition.

## 4. Verification at this checkpoint

The focused V2.1/correction source contract, development availability projection, forward-scan,
event-contract, split, documentation, and current P2 buy-entry regression passed together:
**74 passed**. Repository `make check` then passed with **1,158 passed and 3 skipped**; the three
skips are the existing optional PostgreSQL acceptance paths that require
`QUANTHUS_TEST_POSTGRES_DSN`.

The physical full-corpus event projection has not yet been claimed as complete. The current DevSpace
worktree can see the correction package but receives a permission denial when traversing the
protected Base root; no data permission was changed during this checkpoint. The accepted production
direct-DWD provider contract remains the intended read path. A full 2012-2021 scan, deterministic
rerun comparison, and resulting event-library manifest are still required before closing #74.

No arbitration, blind, simulation, governance, execution, Base mutation, correction mutation, or
market-data write occurred during this checkpoint.
