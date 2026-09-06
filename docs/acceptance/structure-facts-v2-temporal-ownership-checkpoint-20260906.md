# Facts v2 B1-R: temporal ownership and consumption-audit checkpoint

| Field | Value |
| --- | --- |
| Date | 2026-09-06 |
| Work item | `P3-STRUCT-004` |
| Governing design | `../design/structure-facts-v2.md`, section 5 |
| Base revision | `9ea4e8406d7390688d9446bd9985a6c48826db1b` |
| Scope | Additive contracts and local synthetic verification only |
| Owner correction | Actual usage time belongs to operation/decision audit, not market Fact modeling |
| Review source | Temporal peer review of 9ea4e84: R01-R12, 18 diagnostic probes |
| Current verification | Focused 96 passed; full repository 1490 passed / 7 skipped; Ruff and diff check PASS |
| B2 integrated source/consumer admission | NOT RUN / NOT CLAIMED |
| Production or market-data change | NONE |

## 1. Scope and correction

The owner approved implementation of the temporal review with the explicit correction that usage
belongs to operation logs. The earlier review grouped business, generation and consumption clocks
for analysis; they are not all columns or attributes of a market observation.

Three owners are now explicit:

1. Business observations: result-as-of, observation window, event/market confirmation and optional
   business validity; no consumer, consumed-at, decision or invocation cutoff fields.
2. Source and generation/publication metadata: evidenced public/platform version times, physical
   completion, artifact membership and availability to consumers.
3. Append-only operation/decision audit: actual use/decision/recording time, consumer/run/decision,
   query cutoff/view/purpose, and exact observation/revision/spec/receipt/artifact references.

No retroactive used-at, last-used-at, use count or used-by mutation is made to a Fact. An output may
be unused or consumed repeatedly. Creation/publication does not prove consumption. This is a typed
contract for the existing audit boundary, not a new logging service or persistence implementation.

## 2. Implementation surface

- `src/quanthus/temporal_contracts.py`: business-time profiles, explicit windows/counts/finality,
  conservative availability bounds, stable semantic observation keys/revisions, orthogonal query
  context, a local fail-closed source-version selector and dependency-readiness bound.
- `src/quanthus/evidence_consumption.py`: separate generation/publication receipts,
  `ConsumedRevision`, `FactConsumptionEvent` and local log-to-output binding validation.
- `src/quanthus/structure_semantics.py`: clarify the retained B1 helper's limited semantics; no
  behavior change. Its confirmed_at is logical market confirmation, not full input readiness.
- `tests/unit/test_temporal_contracts.py`: boundary, identity, time and audit counterexamples.
- `config/structure-facts-v2-blueprint.v1.json`: expose B1-R separately and keep B2-B5 unimplemented.

HLD, ADR 0014, LLD 03 and LLD 10 share the ownership decision. LLD 03 remains the sole work-item
owner. Existing P2 DecisionEvent/persistence, v1 Fact producers and indicators, DWD, Workers and
execution rules are unchanged. Catalog prefixes/formula definitions are not modified.

## 3. Review disposition by issue

| Review | B1-R disposition | Remaining integrated proof |
| --- | --- | --- |
| R01 business time/window/validity | Separate typed fields/profile; future-effective reference permitted | Bind actual calendars, scope and spec |
| R02 confirmation vs readiness | Logical market confirmation separate from required-input readiness | Authenticate confirming evidence and its selected view |
| R03 recomputation vs actual use | Query purpose separate from knowledge view; exact use lives in audit logs | Original receipt validation and existing decision-store integration |
| R04 selection/closure | Local selector rejects overlap/unknown; retrospective needs explicit pin; readiness is latest required bound | Prove complete revision families/dependencies/cross-sections; implement real as-of joins |
| R05 precision/order | Evidence bounds retained; cutoff inside bounds or equal availability/use is rejected | Reliable sequence/causal-order receipt support where needed |
| R06 finality/counts | Partial/final/unknown and distinct bar/session counts; spec owns sampling | Data-specific finality/late-arrival validation |
| R07 episode end/censoring | Later process observation cannot enter an earlier cutoff | Runtime append-only episode projection and gaps |
| R08 profiles | Measurement/reference/process contracts separated; no universal forecast chronology | Exact executable spec and source grain validation |
| R09 identity | Semantic key/revision exclude generation/publication/use/package inventory | Producer hashing, materialization membership and v1/v2 bridge |
| R10 observability | Daily-bar/intrabar and intent ceilings explicit in governing design | Any finer-data proposal is separately scoped |
| R11 naming/spec | Prefixes unchanged; spec remains algorithm/unit/calendar/clock owner | Dictionary #215 remains independent |
| R12 stopping boundary | B1-R only; bounded B2-B5 next | No all-market rebuild, fresh economic run or uncontrolled expansion |

R01-R04 are closed as modeling/local-contract ambiguities only. They do NOT mean B2 source or
consumer admission has passed. Nonempty references and `declared_complete=True` are declarations,
not evidence authentication. The local selector is not a database view or a complete PIT engine.

## 4. Specific checks and claim ceiling

The focused suite includes:

- Fact business contracts reject usage fields; two independent consumer events reference the same
  immutable observation while only their log identity changes;
- a 2026 generation can support a 2026 counterfactual operation with a 2020 evaluation cutoff,
  but cannot bind to a purported 2020 actual consumption;
- an old actual-use event can be recorded later without changing its original use time;
- substitution of observation/revision/spec/generation/publication/package references fails;
- direct handoff is explicit delivery evidence, not a last-used field;
- delayed pivot confirmation, future-effective rules, partial/final revisions, distinct bar/session
  counts and append-only episode status preserve their separate meanings;
- date-only clocks are not imputed to midnight, and equal timestamps do not invent ordering;
- overlapping public versions, unknown-version fallback and mixed subject keys fail closed;
- required-input readiness does not ignore missing inputs and is distinct from market confirmation;
- later materialization or a larger batch does not affect the semantic observation revision;
- exact source/spec changes remain identifiable, and UTC-equivalent timestamps have equal hashes.

The original temporal review used role-separated analysis by the same assistant, not external
expert signatures. This checkpoint is author-side implementation/testing, not independent market
or production acceptance.

## 5. Verification commands

Focused command:

```text
PYTHONPATH=src <python> -m pytest -q tests/unit/test_temporal_contracts.py tests/unit/test_structure_semantics.py tests/contract/test_document_traceability.py
```

Repository gate:

```text
make check PYTHON=<python>
```

Observed results in this working revision:

```text
New temporal/audit tests: 36 passed
Focused new + retained semantic + documentation contracts: 96 passed
make check: 1490 passed, 7 skipped (190.47 seconds for pytest)
Ruff on changed Python: PASS
git diff --check: PASS
```

The seven skips are the existing unmounted production expert artifacts and unconfigured
PostgreSQL integration context. They are not reported as passes. No actual source-version store,
market Fact build, real-DWD run, decision-log persistence integration or production deployment was
exercised. Full-repository evidence was collected before this results-only documentation update;
focused documentation and diff checks are repeated for publication.

## 6. Next boundary

B1-R is additive contract work. B2 must qualify the first slice's actual source evidence, time/view
selection, identity/coordinates, finality and minimal decision-log bindings before B3-B5 runtime
qualification. Dictionary issue #215 remains outside the critical path. No deployment, new DWD
read/build, economic comparison, model training, thirteen-state or forward execution is included.
