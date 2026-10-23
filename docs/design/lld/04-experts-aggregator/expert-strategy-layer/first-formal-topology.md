# P3-STRAT-002: First Formal Two-Expert Topology

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-STRAT-002` |
| Design status | approved |
| Delivery gate | accepted; evidence: `docs/acceptance/p3-strat-first-formal-topology-production-20260822.md` |
| Implementation readiness | accepted on production and isolated byte-identical January 2020 Worker Runs |
| Historical authority | development partition only |

## 1. Outcome and boundary

Freeze and implement one executable formal strategy topology with exactly two active strategy
experts. The issue owns the second expert, the strategy card, the topology runner, and a formal
attribution adapter boundary. This explicit ownership prevents the topology from depending on an
unimplemented expert hidden behind a design-only card.

The frozen P3 governed candidate remains a capability fixture and is not promoted or copied into a
strategy lineage. Shared low-level features, labels, P2 execution, and generic expert protocols may
be reused through their public contracts. Strategy identity, configuration, mappings, decisions,
and Run digests are new.

## 2. Two-expert shape

| Expert role | Evaluation route | Required action surface |
| --- | --- | --- |
| Market trend-stability expert from `P3-EXPERT-003` | future-target assessment plus complete replay of its policy mapping | entry assessment and scoring |
| Position-risk review expert implemented here | policy-only counterfactual replay | entry risk score/constraint and held-position reduce/exit review |

The position-risk expert is a strategy heuristic, not a system guardian. Tradability, PIT, data
quality, accounting, funding, and hard portfolio limits stay outside both experts and are always
active. The position-risk expert may emit context-specific scoring, constraint, veto, or abstention
effects under one declared expert version and separately versioned policy mappings.

Combining entry risk and held review in one expert is a deliberate MVP resource trade-off. Its
scorecard and attribution expose both decision surfaces and disagreement cases. Evidence that the
two surfaces have different failure modes triggers `narrow_with_new_version` or a later split; it
does not justify silently changing this version.

## 3. Strategy-card contract

Before any development result is opened, a human-approved card binds:

- strategy id/version and hypothesis statement;
- exact product-catalogue candidate id/version that will receive this strategy's evidence;
- candidate generator and eligible-universe ids, versions, and digests;
- both expert assessment versions, complete canonical policy-mapping sets, active modes,
  applicability, and abstention;
- target/resolver/scorer bindings for the assessment expert;
- fixed veto precedence, constraint merge, scoring aggregation, and action mapping;
- position lifecycle, exit, capital-unit, funding, concentration, matching, cost, settlement, and
  execution-profile versions;
- exact effective DWD snapshot and development split digest;
- proof that the assessment expert's evaluated population matches the strategy applicability
  population or an explicit block pending a new evaluation version;
- expected decision-change directions, known risks, stop conditions, resource budget, and rollback;
- two mandatory legal single-expert interventions and one mandatory joint intervention.

The card records active and Shadow roles even when the first version has no additional Shadow-only
expert. Adding an expert or moving one between active and Shadow creates a new strategy version.
Its product-candidate binding is frozen before results and cannot later be redirected to whichever
catalogue entry looks most favorable.

## 4. Legal interventions

The topology predeclares exactly these attribution operations:

1. Replace the trend expert's policy effect with its frozen abstention fallback while preserving
   observation evidence and every other topology component.
2. Replace the position-risk expert's strategy effects with its frozen abstention/base-policy
   fallback while preserving all system guardians and hard position rules.
3. Apply both fallbacks together. If no expert remains able to authorize entry, the declared joint
   fallback is `block_new_with_base_position_management`, not an aggregator crash or implicit
   neutral score.

The two single interventions cover every action-changing strategy expert. The joint intervention
is required because score, constraint, and held-review effects share the capital path and cannot be
assumed additive. No remove-other, weight search, threshold sweep, Shapley expansion, or dynamic
intervention is legal in this version.

## 5. Inputs and owned paths

The issue owns:

- `config/expert-strategies/`
- `src/quanthus/expert_strategy/__init__.py`
- `src/quanthus/expert_strategy/contracts.py`
- `src/quanthus/expert_strategy/registry.py`
- `src/quanthus/expert_strategy/position_risk.py`
- `src/quanthus/expert_strategy/runner.py`
- `src/quanthus/expert_strategy/attribution_adapter.py`
- `tests/unit/test_expert_strategy_contracts.py`
- `tests/unit/test_position_risk_expert.py`
- `tests/acceptance/test_formal_expert_strategy.py`

The runner consumes the direct-DWD/PIT provider and P2 execution interfaces. It does not create a
maintained ADS, implement a second matcher, or write financial truth to PostgreSQL.
This Issue owns and seals the formal `attribution_adapter.py` version. `P3-ATTR-001` consumes that
adapter without editing it; a proven adapter defect returns here as a new strategy/adapter version
before attribution is resumed.

## 6. Execution and audit flow

For each candidate or held review, the runner materializes one exact expert context and records all
applicable and abstained outputs. Aggregation retains the accepted order: strategy/system veto,
constraint intersection, policy-mapped score, action, then hard position/funding/accounting
invariants. Same-day candidates retain score-descending and security-id-ascending ordering.

Every decision binds candidate, feature, target, assessment, policy mapping, topology, position,
execution, data, and split digests. Shadow outputs are stored but cannot link an Order. A baseline
Run uses one shared provider session and feature panel for both expert outputs; it does not rerun
DWD separately per expert.

The Worker input contains the complete canonical evaluation-spec set for both experts. A missing,
extra, or duplicated spec is topology drift and fails before DWD opens.

## 7. Implementation route

1. Produce an outcome-blind strategy decision packet and receive the human strategy-card decision.
2. Prove assessment-population compatibility, then implement exact card loading, version/digest
   closure, and forbidden fixture-lineage checks.
3. Implement the position-risk expert and both context-specific policy mappings.
4. Compose the two experts through existing aggregator and position/P2 ports.
5. Implement the three legal intervention adapters without executing the development comparisons.
6. Pass deterministic synthetic checks and submit a short development-sentinel Job through the
   accepted Worker, then seal the topology and adapter manifests for `P3-ATTR-001`.

## 8. Failure semantics

Missing bindings, fixture identity reuse, mixed expert versions, absent system guardians, an
unbounded or undeclared intervention, all-scoring abstention without the declared fallback,
constraint conflict, evidence outside one PIT context, candidate-universe drift, DWD/split drift,
assessment-population mismatch, or any order linked from Shadow fails the Run. A missing strategy
expert never degrades into a neutral opinion.

## 9. Verification and acceptance

Tests prove exact card parsing, topology closure, two-expert count, guardian separation, active and
Shadow authority, output/context binding, action precedence, intervention legality, joint fallback,
candidate ordering, exact product binding, complete evaluation-spec closure, full decision audit,
Worker-only DWD use, and unchanged v1 golden Runs. Identical semantic inputs must produce
byte-identical formal topology and adapter manifests plus identical sentinel decisions.

Acceptance artifacts are the approved strategy card, two expert/mapping identities, complete
topology manifest, expected-change and stop-condition record, legal intervention manifests,
resource budget, deterministic sentinel Run, and v1 compatibility report. Full multi-year economic
effects belong to `P3-ATTR-001` and end-to-end development acceptance belongs to `P3-EXPERT-005`.

### Unadmitted accounting correction, 2026-10-24

The read-only `P3-STRAT-003` risk diagnosis identified v1 adapter defects in the held-return
input and reduction-count scope. The additive `formal-p2-position-accounting-2.0.0` hooks
measure position-cycle wealth change relative to the wealth immediately before the filled buy,
using buy gross plus buy fees as denominator. P2 cash already includes partial-sale proceeds,
dividends and paid fees; those flows must not be added twice. A new filled position resets its
strategy-reduction count and transient holding state. An absent cycle baseline remains
unavailable. Entry thresholds and expert mappings are unchanged.

Code: `src/quanthus/expert_strategy/formal_p2_accounting_v2.py`; synthetic checks:
`tests/unit/test_p3_risk_diagnostic.py`. This successor is **not admitted for real execution**;
its constructor refuses provider access. Synthetic probes exercise the actual inherited held
review with the corrected hooks. Frozen v1 keeps its legacy hooks and sealed outputs. A new
bound adapter/execution admission and corrected comparison are required before economic
attribution resumes; old results do not establish that the risk policy itself failed.

### Purpose-specific corrected comparison, 2026-10-24

The Owner subsequently authorized wiring, four-variant rerun, independent reproduction and
old-versus-corrected interpretation. `p3_retrospective_accounting_v2.py` composes the reviewed
hooks with the existing retrospective executor. Its engine accepts only the successor code
version; the legacy engine continues to accept only v1. The generic correction constructor
remains unadmitted. `retrospective-execution.v2.json` registers separate exact primary and cold
receipts, binding the successor, accounting version, complete code/config closure, preparation,
runtime, sources and actual snapshot cutoff. Receipt lists are excluded from their own closure
hash to avoid a circular identity; all other admission-policy fields remain bound.

The original inputs, business windows, four variants, costs, expert thresholds and analysis
rules stay fixed. Each role uses its own independently constructed prerequisite; byte-identical
unchanged prerequisite producers may be reused only after fresh hashes and explicit semantic
rebinding proof. One session index serves all four variants per role. Cold execution uses a new
process with original sources and network blocked. No receipt grants historical PIT availability,
strict B/D acceptance, P6 mutation or production admission. Sealed v1 results remain unchanged.

### Fixed component diagnosis, 2026-10-24

Design status: approved by the Owner's step-2 continuation. Delivery gate: open;
real execution requires the separate exact admission registry.

`config/research/p3-strat-003/risk-components.v1.json` freezes five retrospective
cells: T (original Trend entry), TC (20% entry cap only), TE (entry filter plus cap),
TH (original entry plus held risk), and TR (full risk). All use Trend, unchanged
system guardians, 30% base constraints, base exits, costs and corrected P2 accounting.
TC contributes a separately identified unit-scoped strategy constraint; it never
changes the system/base limit. Held risk is the entire existing policy, including
the absolute 10% reduction target and extreme exit. TH's 30%-to-10% reduction is
intentionally not normalized to TR's 20%-to-10% reduction.

The runtime is `p3_risk_components.py`; contracts are in
`tests/unit/test_p3_risk_components.py`. The new experiment/request digest and full
code/config closure bind fresh primary/cold admissions in
`risk-components-execution.v1.json`. Each role reuses only its own independently
constructed, physically reverified prerequisite, with unchanged producer semantics.
One session index serves its five cells. T/TR run first: exact retained equity,
daily returns, final ledgers and every recorded decision business field must match
under a bijective physical-event identity mapping. Complete new entry governance
is also compared to the original runner on the same inputs; the held implementation
is unchanged. Failure stops before the three new cells. No claim is made to compare
old governance fields that were not persisted in old decision rows.

The fixed conditional effects are TC−T (cap), TE−TC (filter given cap), TH−T and
TR−TE (held risk under two entry regimes), their interaction, and TR−T. This is an
ordered conditional decomposition, not an order-independent allocation of value.
Filtering-versus-cap interaction in either held regime and reduction-versus-extreme
exit are not separately identified. No post-result cell addition or threshold search
is admitted. All six contrasts receive descriptive paired 22-session block intervals
for return and drawdown only; other metrics are full-window point diagnostics.
The exposed window and actual information cutoff remain unchanged. The original
factorial, strict B/D, production and P6 gates remain frozen; successor strategy
selection and fresh/forward validation belong to step 3.

## 10. Rollback

Remove the strategy version from the admitted registry and preserve all cards, Runs, decisions,
and adapter manifests. Candidate replay and the accepted P3 fixture remain available. A failed
expert or topology is retired or superseded by a new version; it is never repaired in place.
