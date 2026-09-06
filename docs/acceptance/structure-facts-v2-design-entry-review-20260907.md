# Facts v2 final design-entry review

| Field | Value |
| --- | --- |
| Date | 2026-09-07 |
| Work item | `P3-STRUCT-004` |
| Reviewed implementation revision | `bc798030d32c3e1f81aa600fe9da32c888981f88` |
| Governing design | `../design/structure-facts-v2.md` |
| Blueprint | `../../config/structure-facts-v2-blueprint.v1.json` |
| Decision | PASS for bounded detailed design and implementation entry |
| Runtime Facts v2 acceptance | NOT RUN / NOT CLAIMED |
| Current delivered slice | B1-R local contracts; B2-B5 runtime not started |
| Review independence | Same assistant, role-separated review and fresh counterexamples; not external or independent-model sign-off |

## 1. Question and scope

The owner requested a final review of the existing plan, semantic model and technical route before
deepening concrete Facts v2 delivery. This is an entry review, not acceptance of a nonexistent v2
producer. Unimplemented source verification, data joins and consumer integration must not be
counted as passed simply because local types and tests are green. Equally, every explicitly planned
runtime task is not automatically a reason to restart the approved high-level architecture.

The reviewed baseline was clean, and HEAD and the remote main ref both resolved to the exact
revision above. The review read the governing design, B1-R checkpoint, semantic/time/audit code,
contract tests and coverage ledger; the LLD 10 audit boundary was checked against the Fact model.

The review retained the domain boundary, five Structure families, parallel indicator/disclosure/
fundamental/market domains, required reference-data ownership, separate outcome targets, and the
B2 -> B3 -> B4 -> B5 route. No new graph runtime, generic DWS, ADS, audit store, queue or strategy
engine is justified by this slice.

## 2. Role-separated conclusions

| Perspective | Finding and disposition |
| --- | --- |
| Quantitative research | Definitions, coordinates, declared windows and causal confirmation are explicit. A/B reproducibility is not predictive or economic proof. PASS for the research design boundary. |
| Short-horizon trading / tape observability | Daily data cannot prove intrabar ordering, first touch, reseal, queue priority or actor intent. These ceilings are explicit; the implementation must not imply a complete intraday trading vocabulary. PASS within daily scope. |
| Semantic modeling | Concept/spec/observation and anchor/interaction/episode separation is coherent. A qualified relation grain remains mandatory before multi-anchor construction; see E01. PASS for entry, not approval of a flattened security/date key. |
| Warehouse / temporal evidence | Business time, source/version availability, generation/publication provenance and operation logs have distinct owners. The retrospective provider limitation remains disclosed. B2 must prove the real source chain. PASS for the architecture, runtime evidence open. |
| Technical engineering | Additive contracts preserve legacy producers and use existing expert/Aggregator/P2 and audit paths. Local helper checks intentionally do not form a complete admission gate. E02 requires composition. PASS for the route. |
| Verification / governance | Naming/registration/admission, design/runtime acceptance and source/compute/use evidence are separated. Counterexamples and cold reconstruction remain required at the first runtime gate. PASS for staged verification. |
| Cost and scope | Dictionary #215, geometry/activity expansion, formal experts and ML are not prerequisites of the first runtime slice. Freeze input/scope/run budgets before result-bearing work. PASS for bounded investment. |

No finding requires a new owner choice about domain boundaries, storage topology, timestamp ownership
or the broad delivery sequence. There are three explicit implementation handoff requirements below;
none is reported as already implemented or waived.

## 3. Fresh verification on the reviewed revision

The focused command was:

```text
PYTHONPATH=src <python> -m pytest -q tests/unit/test_temporal_contracts.py tests/unit/test_structure_semantics.py tests/contract/test_document_traceability.py
```

Observed: **96 passed** (0.43 seconds). A separate diagnostic script at
`artifacts/p3-struct-004-final-review/review_probe.py` executed **18 synthetic diagnostics**. All
18 diagnostic assertions matched their expected observations: **14 existing guard checks and
4 scope/required-integration checks**. This is NOT 18 runtime acceptance passes.

| Probe | Observation |
| --- | --- |
| F01 | Prefix discovery is segment-bound and catalog entries remain reserved. |
| F02 | Source version selection is independent of physical revision ordering. |
| F03 | Equal recorded revision-boundary time does not manufacture causal order. |
| F04 | Overlapping eligible revisions cannot silently select a winner. |
| F05 | Unknown availability fails closed, with no unproven old-version fallback. |
| F06 | Date-only time evidence preserves conservative bounds. |
| F07 | Latest required dependency controls readiness; missing inputs are rejected. |
| F08 | Later confirmation cannot enter an earlier decision. |
| F09 | Two consumers reference the same immutable Fact; only their audit events differ. |
| F10 | A present rebuild cannot bind to purported past actual use. |
| F11 | A source correction changes revision identity, not necessarily the business slot. |
| F12-F13 | A generic security-only subject collapses two target slots; a qualified subject can distinguish them. This exposes E01, not a complete producer implementation. |
| F14 | A known future-effective reference passes confirmation but is not yet effective. This exposes E02. |
| F15 | Local receipt binding accepts partial finality: a final-daily consumer still needs a finality gate. This exposes E02. |
| F16 | An observation window cannot end after its result business time. |
| F17 | Equivalent UTC instants preserve semantic identities. |
| F18 | Delivery/use equality does not imply proven delivery-before-use. |

The diagnostic script does not read real market data, change policies, write runtime artifacts or
exercise production. Its findings are version-bound. Future integration tests should assert correct
producer/admission behavior, rather than freeze these current limitations as desired runtime behavior.

A targeted diff from the retained v1 closeout (`9d726c1`) through the reviewed revision showed no
changes in `src/quanthus/backtest`, v1 `structure_facts.py` / `structure_facts_run.py`, the feature
registry, platform code, Compose, or the existing baseline indicator registry. No v1 formula or
runtime authority was silently changed by the reviewed contract work.

## 4. Required implementation handoffs

These requirements are owned by the governing design's "Detailed implementation handoff
constraints" section and reflected in the blueprint. This evidence record explains their origin;
it does not create a competing implementation specification.

### E01: complete semantic result grain (before B3)

Two distances computed by the same specification for the same security and date, but against two
different anchors, are two legitimate result slots. If the producer changes only source closure
and value while retaining an unqualified security-only subject, the generic ObservationRevision
assigns them the same observation_key. They then look like revisions of one slot.

Freeze a semantic relation subject or an explicitly qualified key. Keep stable target slot identity
separate from exact source/anchor revisions and physical package identity. Resolve listing/security
membership explicitly. Test two simultaneous anchors, two different anchors with equal numeric
levels, corrected source versions, and prefix/package/rebuild invariance. The exact producer schema
is B3 detail, not already supplied by the small B1-R dataclass.

### E02: complete admission composition (before B2 runtime acceptance)

No single helper proves admissibility. In particular, confirmed_by() is not an effective-date check,
and validate_consumption_binding() is not a finality or source-authentication check. Compose the
applicable spec/partition/view, exact source selection and closure, business validity, finality,
quality, coordinate, confirmation/readiness and operation-evidence checks under one bounded profile.

A final-daily consumer must reject partial/unknown finality. Known future-effective information may
be read as such but cannot be applied as an already-effective rule or coordinate transformation.
Purpose matters; do not apply an unconditional expiry rule to historical scalar measurements.
Actual-use audit must keep original use evidence distinct from today's counterfactual recomputation.

### E03: explicit evidence limitations and bounded delivery (B2-B5)

Record a supported evidence view and missing-proof reasons for each required source. Do not silently
upgrade retrospective data to public/platform PIT, or downgrade a requested strict view on failure.
A separately admitted retrospective lane may continue with its limited claim. Source-view failures
must not become bearish labels, forced sales or hidden future-informed historical exclusions.

Keep attempted/eligible/unavailable/rejected denominators visible. Freeze the first question, minimal
inputs, data revisions, observation/decision/execution profile, warm-up and scope, small anchor set,
interaction/gap/equality rules, consumer compatibility mapping, resource/run budget and independent
cold reconstruction contract before result-bearing qualification. Do not tune or broaden scope based
on observed outcomes. Dictionary #215 remains off the critical path.

## 5. Gate decision and next concrete artifacts

```text
High-level plan, domain model and technical route: PASS FOR DETAILED IMPLEMENTATION ENTRY
B1-R local-contract scope: retained; no broader runtime claim
B2 integrated input/consumer readiness: NOT IMPLEMENTED / NOT VERIFIED
B3 qualified multi-anchor producer: NOT IMPLEMENTED / E01 required
B4-B5 interaction/episode and real-DWD qualification: NOT RUN
Facts v2 runtime acceptance: NO
P3-STRUCT-004: OPEN
```

The next work should produce a bounded B2 input-readiness report and concrete interface/schema
specification, followed by the minimal anchor/interaction/episode chain. The first runtime gate
requires real-data evidence and independent reconstruction through the existing consumer/P2 path.
Economic improvement, a formal investment expert, thirteen-state expansion, ML and forward execution
remain separately authorized and unclaimed.

Another broad ontology review is not a prerequisite. Reopen an owner discussion only if evidence
requires changing approved domain ownership, information-view claims, resources beyond the frozen
budget, price-coordinate meaning, or execution/strategy authority. Ordinary implementation details
and counterexamples belong in the current work item's design/tests, not a new approval loop.

## 6. Publication verification

This review changes documentation/coverage routing only; the temporal and consumption implementations,
legacy Facts, indicators, DWD, Worker and P2 are unchanged. No deployment or research Job is performed.

Observed verification on the reviewed code plus the entry-review documentation/coverage changes:

```text
Focused semantic/temporal/document tests: 96 passed (0.43 seconds)
Fresh diagnostic probes: 18 expected assertions matched (14 guard, 4 scope/integration)
make check: 1490 passed, 7 skipped (185.40 seconds for pytest)
Ruff on semantic/time/audit modules and their unit tests: PASS
git diff --check: PASS
```

The seven skips remain the two unmounted production-artifact tests and five unconfigured PostgreSQL
integration tests. They are not passes and do not prove runtime integration. The full gate includes
doctor, compilation, repository tests and the offline planning check. It ran before this results-only
update; focused documentation and diff checks are repeated before publication.

Publication contains only this evidence file, the governing design handoff constraints, blueprint
next-gate/evidence links, the documentation map and the work-item index. B2-B5 implementation status
remains not_started; v2_runtime_accepted remains false.
