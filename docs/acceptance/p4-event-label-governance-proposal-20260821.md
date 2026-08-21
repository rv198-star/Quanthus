# P4-LABEL-001 Outcome-Blind Governance Proposal

| Field | Value |
| --- | --- |
| Status | proposal implemented; human approval required before real outcome reads |
| Work item | `P4-LABEL-001` |
| Predecessor | `P4-EVENT-002` accepted on production A/B revision `27a78f95382b99eb9e647875be208873d94379a1` |
| Candidate corpus identity | 36,820 deterministic P4 candidates; no future outcome values read for this proposal |
| Outcome card | `p4-event.outcome-first-passage@1.0.0` |
| Outcome digest | `sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca` |
| Structural-break card | `p4-event.structural-break-trend@1.0.0` |
| Structural-break digest | `sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc` |

## Outcome-blind boundary

This proposal was built from the approved P0/PRD/HLD/LLD contracts, the accepted P4 candidate and
split-policy identities, and already-approved market-data semantics. It did not inspect the
2012-2021 candidates' post-entry returns, label frequencies, class balance, or any later feature
values. The card loaders expose `require_labeling_authority()` and reject real labeling while the
cards remain `draft` / `pending_human_approval`.

Synthetic fixtures may exercise the proposed ordering because they contain no production outcomes.

## Proposed outcome card

The proposal freezes:

- entry anchor: the accepted actual executable T+1 open from `P4-EVENT-002`;
- observation horizon: exactly 20 market sessions, with the entry session as session one;
- positive first-passage threshold: +15% economic value from entry;
- adverse first-passage threshold: -7% economic value from entry;
- price path: hypothetical one-share economic value using daily high/low, applying accepted cash
  dividends and bonus-share actions before the session threshold checks;
- no transaction costs in the label because this is a price-path outcome, not a simulated exit;
- terminal precedence: `not_executable` -> `failure` -> `positive` -> `neutral`;
- same-session positive/adverse overlap: `failure` wins because daily OHLC cannot prove intraday
  ordering;
- same-session structural-break/positive overlap: `failure` wins conservatively;
- no terminal condition by session 20: `neutral`;
- insufficient authorized partition tail: `censored`, excluded from the three-class corpus;
- semantically inadmissible or missing required future evidence: `unavailable`, never coerced to
  neutral or failure;
- suspension: consumes the market-session slot but does not create a price threshold without a
  valid bar;
- `not_executable`: inherited from the accepted #74 T+1 partition and retained as the fourth
  research subset, excluded from three-class training/backtest evaluation.

The 20-session horizon fits inside the already-approved 22-session maximum overlap, purge, and
embargo contract, so this proposal does not require a split-policy threshold change.

## Proposed structural-break card

The structural card is independent of any expert or action mapper. It evaluates each market-session
close after executable entry and triggers when either:

```text
(total_return_index < sma60 and sma20 <= sma60)
```

under the accepted corporate-action-normalized total-return semantics, or an authoritative listing
termination becomes effective.

The moving-average portion intentionally reuses the already-implemented P3 structural trend-break
formula as a direct market-data rule rather than importing P3 strategy decisions. The card freezes
20/60-session SMA windows and requires 60 sessions of usable history. Suspension alone does not
trigger a break. Missing required structural evidence resolves to `unavailable`, not failure or
neutral.

## Contract evidence

Repository-owned strict contracts are in:

- `src/quanthus/event_library/label_cards.py`;
- `config/p4-event-outcome-label.research.v1.json`;
- `config/p4-event-structural-break.research.v1.json`;
- `tests/unit/test_event_label_cards.py`.

The synthetic contract tests cover:

- exact digest loading and structural-card binding;
- draft cards refusing real labeling authority;
- positive first passage;
- adverse-first and structural-first failure;
- same-session adverse/positive and structural/positive failure precedence;
- neutral at the fixed horizon;
- inherited `not_executable`;
- partition-tail censoring; and
- missing-evidence unavailable settlement.

## Human approval gate

No real future-outcome corpus may be read until a human explicitly approves both exact semantic
digests shown at the top of this record. Approval may change lifecycle/governance metadata but must
not change either semantic digest. Any change to thresholds, horizon, price basis, corporate-action
treatment, overlap precedence, structural formula, censoring, missing-data behavior, or bound
candidate/split policy requires a new semantic digest and renewed approval.
