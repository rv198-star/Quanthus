# Use retained learning assets through the existing expert consumer

| Field | Value |
| --- | --- |
| Design status | approved for the Owner's bounded 2026-10-29 delivery |
| Delivery gate | example validation is recorded in the [batch acceptance](../acceptance/learning-consumer-cycle-20261029.md) |
| Parent | [Supported asset boundary](../design/learning-assets-v1.md) |
| Work items | #540 coordination; #545 consumer interface; #546 delivery |
| Consumer | `FormalStrategyRunner.evaluate_entry` / `evaluate_held`, followed by the existing aggregator |

Use the existing consumer rather than writing another runner. The concrete delivery here is
a reproducible calling example and its input/output contract. It reads ordinary code and the
existing frozen strategy registry, uses synthetic observations, and writes JSON to stdout.
It has no orders, data collection, model fitting or service side effects.

## Supported invocation

Run from the repository root with Python 3.10 or newer. Save the following block outside the
repository if a local script is convenient, then invoke it with `PYTHONPATH=src python3 -B`.
When using stdin, append `-` to that command and provide the block on stdin. The existing
strategy card supplies all policy thresholds; the example changes none of them.

```python
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.aggregator import aggregate_score_and_map_action
from quanthus.expert_protocols import EvidenceKind, ExpertEvidenceRef
from quanthus.expert_strategy import (
    ExpertStrategyRegistry, FormalExpertContext, FormalStrategyRunner, HeldPositionFacts,
)
from quanthus.expert_strategy.runner import position_facts_evidence
from quanthus.feature_registry import FeatureCutoffRule, FeatureLineage, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact
from quanthus.stage_learning.head2_consumer import pending_assessment

root = Path.cwd()
cutoff = datetime(2020, 1, 6, 7, tzinfo=timezone.utc)
event = "event:asset-consumer-synthetic"
snapshot = "synthetic-assets-only"

def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

def evidence(name):
    return ExpertEvidenceRef(EvidenceKind.SNAPSHOT, name, "1.0.0", digest(name))

def fact(name, value):
    source = FeatureSourceEvidence("synthetic", name, "1.0.0", snapshot, cutoff)
    lineage = FeatureLineage(
        feature_id=name, feature_version="1.0.0", definition_digest=digest(name),
        snapshot_id=snapshot, market_date=date(2020, 1, 6), knowledge_cutoff=cutoff,
        source_revision="synthetic", universe_policy_version="synthetic",
        sources=(source,), history_sessions=60, cross_section_complete=None,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    )
    return FeatureFact(
        security_code="synthetic-security", feature_id=name, feature_version="1.0.0",
        lineage=lineage, value=value,
        unavailable_reason="synthetic_unavailable" if value is None else None,
    )

registry = ExpertStrategyRegistry(root)
runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
held_ref = position_facts_evidence(
    unit_id="synthetic-unit", holding_sessions=3, marked_return=None,
    prior_strategy_reduction_count=0,
)

def context(missing=False, held=False):
    return FormalExpertContext(
        security_code="synthetic-security", snapshot_id=snapshot, knowledge_cutoff=cutoff,
        position_state="held" if held else "empty",
        feature_facts=(fact("price.rsi14", None if missing else Decimal("60")),
                       fact("risk.realized_volatility20", Decimal("0.20"))),
        label_results=(),
        additional_evidence_refs=(evidence(event), evidence("trend-observation:" + event),
                                  held_ref),
    )

ready = runner.evaluate_entry(
    event_id=event, context=context(), trend_state="stable", entry_evidence_ready=True,
)
missing = runner.evaluate_entry(
    event_id=event, context=context(missing=True), trend_state="stable",
    entry_evidence_ready=True,
)
held = runner.evaluate_held(
    event_id=event, context=context(missing=True, held=True),
    position_facts=HeldPositionFacts(3, None, 0, held_ref),
)
pending = pending_assessment(
    context(), raw_score=1.0, model_digest=digest("synthetic-model-not-admitted"),
    purpose_digest=digest("synthetic-purpose"),
)
combined = aggregate_score_and_map_action(
    context(), ready.governance,
    (*ready.action.scoring_outputs, pending.assessment), runner.score_policy,
)
blocked = aggregate_score_and_map_action(
    context(missing=True), missing.governance,
    (*missing.action.scoring_outputs, pending.assessment), runner.score_policy,
)
print(json.dumps({
    "scope": "SYNTHETIC_INTERFACE_EXAMPLE", "execution_authorized": False,
    "strategy_card_digest": registry.card.semantic_digest,
    "topology_digest": registry.topology_manifest.digest,
    "decisions": {"ready_entry": ready.to_dict(), "missing_entry": missing.to_dict(),
                  "missing_held": held.to_dict()},
    "pending_head2": {"model_digest": pending.model_digest,
                      "purpose_digest": pending.purpose_digest,
                      "admission": pending.consumer_admission,
                      "reason": pending.assessment.reason_code,
                      "applicability": pending.assessment.applicability.value,
                      "calibrated_probability": pending.calibrated_probability,
                      "combined_action": combined.final_action.value,
                      "blocked_action": blocked.final_action.value},
}, indent=2))
```

The expected actions are `trial_entry`, `no_action`, and `no_action`. The pending Head2
assessment remains `abstained` / `NOT_GRANTED`; its raw score changes neither the permitted
entry nor the blocked entry. The JSON includes exact card/topology identity, policy and
governance digests, evidence and reason codes. Inspect those fields when a caller asks why an
input was rejected. A hypothetical `trial_entry` is a decision proposal, never a fill.

## Reusing it with governed inputs

| Input / asset | Caller obligation | Failure meaning |
| --- | --- | --- |
| Feature facts | Exact security/snapshot/feature lineage and declared cutoff; Decimal risk values | Missing values block new entries; they do not establish structural damage or require sale |
| Trend observation | Purpose-compatible admitted observation and matching event evidence | Unknown trend abstains; do not derive it from a pending Head2 score |
| Held facts | Audited unit, holding sessions, reduction history and position evidence | Unknown marks remain unknown; existing independent hard exits retain their precedence |
| Retained model | Exact checkpoint/config/source-purpose identity and separate use qualification | Readable models are still unqualified for trading; keep the pending envelope |
| Result | Preserve complete decision JSON/digest; use the existing P2 path if separately authorized | Email, an example or a returned action is not execution permission |

The example deliberately avoids the archived March drivers. For a new real caller, freeze its
purpose and already-admitted input identities before substituting observations; this runbook
does not admit another snapshot or provide a production activation switch.

## Existing diagnostic delivery

For current task selection, reuse the completed [held diagnosis](../work-items/learning-delivery-20261029.md)
and [entry diagnostic](../acceptance/learning-entry-diagnostic-20261029.md), together with the
later March disposition. The [current batch](../work-items/learning-consumer-cycle-20261029.md)
lists their precise identities and decisions. Do not rerun their full materializations merely
to display the same conclusion.

`scripts/learning_held_decision_census.py --help` documents the existing narrow authenticated
census CLI for a separately justified rerun. An exact selection SHA and fresh output are required.
The completed result is a research diagnostic, not a new ranking, reference set or live policy.

## Review an already-recorded historical decision

The [historical review contract](../design/historical-decision-review-v1.md) adds a
read-only CLI for the exact previously-consumed #545 archive. This path joins recorded
decisions and facts; it does not call the runner in the synthetic example above.
The package and local 12-category example views are located in the
[acceptance record](../acceptance/historical-decision-review-20261029.md).

Build once from the exact selection into a new, source-isolated directory. `SELECTION`
is the authorized external selection path; `PACKAGE` must have an existing physical
parent. Do not substitute a different archive or reuse an expired execution budget.

```bash
PYTHONPATH=src python3 -B scripts/learning_decision_review.py build \
  --selection "$SELECTION" --output "$PACKAGE"
```

Keep the returned manifest SHA256 in the caller's trusted receipt. Reading thereafter
needs only the package and ordinary code; source paths stored in the manifest are
not followed. Set `DIGEST` to that exact trusted digest, rather than accepting a
replacement digest supplied by an untrusted package.

```bash
PYTHONPATH=src python3 -B scripts/learning_decision_review.py verify \
  --package "$PACKAGE" --manifest-sha256 "$DIGEST"
PYTHONPATH=src python3 -B scripts/learning_decision_review.py query \
  --package "$PACKAGE" --manifest-sha256 "$DIGEST" \
  --security 000011 --date 2012-01-05 --limit 12
PYTHONPATH=src python3 -B scripts/learning_decision_review.py query \
  --package "$PACKAGE" --manifest-sha256 "$DIGEST" \
  --reason insufficient_history --limit 12 --format html --output "$VIEW"
```

`VIEW` is a new external file; the CLI does not overwrite files or add files inside
the package/source directories. `--unit` and `--episode` disambiguate multiple
capital units or entry events. Inspect `matched`, `returned` and `truncated` rather
than treating the display limit as the full denominator. `--format json` returns the
recorded values and source locators; HTML expands those same values without remote assets.

The original `trial` position state, actual 2026 retrospective cutoff and unavailable
reasons are retained. An old shadow proposal is separately identified and remains parked.
No learned-model use was observed in this retained trace; model admission, entry/allocation/
fill alternatives and historical PIT qualification are not inferred from these cards.
