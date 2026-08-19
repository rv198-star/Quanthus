# P4 Event Scan DWD Historical PIT Source Audit

| Field | Value |
| --- | --- |
| Evidence status | blocked finding |
| Evidence date | 2026-08-06 |
| Governing work | `P4-EVENT-002` / GitHub Issue #74 |
| Governing design | `docs/design/lld/06-event-library.md` |
| Candidate rule | `p4-event.price-volume-state-transition@1.0.0` |
| Source policy | `p4-event-source.new-chain-dwd-base@1.0.0` |
| Data authority | New-chain CN01 DWD mirror only |

## 1. Decision

The new-chain DWD remains the only allowed physical data authority for P4. The old dual-lane
DWD/DWS, operational or portable ADS, release sets, historical bundles, and mutable pointers remain
forbidden.

A real 2012-2021 scan is nevertheless blocked. The accepted candidate rule requires every consumed
DWD `knowledge_from` to be no later than historical T close. The current DWD builders assign later
Base-build knowledge to several required historical objects. Reading those rows literally therefore
fails the approved PIT gate, while silently substituting economic observation time would change the
approved semantics without a versioned contract.

## 2. Reviewed evidence

| Required object | Implementation evidence | Finding |
| --- | --- | --- |
| `dwd_security_bar` | `src/quanthus/pipelines/securities_warehouse/market_observation.py` | Output `knowledge_from` is the builder `cutoff`, including historical daily bars |
| `dwd_tradability_state` | `src/quanthus/pipelines/securities_warehouse/tradability_state.py` | Aggregated state uses `build_cutoff()` as `knowledge_from` |
| `dwd_security` / `dwd_listing` | `src/quanthus/pipelines/securities_warehouse/listed_company.py` | Identity knowledge is at least the later build `knowledge_cutoff` |
| Candidate contract | `config/p4-candidate-price-volume-transition.research.v1.json` | Every PIT input declares `knowledge_from <= T close` |
| Scan implementation | `src/quanthus/event_library/scan.py` | Rows with later knowledge fail closed as `knowledge_after_t_close` |

The focused negative test mutates an otherwise valid 2012 candidate row to a 2026 knowledge cutoff.
The result contains zero raw triggers and one explicit `knowledge_after_t_close` rejection.

## 3. Forbidden shortcuts

The scan must not:

- treat DWD Base build cutoff as historical T knowledge;
- replace `knowledge_from` with `observation_start`, `observation_end`, effective dates, or source
  dates without a reviewed versioned projection;
- recover historical visibility from the obsolete ADS or portable research bundles;
- open 2022-2024 arbitration, 2025 permanent blind, or 2026 simulation observations to calibrate a
  workaround; or
- weaken the approved candidate-card digest in place.

## 4. Required resolution

Before the physical DWD reader and full development scan can run, one of the following must receive
explicit design and contract authority:

1. rebuild or correct the new-chain DWD objects so their historical `knowledge_from` represents the
   approved economic-availability semantics; or
2. define a versioned P4 historical PIT projection that maps each required DWD object to an
   independently justified economic-availability timestamp, then revise and reapprove the candidate
   rule if that projection changes its exact input-field contract.

The second route must be table-specific. Market bars, calendars, listing identity, suspension/ST,
price-limit rules, adjustment factors, and static concentration mapping do not share one universal
availability timestamp.

## 5. Verification and external state

- Focused P4 source, trigger, entry, P2 adapter, and documentation suite: 59 passed.
- Full repository `make check`: 894 passed, including doctor, compile, tests, and planning preview.
- Ruff and strict mypy passed for the affected implementation.
- No market-data file or forbidden partition was read.
- The DevSpace account could identify the OCI DWD directory but lacked read permission for its
  protected contents, so the physical Base manifest and row values were not independently inspected
  in this audit. The blocking finding is established from the accepted builder and candidate
  contracts and must be verified against the physical Base once read-only access is granted.
