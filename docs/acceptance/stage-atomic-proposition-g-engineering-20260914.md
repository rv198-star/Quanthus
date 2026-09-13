# P3-STAGE-012-G Engineering Acceptance — 2026-09-14

## Disposition

```text
P3-STAGE-012-G ENGINEERING: ACCEPTED
G1 RESULT-BEARING 2016 MARKET JOB: READY AFTER THIS REVISION IS DEPLOYED
MARKET DATA READ IN THIS CHECKPOINT: 0
PRESERVED 2021 READ: 0
JOINT PAIR SEARCH: 0
AUTOMATIC RETRY: 0
```

## Frozen contracts

- preregistration: `config/stage-learning/stage-atomic-proposition-development-screen.v1.json`
  (`sha256:24aacc70ef7ffa3bbfd0e28e8aa33fd91ba51bbd65da6c13640926215987c54d`);
- execution: `config/stage-learning/stage-atomic-proposition-development-execution.v1.json`
  (`sha256:5203b5c4a325b775722c90c7404fc149567e448cf63d39e731e22fcd6cebe098`);
- 2016 evidence scope: `config/stage-learning/order-reference-evidence-scope-2016.v1.json`
  (`sha256:6bbe9c56b850d6bb6ebe49766f2e5c9c2860def98fb06b54c5320ea672b6e32b`).

## Atomic-first scorer

`src/quanthus/stage_learning/atomic_proposition_development.py` implements the preregistered order:

1. score the exact nine P010 instability profiles, each with the same fixed failed-envelope-resolution
   additive channel;
2. evaluate the exact frozen P008 relocation guard as one non-selectable proposition;
3. score the exact three P011 order profiles using the order core without relocation veto;
4. freeze at most one instability candidate and one order profile independently;
5. only then test the frozen I/R/O truths through the existing Stage0/Stage2 resolver.

The scorer has no market reader, no joint pair ranking and no post-result candidate expansion. Any
atomic or projection failure keeps preserved-2021 authority false.

## G1 prepare

`src/quanthus/stage_learning/atomic_proposition_development_prepare.py` reuses the proved P011
population/read/selection/evidence primitives. It additionally verifies the P011 governed artifact and
adds the complete P011 512-identity fresh pool to the exclusion closure before constructing the new
2016 pool.

The prepare publishes:

- source binding and 2016 selection;
- exact still-unread preserved-2021 plan binding;
- unlabeled threshold basis;
- finite 9 / 1 / 3 atomic candidate surface with zero joint pairs;
- blind `evidence.json` plus existing directional path facts;
- a separate `envelope-measurements.json` used only after the review ledger is sealed.

The blind reviewer transport contains the raw 21-close T-only path and existing path facts, but does
not contain the registered envelope measurement, thresholds or candidate outputs. This prevents the
new measurement from becoming its own reviewer label.

## Worker and restricted control

The Stage Worker admits exactly one new result-bearing kind:

```text
quanthus.stage-atomic-proposition-g1-prepare.v1
```

Its access budget is one-shot and idempotent for the exact revision. A different revision has no retry
or replacement authority. Review pages are virtual/read-only; the selection transport exposes the
basis/grid/envelope payload only with an explicit `review_ledger_must_be_sealed_before_use` marker.

The restricted RX6800 AgentDock helper adds only `submit-p012-g1`; it does not add generic submit,
shell, cancel or file authority.

## Verification

Focused engineering tests cover:

- minimum alternating failed-envelope-resolution semantics;
- atomic order core without relocation veto;
- independent I/R/O selection and exact resolver compatibility;
- G1 artifact inventory and evidence-scope binding;
- one-shot Worker budget and runtime receipt;
- envelope-hidden review transport versus envelope-present selection transport;
- restricted helper command/kind binding.

No 2016 market source was read by this engineering checkpoint.

## Next gate

After this revision is durable and deployed to RX6800, exactly one G1 prepare Job may read the frozen
2015 warm-up / 2016 development scope. G2 must seal the blind per-role atomic ledger before the
selection transport is used for G3 scoring. There is no automatic retry. Preserved 2021 remains closed
until H freezes the exact atomic/projection package.
