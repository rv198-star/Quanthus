# Bounded local head2 fit and update

| Field | Value |
| --- | --- |
| Design status | draft — non-governing implementation note |
| Delivery gate | open — experiment and consumer admission remain separate |
| Work items | P5-STATE-003 / #543; P5-LOCAL-001 / #544 |
| Parent context | [Historical learning](stock-stage-historical-learning.md) |
| Conformance | `tests/unit/test_stage_local_head.py`; `tests/unit/test_stage_head_comparison.py` |

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

`head_comparison` supplies bounded descriptive comparison on one caller-admitted
reference set. Calibration and evaluation must be chronological and disjoint from
consumed training/development identities; UNKNOWN consumption is retained. Repeated
issuer/episode anchors are reported as clusters. Missing classes produce null AUC/AP.
The `head_comparison.stage2_predicate` adapter uses the exact frozen R0 predicate;
its non-match remains NO_ASSERTION rather than a semantic negative reference.
The legacy rule module and historical code/config manifests remain byte-immutable;
equivalence tests bind the adapter to its original packet evaluation path.

An explicitly requested small-sample Platt pilot may fit only its calibration role
after the frozen class and distinct-issuer gates pass. Parameters, source/reference,
model and code identities belong in the caller's immutable run ledger. Its output is
exploratory and is never substituted for formal calibrated probability or P5
qualification. An unqualified pilot does not fit at all. The production model output
continues to abstain, subject to the existing shared P3 expert/veto path. No economic
replay or P6 admission follows from successful comparison code or a pilot estimate.

## Separate bounded Head2 correction (2026-10-26)

Owner activated one bounded correction after the first candidate's audit. The
versioned engineering policy is
`config/research/learning-mvp/head2-revision.v2.json`; its source/reference delivery
gate remains blocked. This extension does not amend the frozen first experiment
or grant a new training/consumer request. Its parent research boundaries remain
those of [historical learning](stock-stage-historical-learning.md).

`head2_revision` encodes absolute directional efficiency and center displacement,
the original width definitions, and the two existing directional-path facts.
Original Decimal values/revisions are retained in feature identity. A seventh
column distinguishes mathematically undefined counter-direction at zero net
displacement from actual source unavailability. The zero-direction mask requires
zero directional efficiency and zero range traversal; other missing inputs remain
unavailable. All facts have the same T anchor within 2012–2021. These are derived
retrospective features, not reconstructed historical knowledge cutoffs.

Initial fit reuses the unchanged `local_head` primitives with seven named inputs,
200 deterministic steps, learning rate0.05 and L2=0.01. The separate V2 update
uses one fixed100-step objective: half of the original-training mean loss and half
of the new-batch mean loss, plus L2. Group means avoid weighting by accidental
batch size. The original scaler never refits. Trigger0.6931471805599453 is carried
from the frozen prior request, explicitly versioned with its provenance digest.

The update checkpoint has its own canonical JSON schema; it preserves the complete
original checkpoint and hashes the exact training/update/protection rows. Resume
checks all three sets and deterministic progress. A distinct, definite two-class
development protection set never enters gradients. Positive and negative error
counts must not increase and new-batch loss must not increase. A rejected update
is retained and cannot trigger tuning or evaluation-based version selection.
Protection is a development guard, not independent generalization evidence.

The caller must separately freeze source qualification, prior-exposure exclusions,
reference meaning, all roles/windows and code/config identities before actual
training. Train/update/protection/evaluation have disjoint sample, issuer and
episode identities with chronological constraints. The policy's pilot support
counts do not establish production statistical sufficiency. The V2 primitives
remain usable on synthetic rows without granting a real experiment.

`head2_consumer.pending_assessment` preserves model/purpose identity and returns an
abstained expert assessment for every valid raw score. It supplies no activation
flag, probability calibration or new action mapping. Synthetic tests call the
actual shared `compare_entry_assessments` path; system veto and action-policy
identities remain shared. Factual missingness still requires the ordinary system
BLOCK_NEW veto; model abstention must not force a sale. Actual consumer/P3 economic
comparison and P6 remain separately unqualified.

Conformance: `tests/unit/test_stage_head2_revision.py` and
`tests/unit/test_stage_head2_consumer.py`, in addition to the unchanged legacy
regressions. Immutable per-run evidence records actual source/fit/consumer status;
successful engineering tests do not accept the state-head experiment.

## Fixed-census successor (2026-10-27)

Owner activated the audited staged successor through
`config/research/learning-mvp/head2-census.v1.json`. Design status is
approved-for-bounded-execution; the delivery gate remains blocked on the exact
runtime source/reference/support evidence. This successor does not amend the
closed64 experiment or the frozen V2 role caps. Work items remain P5-STATE-003,
P5-LOCAL-001 and P3-ASSESSMENT-REUSE.

`head2_census` binds a complete frozen148-slot cohort:20 explicitly reused
development cases,32 protection,16 update and80 evaluation slots. Every
source-qualified definite two-role consensus enters its assigned role; UNKNOWN,
disagreement and unavailable slots retain their census without becoming negative
or being replaced. Company/security/listing/episode identity exclusion and exact
chronological blocks are validated before role admission. The numerical feature,
optimizer, threshold, update trigger and retention policy remain V2. The separate
protocol digest and full runtime request enter the checkpoint eligibility binding;
V2 keeps rejecting sizes above its original limits. No source reader or collector
is introduced by this module.

One initial fit on the20 admitted development cases precedes any new source or
reference access. At the fixed0.5 threshold it must identify at least one true
positive and true negative to justify spending on new evidence; otherwise the
request closes with DEVELOPMENT_NONDISCRIMINATION_STOP. This development screen
is neither independent model quality nor a proof that the state task is impossible.
The resulting v0 is shared downstream, rather than fitted again inside the Job.

Only then are protection/update sources and references processed. A no-trigger
update remains NOT_RUN; an actual rejected update is retained while v0 is chosen.
Protection labels may make this fixed development choice but never enter gradients.
The chosen candidate and predictions for every available evaluation slot must be
committed before evaluation opinions are opened. The commitment binds request,
code,cohort,candidate and prediction identities; the runtime dispatcher owns the
actual file/access order, source qualification, budgets and independent rebuild.
Hash equality by itself does not prove temporal isolation. All definite evaluation
cases are retained, with pooled and time-block paired results. Conformance is
`tests/unit/test_stage_head2_census.py`; real results remain separate per-run
evidence. No probability calibration, P3 economic, P6 or production gate is opened.
