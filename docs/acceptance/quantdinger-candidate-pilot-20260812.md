# QuantDinger candidate compatibility pilot acceptance

> **Superseded for DWD and performance claims.** This record preserves the 2026-08-12 adapter
> compatibility result. Its +69.630054%, 30-fill result was not produced directly from
> `dwd-base-202607-v2.1` and is no longer the current candidate metric. Use
> `dwd-v2-1-quantdinger-conformance-20260813.md` for the direct DWD v2.1 result, cross-engine
> reconciliation, and current +60.279056% P2 result.

| Field | Value |
| --- | --- |
| Date | 2026-08-12 |
| Status | compatibility replay accepted; governance admission pending |
| Candidate | `quantdinger.strategy-v2-bullish-three-lines@11.0.0-quanthus.1` |
| Upstream | `OpenByteInc/QuantDinger@e64e1c227bf3174e441a42143620179b286387e1` |
| Evidence | `/home/ubuntu/data/quanthus/research/quantdinger-candidates/bullish-three-lines-development-v1.json` |

## Boundary

Only the signal semantics of QuantDinger template `strategy_v2_bullish_three_lines` are adapted.
The QuantDinger runtime, market providers, backtest engine, and fill model are not imported or
used. Source symbol `CNStock:600519.SH` is explicitly mapped to Quanthus security identity
`600519`; an absent identity fails closed.

The replay reads Quanthus DWD/PIT frames through the accepted historical reader. Decisions use
history visible at T close, and fills are refereed by Quanthus P2 under
`close-to-next-open-v1`. Missing or invalid bars block entry for the affected day without aborting
the full batch or forcing a held-position sale. Successful replay produces a candidate in
`replayed` / `pending-review`; it cannot grant governance admission or execution authority.

## Real replay evidence

The development bundle
`strategy-replay-development-1f1ea4d-v1` was replayed from 2012-01-04 through 2021-12-30:

| Result | Value |
| --- | ---: |
| Decision days | 2,430 |
| Entry / exit decisions | 17 / 15 |
| P2 fills | 30 |
| Closed trades | 15 |
| Total return | +69.630054% |
| Maximum drawdown | 10.377494% |
| Pending exit | false |
| Governance decision | `pending-review` |
| Execution authorized | false |

Evidence digest
`sha256:c44145db2ab268389a3b593e6a1f66ba388512a8b045c23d37bc150c9bdc50cb`
was independently recomputed from the canonical payload and matches. The result digest is
`sha256:98148865ca16bad7b2d71758a938f0deadc765b0485a2a528d843070092aed13`;
the evidence file SHA-256 is
`035b1693a4fbe40fe79bbe4752bef0e3647c5ca4deb450edf54e223b6e6f1c1b`.

## Limits and decision

The accepted development ADS declares
`capability_fixture_without_per_decision_publication_proof`. It is the same development replay
carrier used by formal P3 work, but this pilot does not claim independently proven canonical
publication at every historical decision time. Arbitration and blind partitions remain sealed.

One full replay took roughly 14 minutes because historical frames are queried per decision day and
are not batch-cached. This is acceptable for the first compatibility proof, but it is a batch
throughput limitation before validating a larger candidate catalogue.

The compatibility vertical slice is accepted: QuantDinger signal semantics can enter Quanthus as
a candidate and complete DWD/PIT plus P2 replay. The strategy itself is not governance admitted,
not an execution strategy, and not evidence that other QuantDinger templates are compatible.

## Product and deployment acceptance

The catalogue now exposes five candidate versions, zero governance admissions, and zero execution
strategies. All five remain `candidate-version` objects; the rejected baseline retains its rejection
decision without becoming a non-candidate system strategy. The QuantDinger pilot is rendered as a
source-labelled `replayed` / `pending-review` candidate.

- Product source contracts: 5 passed.
- Candidate/platform focused tests: 24 passed.
- Platform contracts excluding the intentionally reopened PK4 completion assertion: 35 passed.
- Ruff and mypy for the changed backend boundary: passed.
- Product production build: passed.
- Public API: catalogue `2026-08-12.3` returned 5 candidates, 0 governance admissions, and 0
  execution-authorized strategies.
- Public Playwright: 4 passed across desktop Chromium and mobile Chromium, including candidate
  counts, QuantDinger row, removed capabilities, navigation, and horizontal overflow.
- Public edge: root returned HTTPS 200, external `/metrics` returned 404, and all three Docker
  services were healthy. PostgreSQL data and the existing administrator were preserved.

## DWD and replay-window correction

User review found that the Product Web showed the 2012-2021 development partition beside a generic
"data" label, which could be read as the Quanthus DWD coverage range. The catalogue now separates:

- DWD authority at the time of the later correction: `V2-Build202607 / dwd-base-202607-v2.1`,
  through 2026-07-31, with
  dataset-specific historical starts;
- ADS governance: development 2012-2021, sealed arbitration 2022-2024, sealed permanent blind 2025,
  and accepted recent-research availability from 2026-01-01 through 2026-07-30; and
- candidate evidence: all five displayed results currently bind decisions from 2012-01-04 through
  2021-12-30 and T+1 context through 2021-12-31.

This correction does not relabel old results as 2026 evidence. The recent-research bundle exists,
but none of the five catalogue candidates has yet produced a published replay over it. The Web
therefore says "尚未回放" for that partition. Backend contracts reject a DWD cutoff regression,
missing replay partition, or candidate evidence outside its declared partition.
