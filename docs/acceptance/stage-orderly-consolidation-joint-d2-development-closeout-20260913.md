# P3-STAGE-011-D2 Joint Development Closeout — 2026-09-13

## Disposition

```text
P3-STAGE-011: REVISE_OR_PARK_DEVELOPMENT_FAIL
D1 RECOVERY: SUCCEEDED
D2 BLIND REVIEW: SEALED BEFORE CANDIDATE READ
FIXED PAIRS SCORED: 27 / 27
ELIGIBLE PAIRS: 0
PAIR FROZEN: false
PRESERVED 2021: UNREAD
```

The exact sealed 190-case denominator resolved to Stage0=83, Stage2=21, Neither=74 and Ambiguous=12.
The review ledger SHA-256 is
`sha256:1a73c66dd1255970c3a8de81a92adfabe7e57fc668b8deab7a12ef3eb82092f1`.
Only after that ledger was immutably snapshotted and committed did the run read the frozen threshold
basis and exact 9 × 3 = 27 candidate-pair grid.

The raw deterministic selection result is immutably snapshotted as NexusDock artifact
`03f8853a1f4f6505b67c26f9ef96b769`, SHA-256
`b643a37841ad3241e9be6670b89d29d254e510129a9d2620a8baebd3f3cc8fad`. The 27-trial digest is
`sha256:a45af35b450ec55fdf3c667bca85420d94d1e3e014678c12a463fd800e1ee5bf`.

## What P011 fixed

The fresh 2018 evidence strongly supports the Stage2 topology repair. On the diagnostic frontier the
`order-balanced` Stage2 profile produces:

```text
TP = 20 / 21
FP = 1
FN = 1
precision = 0.9523809524
coverage = 0.9523809524
FPR = 0.0063694268
pair overlap = 0
```

The old center-shift hard truth gate is therefore not the binding reason P011 fails. All 27 frozen pairs
have zero Stage0/Stage2 proposition overlap.

## Why P011 still fails

Every one of the 27 pairs fails the frozen absolute Stage0 positive-reference coverage gate. The best
frontier reaches:

```text
Stage0 TP = 58 / 83
Stage0 FP = 4
Stage0 FN = 25
precision = 0.9354838710
coverage = 0.6987951807
FPR = 0.0421052632
required coverage = 0.70
```

Because 58/83 is below 0.70, the integer minimum is 59 true positives. The frontier is therefore one TP
short, but the preregistered stop rule does not permit rounding, threshold relaxation, reference rewrite
or a 28th pair.

The failure is also not merely one unlucky point. Read-only attribution of a representative one-gate
frontier pair finds 25 definite Stage0 false negatives:

- 14 have the positive-instability projection itself FALSE;
- 11 have instability TRUE but are vetoed by the frozen P008 relocation guard;
- one of the 14 is additionally called Stage2 by the repaired order projection.

For all 14 cases where instability remains FALSE, both P010 positive channels are structurally closed:
`width_expansion_material=false`, `absolute_range_width_gain_material=false`, and
`broad_range_width_material=false`. This shows a shared amplitude admission boundary. Narrow or
contracting paths that the sealed reviewers still regard as strongly reversal-disordered can fall outside
both expansion and broad-reversal channels by construction.

Separately, 11 definite Stage0 references satisfy the frozen relocation guard. That is a nontrivial
semantic alignment conflict between the Primary reference and the inherited P008 numeric relocation
boundary. It does not prove either side is correct and cannot be resolved by tuning after seeing this
result.

## Stop rule

No pair is frozen. Therefore:

- D3 is not opened;
- preserved 2021 market data remains unread;
- no threshold is relaxed;
- no reference is rewritten;
- no grid expansion or 28th pair is allowed;
- no supplement or automatic retry is allowed;
- the P008 relocation guard is not retuned inside P011;
- 2019 P010 and 2020 P009 development evidence is not reopened for learning;
- 2022-2025, official labels, daily activation, strategy and trading remain closed.

Machine result:
`config/stage-learning/orderly-consolidation-joint-development-selection-result.v1.json`, digest
`sha256:9e3c61db83e5b9f6914dbab763c8597686f67d1527355b097020e91b690d0348`.

Post-result diagnostic:
`config/stage-learning/orderly-consolidation-joint-development-failure-diagnostic.v1.json`, digest
`sha256:3518ba46bcd32cae6dc34b524e58dc43bb40a5cb54e329e1626371de586bda5a`.

Any successor must be a new work item. It should decide before fresh data whether the missing abstraction
is a positive narrow-reversal / contracting-disorder Stage0 channel, a shared relocation semantic
contract, or two separately governed problems. P011 itself is closed.
