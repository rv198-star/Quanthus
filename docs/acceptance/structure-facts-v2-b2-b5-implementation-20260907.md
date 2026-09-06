# Facts v2 B2-B5 implementation checkpoint

| Field | Value |
| --- | --- |
| Date | 2026-09-07 |
| Work item | P3-STRUCT-004; #216 and children #217-#222 |
| Governing design | `../design/structure-facts-v2-delivery.md` |
| Prior published planning revision | `3f67604` |
| Implementation | RELEASE-READY; synthetic/repository gates passed, real qualification pending |
| Real-DWD A/B | NOT RUN |
| B2-B5 issue closure | OPEN pending respective real-data evidence |
| Claim | Bounded engineering and semantic compatibility only |

## Implemented surface

- `structure_v2_inputs.py`: final daily retrospective admission, exact source-member and
  reference evidence, complete security/session attempts, explicit rejections and weak-view receipt.
- `structure_v2.py`: prior-20/60 high/low references, complete target-qualified distance identities,
  same-anchor close crossings, loss/reclaim sequence, gap/action censoring and prefix-stable rows.
- `structure_v2_consumer.py`: one qualified target per existing expert invocation, continuous
  signed-distance mapping through existing Aggregator, real physical use receipts and existing P2.
- `structure_v2_review.py`: independent pure counts, scope-end reporting and preregistered blind
  sample selection. No future target or P2 outcome participates in selection.
- `structure_v2_run.py`: one verified Direct-DWD session per Job, admitted source extraction,
  shared B2-B5/P2 inputs, immutable publication, timeout, stage timing/denominators/bytes.
- `structure_v2_artifact.py`: stdlib-only bounded verifier, exact files/schemas/hashes, causal
  rebuild from published members, full target and audit membership, decision score/action and P2
  fill-event reconciliation. This does not rebuild expensive DWD prerequisites.
- Event Worker: a fourth explicit config mapped only to the v2 module; prior configs, API, compose,
  seeders, P2 and existing indicator formulas remain unchanged.

## Important pre-result corrections

Code inspection before any new real-data result found two identity pitfalls and corrected them:

1. Run request scope must not be the algorithm specification. `calculation_spec` excludes stock
   subset, report dates, physical budgets and execution probe settings. A test extends the report
   range and proves existing business observation identities remain unchanged.
2. Calendar/action evidence revisions must not become new semantic price coordinates. The logical
   calendar/profile and effective action epoch are separate from exact source-member evidence.
   A correction changes provenance/revision without inventing a distinct simultaneous target.

Business result time comes from the fixed final-daily profile, not a caller's knowledge cutoff.
Physical consumption time remains exclusively in the audit sidecar. Signed distances enforce a
local precision28 Decimal context. A/B compares business/consumer/P2 semantics, not wall clocks.

## Source and acceptance ceilings

The adapter consumes only verified, job-local projection members. It uses original source-record,
source-revision/artifact and listing identity fields, exact Decimal values, the independent DWD
calendar, and effective corporate actions. Source-public and historical platform arrival cannot be
proven by this retained provider; neither clock is imputed. The first lane remains explicitly
retrospective. General historical as-known/public reconstruction is NOT accepted by this checkpoint.

A historical bar is marked final through the retained final daily dataset/profile contract, not by
inventing a publication timestamp. Unknown/partial input fixtures are rejected. Optional missing
volume does not discard otherwise valid prices. No later quality failure changes an earlier stock
universe. Entire missing calendar sessions and missing per-security observations are both visible.

The compatibility expert receives one target-qualified measurement per invocation because the
existing protocol forbids duplicate feature/version keys in one context. It is not an investment
expert or economic promotion. All process/target facts remain separately published and verified.
The typed consumption audit persists inside the existing immutable governed Run; no second log
service or legacy P2 DecisionEvent schema is introduced.

## Verification

Pre-release focused command:

```text
PYTHONPATH=src <python> -m pytest -q tests/unit/test_structure_v2*.py tests/unit/test_structure_semantics.py tests/unit/test_temporal_contracts.py tests/contract/test_document_traceability.py
```

Observed: **134 passed**. New source, business, full synthetic pipeline, tampering and Worker
integration tests account for 36 cases; the issue-plan tests add two. Worker tests use an explicitly
labelled single-threaded dispatch store double, not PostgreSQL lease/transaction evidence.

Checks include same-valued distinct targets; corrections vs slots; scope-extension stability;
equality/loss/reclaim; gap/action censoring; explicit denominators; immutable usage; synthetic A/B
same semantic result with distinct physical audit; malicious rehashed business fields, audit
substitution, partial inputs, omitted consumer targets, changed blind samples and invented fills;
static worker dispatch and sealed recovery without another child.

Ruff on changed Python: PASS. `git diff --check`: PASS.
Repository-wide `make check`: **1528 passed, 7 skipped** (193.90 seconds for pytest).
The seven skips retain unmounted production expert artifacts and unconfigured PostgreSQL context;
they are not passes. This is not yet evidence of real PostgreSQL execution or real DWD v2 output.

## Exact next gates

Publish exact tested revision, deploy only Event Worker with all seeders off, use governed submit
with the new fixed config, execute A and B cold once each, independently compare semantic/ADM/input
readiness identities, inspect bounded T-visible samples and close #219 -> #220 -> #221 -> #222 ->
#216 only with respective evidence. Reuse B1/B1-R closure; do not rerun v1 or the economic experiment.
If host admission does not support this explicit path, report that exact boundary and do not swap
into an old config or enable seeding as a bypass. Dictionary #215 and B6-B11 remain separate.
