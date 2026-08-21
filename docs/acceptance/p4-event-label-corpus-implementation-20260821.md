# P4-LABEL-001 Label Corpus Implementation Checkpoint

| Field | Value |
| --- | --- |
| Status | implementation complete; real development A/B corpus acceptance pending |
| Work item | `P4-LABEL-001` |
| Approved outcome card | `p4-event.outcome-first-passage@1.0.0` |
| Outcome digest | `sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca` |
| Approved structural-break card | `p4-event.structural-break-trend@1.0.0` |
| Structural digest | `sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc` |
| Human approval | `https://github.com/rv198-star/Quanthus/issues/75#issuecomment-5366610770` |

## Implementation boundary

`src/quanthus/event_library/label_corpus.py` adds one deterministic label sidecar to an already
sealed P4 Event Library. It does not rewrite event ids, candidate membership, T+1 executable
partitioning, or the approved candidate rule.

One new labeled Event Worker run contains:

```text
run/
├─ event-library/       # same P4 candidate/event contract
├─ label-corpus/
│  ├─ labels.jsonl
│  ├─ summary.json
│  ├─ manifest.json
│  └─ READY
├─ stdout.log
├─ stderr.log
└─ run-manifest.json
```

The label manifest binds:

- exact Event Library manifest SHA and content digest;
- exact outcome-card id/version/digest;
- exact structural-break id/version/digest;
- P3 total-return feature-registry version/digest;
- feature-panel report digest;
- immutable replay-bundle snapshot id/content digest;
- development-only scope; and
- the complete label file inventory/content digest.

Every candidate event produces exactly one `labels.jsonl` row. `positive`, `neutral`, `failure`,
and inherited `not_executable` rows use `settlement_status=settled`. Partition-tail rows use
`censored`; semantically inadmissible future evidence uses `unavailable`. Censored and unavailable
rows are retained for audit but excluded from the three-class corpus.

## Outcome path semantics

The implementation follows the approved cards without adding result-derived parameters:

- entry anchor is the accepted actual executable T+1 open;
- the entry session is session 1 of 20 common SSE/SZSE market sessions;
- positive threshold is +15%; adverse threshold is -7%;
- failure wins same-session positive/adverse or positive/structural overlap;
- structural break uses the approved total-return-index/SMA20/SMA60 rule or authoritative listing
  termination;
- suspension consumes a session but does not fabricate a threshold or structural break;
- a new T+1-open holding does not receive an action effective before that open; accepted cash and
  bonus-share actions strictly after entry update the one-share economic-value path before that
  session's threshold checks;
- missing required non-suspension price/structural evidence fails closed to `unavailable`; and
- fewer than 20 authorized development sessions at the partition tail produces `censored` before
  any three-class settlement is accepted.

The structural features reuse `BaselineFeaturePanel.build` and
`config/p3-baseline-features.research.v1.json`; no parallel moving-average implementation is
introduced.

## Worker and recovery contract

The existing static `event-worker` remains the only P4 Worker and continues to admit only
`quanthus.p4-event-scan.v1`. The governed input now additionally binds:

- `config/p4-event-outcome-label.research.v1.json`;
- `config/p4-event-structural-break.research.v1.json`; and
- `config/p3-baseline-features.research.v1.json`.

The Worker invokes `run-p4-labeled-event-scan` as one lease-governed child process. Run manifest
schema advances to `quanthus.p4-event-run.v2` and binds both Event Library and label-corpus digests.
Recovery accepts an already sealed run only if both independent verifiers pass and label/event
counts reconcile, including inherited `not_executable` count equality.

## Local evidence

Focused checks cover:

- first-passage positive/failure/overlap ordering;
- structural failure and listing termination;
- suspension semantics;
- not-executable inheritance;
- censor and unavailable behavior;
- cash-dividend and bonus-share economic path adjustment;
- code/calendar/action/feature source joins;
- exact Worker input and sealed-run recovery; and
- control-plane-only Worker boundary.

Observed local gates before production handoff:

- label/Worker focused suite: 41 passed;
- repository `make check`: 1,250 passed, 4 optional PostgreSQL-DSN tests skipped.

No real label counts or outcome distributions were inspected while choosing the card semantics.

## Remaining acceptance

`P4-LABEL-001` remains open until two independent production Jobs on the exact same governed input
and code revision reproduce:

1. the same 36,820 event universe and accepted #74 Event Library identities;
2. identical label settlement/outcome counts;
3. identical label-corpus content digest and manifest SHA;
4. full event-id coverage with no duplicates; and
5. independently verifiable immutable Run v2 manifests.

Only after that A/B gate passes may #75 close and `P4-REPORT-001` begin using the labeled corpus.
