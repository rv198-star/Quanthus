# Entry consolidation: approved steps1–3

| Field | Value |
| --- | --- |
| Design status | approved for the bounded A–C diagnostic; later confirmation remains a draft request |
| Delivery gate | accepted for development diagnosis only; learning quality/value/admission remain open |
| Parent | [Conditional route and A–C amendment](../work-items/learning-next-route-20261028.md) |
| Work items | #540 coordination; #542 qualification; #545 consumer boundary; #546 retained assets |
| Governing request | [Versioned diagnostic](../../config/research/learning-mvp/entry-consolidation-diagnostic.v1.json) |
| Executed code | `09037518e3d65bdd6af93c9dec3f3b91993ed11a` |
| Source/evidence job | `cn02:/srv/agentdock/.codex/learning-entry-diagnostic-20261028T164040Z-e9b7634` |

This batch completes its three scoped steps. It does not complete the learning
MVP or claim that the model selects better trades. There was no new training,
update, annotation, acquisition, economic replay, P6 change or production change.

## 1. Minimum bridge and fixed use

Restore only the unchanged `head2_facts.py`, `head2_revision.py` and
`head_comparison.py` closure. The retained seven-feature checkpoint/scaler and R0
predicate remain unchanged. New `entry_baseline.py` recovers the original B event
namespace and requires an exact bijection to its decision record. The runtime's
snapshot differs from the older preparation snapshot; the adapter uses the exact
original runtime request, not a guessed identity. All22,955 events matched.

`entry_consolidation.py` computes forty venue sessions ending T−1 once and shares
the immutable Facts between both controls. C0 is the retained B arm with Trend,
entry cap and held risk disabled, exactly as originally run. Candidate filters
only withhold entries. They cannot reenable a rejected B decision, create a sale,
resize a position, suppress an exit or infer a fill.

Nineteen focused regressions cover event/source/date mismatches, duplicates,
company/alias exclusions, T−1, post-training timing, missing/zero/action-reset
inputs, baseline precedence, explicit futility stops and safe disposition.
Focused Ruff and mypy over all six new/restored source files pass.
`make check` passed:5,548 tests passed,20 skipped,8 warnings; total585.59s,
including580.55s of pytest. The skip reasons remain missing production artifacts,
PostgreSQL configuration, sklearn or torch. Repository Ruff still reports the
same216 existing findings, so the repository is not fully lint-green. No remote CI,
production, new training or independent economic confirmation ran in this batch.

## 2. Actual development census

Before new scoring, freeze the exact retained model, control preparation and
decisions, original runtime request,34 metadata files, selected accepted source
members and supplemental action evidence, current code/config closure and budget.
Input identity is
`sha256:5ab3d8d7cf1852bf4e34fe2523f6deed85e28d5cc1386c1457da7b916de646d0`.
The code reads only selected historical raw inputs and preserves the declared
Decimal(str(binary64)) convention and unavailable knowledge times. This is
retrospective development, not historical PIT or fresh confirmation.

Of22,955 original opportunities,22,041 have signals no later than the model's
2021-08-31 last training observation. Another116 hit excluded evaluation/recent
issuer aliases;8 have a T−1 anchor no later than training. The frozen remainder
is790 opportunities across669 issuer codes. Exclusions cover775 identities and
777 aliases: preserved global identities, old evaluation pools, conservatively
all recent Head2 selected roles and original31, with whole-company alias closure.
Other known old development can be reused; it is not relabeled unseen.

| Population | C0: original B | C1: B + fixed R0 | C2: B + seven features |
| --- | ---: | ---: | ---: |
| Full790 opportunities | 433 | 58 | 277 |
| Common known760 opportunities | 416 | 58 | 277 |
| Unknown30 opportunities | 17 | 0 | 0 |

The30 unavailable cases remain in the full denominator:24 cross a corporate
action coordinate reset and6 contain zero-activity observations. Neither candidate
turns them into a forced sale or known disagreement.

Among known inputs,221 entry decisions differ:220 are C2-only and1 is C1-only.
The candidate is substantially more permissive than R0. This is not evidence of
better precision, profitability or useful consolidation recognition.

Differences span208 issuers and75 dates. The largest issuer contributes2;
removing it leaves219 differences. The largest date contributes10; removing it
leaves211. Entry events are the original units, not independent held episodes;
multiple events from one issuer remain clustered.

The run used one bounded raw projection, consumed31,420 rows through its per-code
ranges and built790 immutable Facts materializations, each consumed twice.
It took15.19s in the measured run, with
212,799,488 bytes peak RSS. Source hashes were checked before and after and
remained unchanged. One separately written arithmetic audit rechecked all790
selection/decision relations, R0 predicates and model logits from the sealed Facts,
with no raw market/return-source reopening. The largest score difference was
1.11e−16 and every threshold classification matched. This is a separate arithmetic
check, not an external scientific audit or independent source rebuild.

The terminal12 events have no next session inside the diagnostic's calendar
scope ending2021-12-30. Their next-open coordinate is explicitly unavailable;
no fill is inferred. A future capital comparison must qualify its complete
T+1 calendar/execution scope before it can run.

## 3. Fixed disposition and next request

Both predeclared futility checks pass: C2 is neither identical to C1 nor explained
by one issuer or date. Retain the existing candidate for one independent
qualification; do not train, add features or tune a threshold from these results.

The [single next qualification request](../../config/research/learning-mvp/entry-confirmation-qualification-request.v1.json)
pins the checkpoint, controls and consumed development identities. Its experiment
gate is blocked: a new exact cohort/untouched roles, purpose-compatible blind state
reference, finite support/error/value criteria, complete capital/execution closure
and measured budget have not been qualified. Current790 scored cases cannot be
renamed a fresh holdout. This batch has not shown that no qualifying cohort exists.

After those gates qualify, compare complete C0/C1/C2 capital paths and costs,
preserving cash left uninvested and unknown inputs. Independently distinguish
recognition quality from decision value. Only worthwhile qualified value can
justify a separately governed P6 proposal. If independent qualification fails,
close this branch with the exact limitation and retain the working assets.

The blueprinted outcome is therefore: **entry diagnostic engineering complete;
dispersed development difference observed; quality/value/admission unaccepted**.
