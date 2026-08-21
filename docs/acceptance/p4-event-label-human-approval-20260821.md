# P4-LABEL-001 Human Approval Checkpoint

| Field | Value |
| --- | --- |
| Status | exact card digests approved; real development-outcome reads authorized |
| Work item | `P4-LABEL-001` |
| Decision reference | `https://github.com/rv198-star/Quanthus/issues/75#issuecomment-5366610770` |
| Approval authority | human project owner |

## Approved structural-break card

- identity: `p4-event.structural-break-trend@1.0.0`
- semantic digest: `sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc`
- lifecycle: `approved`
- structural break: `(total_return_index < sma60 and sma20 <= sma60) or authoritative_listing_termination`
- suspension alone is not a break;
- missing evidence remains unavailable;
- accepted corporate-action-normalized total-return semantics are required.

## Approved outcome card

- identity: `p4-event.outcome-first-passage@1.0.0`
- semantic digest: `sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca`
- lifecycle: `approved`
- anchor: actual executable T+1 open;
- observation window: 20 market sessions, with entry session as session 1;
- positive first passage: +15%;
- adverse first passage: -7%;
- structural break is an independent failure condition;
- same-session positive/adverse overlap: failure wins;
- same-session positive/structural overlap: failure wins;
- no terminal condition by session 20: neutral;
- insufficient partition tail: censored;
- missing required future evidence: unavailable;
- inherited T+1 not-executable events remain not-executable and outside the three-class corpus.

## Governance consequence

The approval binds only the exact semantic digests above. Lifecycle and governance metadata may now
move from `draft` / `pending_human_approval` to `approved` without changing semantic digests. Any
change to thresholds, window length, price path, overlap ordering, structural-break formula,
corporate-action semantics, missing/censor rules, or output semantics changes the digest and
requires new human approval before result-bearing reads.

With this checkpoint present, the repository may proceed to the P4 development label-corpus build.
The approval does not authorize arbitration or permanent-blind reads, strategy promotion, or any
change to the accepted P4 candidate rule.
