# P3-STAGE-012-B Relocation Conflict Adjudication — 2026-09-13

## Disposition

```text
P3-STAGE-012-B: PASS_CONSUMED_SEMANTIC_ADJUDICATION
CONFLICT PACKET: 11 / 11 ACCOUNTED
P008 GUARD RETUNE: NOT SUPPORTED
P011 LEDGER: UNCHANGED
```

B examines only the eleven consumed P011 cases where the sealed projected reference was `STAGE_0`,
the representative frontier had positive instability, and the frozen P008 relocation guard was also
positive. No new market row or preserved partition was read.

## Classification

The diagnostic categories were frozen before adjudication:

- **R1 guard over-fire** — numeric relocation is positive but full T-only path evidence does not
  support coherent repricing;
- **R2 reference under-call** — coherent relocation is supported and the blind atomic reviewer likely
  failed to assert it; independent instability is not needed to explain the path;
- **R3 genuine coexistence** — relocation and structural disorder are both defensible;
- **R4 unresolved** — consumed evidence cannot distinguish the above.

Result:

```text
R1 guard over-fire       0
R2 reference under-call  2
R3 genuine coexistence   9
R4 unresolved            0
```

The dominant finding is therefore **not** a reproducible P008 guard failure. Nine cases are better
represented by the already-supported `instability=TRUE + relocation=TRUE` mixed state, while two are
better described as coherent relocation that the original reference under-called.

## Implication

P011's eleven relocation-veto Stage0 false negatives should not be used as a threshold-retuning packet.
They demonstrate that projected Stage0 references can compress atomic information. The next reference
protocol must ask reviewers for instability and relocation independently and report mixed-state rates
before Stage projection.

This finding also means the next successor phase should leave the P008 relocation implementation
frozen unless a future independently designed diagnostic produces an actual guard-over-fire shape.

Machine evidence:
`config/stage-learning/stage-relocation-conflict-adjudication.v1.json`.

## Claim ceiling

This is same-origin post-closeout semantic adjudication over already-consumed evidence. It guides
successor design but does not rewrite the sealed P011 ledger, replace historical labels, qualify the
P008 guard, or create fresh evaluation evidence.
