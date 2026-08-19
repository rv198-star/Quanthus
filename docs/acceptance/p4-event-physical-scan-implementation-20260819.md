# P4 Event Physical Scan Implementation Checkpoint

| Field | Value |
| --- | --- |
| Status | implementation-complete; real-corpus runtime acceptance pending |
| Work item | `P4-EVENT-002` / GitHub Issue #74 |
| Branch | `issue-74-forward-scan-v21` |
| Rebased main | `ac6935c` |
| Candidate rule | `p4-event.price-volume-state-transition@1.0.0` |
| Candidate digest | `sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50` |
| Source policy | `p4-event-source.direct-dwd-v2.1-retrospective@2.0.0` |
| Source-policy digest | `sha256:3b4992128ee40f8896e8b703806ea19b67da71e2ce60567a5f95021ee9a64489` |
| Split policy | `p4-event-date-split@1.0.0` |
| Split digest | `sha256:10c58c9f402327b2c1e9e3ba9782cce909216b795018b89586bc5a9b210fb684` |
| Data authority | DWD Base V2.1 + correction-001 through the accepted direct-DWD provider |

## Implemented physical path

`src/quanthus/event_library/physical_scan.py` closes the repository-side physical implementation
for the first P4 case without changing the approved candidate rule or split digests.

The path is:

```text
DWD Base V2.1 + correction-001
  -> accepted Direct-DWD job-local provider
  -> exact Decimal / identity / lineage candidate projection
  -> stable-listing streaming forward scan
  -> security-level isolation and deterministic concentration
  -> exact P1DailyExecutionEvidence at T+1
  -> accepted P1-to-P2 buy-open mapping
  -> executable / not_executable partition
  -> immutable event-library manifest + READY
```

The physical projection adds only lineage-preserving fields to the existing direct-DWD replay
materialization. Existing replay `DOUBLE` columns remain unchanged; P4 uses additional raw DWD
Decimal OHLC/turnover columns so the frozen threshold comparisons do not depend on floating-point
round trips. DWD internal security, company, listing, venue, effective-time, raw knowledge-time, and
bar-source evidence are retained for event identity and audit.

The forward scanner now exposes a series-streaming API. It processes one stable listing interval at
a time and retains only raw triggers globally, while the final 22-session isolation remains keyed by
security across non-overlapping relisting intervals. Static level-one industry membership is applied
only after a raw trigger exists; it may fail closed during concentration but cannot participate in
the event signal.

The historical reader now exposes the same `P1DailyExecutionEvidence` used internally to build P2
market sessions, including a date-batched read path. P4 therefore does not reproduce matcher rules:
its T+1 partition is derived from the exact accepted P1/P2 execution-state adapter, including
`NON_TRADING_OBSERVED` cancellation.

## Package contract

`run-p4-event-scan` publishes one immutable research-only directory containing:

- `projection/candidate_daily.parquet`;
- `projection/projection.json`;
- `events.jsonl`;
- `entry-partition.jsonl`;
- `summary.json`;
- `manifest.json`; and
- `READY`.

The manifest binds code revision, source-policy digest, candidate-rule digest, split-policy digest,
projection identity, event counts, every retained file SHA-256 and size, and one content digest.
`verify-p4-event-scan` independently rechecks the physical inventory, checksums, READY binding, and
content digest.

## Verification completed

- synthetic physical Direct-DWD -> projection -> streaming event scan -> P1/P2 T+1 partition:
  passed;
- streaming scan and in-memory scan produce identical results on the same input;
- non-overlapping relisting intervals are accepted while overlapping same-security chunks fail
  closed;
- static-industry absence is proven to occur after raw-trigger formation;
- P1 execution-evidence batch and single-item paths agree;
- focused P4/P2/document suite, including the Direct-DWD physical SQL projection: 73 passed;
- changed-file Ruff checks and `git diff --check`: passed;
- repository `make check`: 1,168 passed and 3 skipped; the skips are the existing optional
  PostgreSQL tests because `QUANTHUS_TEST_POSTGRES_DSN` is not configured;
- CLI `run-p4-event-scan` and `verify-p4-event-scan` entry points parse successfully.

Repository-wide strict mypy is not a release gate for this change and still reports the previously
known project-wide typing debt in attribution, ODS, legacy CLI, and related modules. The two typing
issues introduced by the first P4 refactor were corrected before this checkpoint.

## Runtime acceptance still required

No production DWD permission was changed to manufacture a successful test. The current DevSpace
identity is `devspace` and cannot traverse the protected OCI Base directory
`/srv/quanthus/dwd-dws-v2.1/base`, which is owned by the production `ubuntu` runtime boundary. The
correction package is visible, but the Base payload is intentionally not readable from this
workspace identity.

A direct CLI probe reached the physical-runtime boundary and failed closed with
`cannot read DWD manifest: /srv/quanthus/dwd-dws-v2.1/base/dwd-base-202607-v2.1/manifest.json`.
No source-policy, provider-config, projection, or scan error occurred before that protected read.

The remaining acceptance step must therefore execute `run-p4-event-scan` under an already accepted
read-only runtime identity that can read the Base and correction. It must produce two independently
named 2012-2021 runs. #74 may close only if both runs verify and their event-library content and
manifest identities are byte/digest identical, with no arbitration, blind, simulation, governance,
or execution authority opened.
