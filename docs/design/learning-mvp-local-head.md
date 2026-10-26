# Bounded local head2 fit and update

| Field | Value |
| --- | --- |
| Design status | draft — non-governing implementation note |
| Delivery gate | open — experiment and consumer admission remain separate |
| Work items | P5-STATE-003 / #543; P5-LOCAL-001 / #544 |
| Parent context | [Historical learning](stock-stage-historical-learning.md) |
| Conformance | `tests/unit/test_stage_local_head.py` |

`quanthus.stage_learning.local_head` implements one small binary logistic head for an
explicitly qualified retrospective request. It does not open source data, qualify a
reference, change a frozen P5 request, or authorize research or production. Existing
P5 neural seeds, windows, activation and assessment contracts remain unchanged.

The caller freezes the exact request, source, reference, eligibility, split, feature,
target and code digests and explicit optimizer policy. Its stage graph is:

1. Admit exact definite training rows and one finite policy. Fit one train-only scaler.
2. Run deterministic full-batch gradients. Persist a canonical JSON checkpoint if
   interrupted; resume requires the same rows and bindings and retains the scaler.
3. Diagnose one later, identity/episode-disjoint development batch against complete v0.
   Persist its independent canonical diagnostic/consumption ledger even if no update
   fires. Both v0 and v1 evaluation require that pinned ledger; immutable v0 alone
   cannot establish that a later sample is still fresh.
4. Only if the frozen loss trigger fires, execute one incremental v1 update with the
   same scaler. Its ledger records development consumption by v0 and training by v1.
5. After both versions are frozen, evaluate qualified later reference rows. Evaluation
   cannot reuse any consumed sample, security or episode. A missing class has no AUC.

All computation is in memory; persisted checkpoints and per-run receipts are job-local
artifacts, not repository data, caches or ADM. At most128 rows,24 features and1000
steps per version are admitted. A dispatcher must impose the frozen wall/RSS/write
budget and perform the source/reference admission separately. No automatic search,
calibration, replacement of unavailable samples or second local update is provided.

The checkpoint binds its semantic inputs, progress cursor, original train scaler,
parent version and versioned row consumption. Readers require pinned byte hashes
and exact bindings, reject malformed or noncanonical JSON, and never unpickle models.
The diagnostic reader likewise requires a pinned byte hash, validates every consumed
identity and date, and evaluation binds it to the exact v0 parent. For v1 its development
uses must also equal the checkpoint's ledger. The admitted run ledger owns the selected
checkpoint and diagnostic hashes; a parent hash is an association, not admission.
An expected hash must come from an admitted ledger; a hash alone does not qualify an
arbitrarily created model. Independent reproduction rebuilds the fit from exact inputs;
a successful checkpoint reload is only a recovery test.

Predictions expose a sigmoid **raw score** and a null calibrated probability. Existing
`resolve_output` therefore emits `ABSTAIN`; missing features emit `UNAVAILABLE`.
Neither authorizes entry, forced sale, expert/veto bypass, strategy benefit or P6.
Raw-score diagnostics, actual fit/update completion and consumer acceptance must be
reported separately. Unknown/ambiguous references never become negative labels.
