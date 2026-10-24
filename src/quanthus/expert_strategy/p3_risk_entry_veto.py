"""One frozen TCH/TR exposed-window test, separate from the sealed five-cell run.

Only the strategy entry veto abstains. Fixed-cap entry, full held policy and
corrected accounting are composed without changing shared production policies.
Retained prerequisites are admitted per role, never as an implicit shared cache.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_base24_native as n
from quanthus.expert_strategy import p3_component_checkpoint as checkpoint
from quanthus.expert_strategy import p3_retrospective_accounting_v2 as corrected
from quanthus.expert_strategy import p3_risk_components as components
from quanthus.expert_strategy.p3_retrospective_analysis import path_metrics
from quanthus.expert_strategy.runner import FormalStrategyDecision, FormalStrategyRunner

VERSION = "p3-risk-entry-veto-retrospective-v1"
REQUEST_PATH = "config/research/p3-strat-003/risk-entry-veto.v1.json"
ACTIVATION = "risk-entry-veto-execution.v1.json"
VARIANTS = {
    "TR": {"entry_filter": True, "entry_cap": True, "held_risk": True},
    "TCH": {"entry_filter": False, "entry_cap": True, "held_risk": True},
}
ORDER = ("TR", "TCH")


def load_request(repository: Path) -> dict[str, Any]:
    request = n._json(repository / REQUEST_PATH)
    parent = repository / "config/research/p3-strat-003"
    proposal = n._json(parent / "risk-successor-proposal.v1.json")
    stage = proposal["validation_stages"]["A_exposed_window_mechanism"]
    old = components.load_request(repository)
    if (
        request.get("schema_version") != "quanthus.p3-risk-entry-veto.v1"
        or request.get("experiment_id") != VERSION
        or request.get("variants") != VARIANTS
        or any(type(x) is not bool for v in request["variants"].values() for x in v.values())
        or request.get("execution_order") != list(ORDER)
        or request.get("roles") != ["primary", "independent_cold_reproduction"]
        or request.get("contrast") != {"TCH": 1, "TR": -1}
        or request.get("scope") != old["scope"]
        or request.get("information_cutoff") != old["information_cutoff"]
        or request.get("already_observed_window") is not True
        or request.get("proposal_sha256") != n._sha(parent / "risk-successor-proposal.v1.json")
        or request.get("card_sha256") != n._sha(parent / "risk-entry-veto-card.v1.json")
        or request.get("mapping_sha256") != n._sha(parent / "risk-entry-veto-mapping.v1.json")
        or request.get("analysis") != stage["analysis"]
        or request.get("business_screen") != stage["business_screen"]
        or request.get("budget") != stage["budget"]
        or any(
            request.get(k) != old[k]
            for k in ("original_request_sha256", "strategy_card_sha256", "anchor_snapshot_id")
        )
        or request.get("anchor_artifact_sha256") != {"TR": old["anchor_artifact_sha256"]["TR"]}
        or request.get("anchor_decision_sha256") != {"TR": old["anchor_decision_sha256"]["TR"]}
        or any(
            request.get(k) is not False
            for k in (
                "original_request_replaced",
                "original_B_accepted",
                "original_D_activated",
                "historical_availability_claim",
                "P6_mutation_authorized",
                "production_authorized",
                "fresh_pilot_authorized",
                "parameter_search",
                "source_collection_authorized",
            )
        )
    ):
        raise n.InputError("ENTRY_VETO_REQUEST_BINDING")
    return request


def business_bundle(
    artifacts: Mapping[str, Any],
    coordinates: Mapping[str, str],
    *,
    snapshot: str,
    anchor_snapshot: str,
    snapshot_suffixes: Sequence[str],
) -> dict[str, str]:
    """Normalize typed generated IDs through their complete semantic graph.

    Source snapshot mapping is exact; ledger snapshots have distinct generated
    identities. Signals, intents, orders, fills, settlements and ledger entries
    are bound topologically by their full business payload and existing edges.
    Only opaque identity and raw equal-time UUID ordering disappear. Every value,
    local sequence, event time and event multiplicity remains in the digest.
    """
    names = {
        "decisions.json",
        "engine-events.json",
        "equity.json",
        "daily-returns.json",
        "final-ledgers.json",
        "reconciliation.json",
    }
    if set(artifacts) != names:
        raise n.InputError("ENTRY_VETO_FULL_ANCHOR_FILES")
    decisions = artifacts["decisions.json"]
    decision_ids: dict[str, str] = {}
    for source in decisions:
        row = {k: v for k, v in source.items() if k != "formal_decision_digest"}
        if row["surface"] == "entry":
            if row["decision_key"] not in coordinates:
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_COORDINATES")
            row["decision_key"] = coordinates[row["decision_key"]]
        value = digest_value(row)
        if decision_ids.setdefault(source["formal_decision_digest"], value) != value:
            raise n.InputError("ENTRY_VETO_FULL_ANCHOR_DECISION_ID")
    events = artifacts["engine-events.json"]
    id_fields = {"signal_id", "intent_id", "order_id", "fill_id", "entry_id", "settlement_id"}
    ids: dict[str, str] = {}
    inverse: dict[str, str] = {}
    snapshots: dict[str, str] = {}
    action_ids = {
        e["payload"]["action_id"] for e in events if e["event_type"] == "corporate_action"
    }
    if len({e["event_id"] for e in events}) != len(events):
        raise n.InputError("ENTRY_VETO_FULL_ANCHOR_EVENT_ID")
    for e in events:
        payload = e["payload"]
        if e["event_type"] == "daily_snapshot":
            raw = payload.get("snapshot_id")
            if isinstance(raw, str) and raw.startswith("lsnap_"):
                token = "ledger-snapshot:" + digest_value(
                    [payload["unit_id"], payload["session_date"]]
                )
                if (
                    snapshots.setdefault(raw, token) != token
                    or inverse.setdefault(token, raw) != raw
                ):
                    raise n.InputError("ENTRY_VETO_FULL_ANCHOR_LEDGER_SNAPSHOT")

    def walk(value: Any, field: str = "") -> Any:
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, field) for v in value]
        if field in id_fields:
            if value not in ids:
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_ID_REFERENCE")
            return ids[value]
        if field == "reference_id":
            if value in ids:
                return ids[value]
            if value not in action_ids:
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_REFERENCE")
        if field == "decision_version":
            prefix, sep, digest = value.rpartition("|")
            if not sep or digest not in decision_ids:
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_DECISION_REFERENCE")
            return prefix + sep + decision_ids[digest]
        if field.endswith("snapshot_id") and isinstance(value, str):
            if value in snapshots:
                return snapshots[value]
            for suffix in ["", *snapshot_suffixes]:
                if value == snapshot + suffix:
                    return anchor_snapshot + suffix
            if value.startswith(("lsnap_", "p3-base24-retrospective-comparison-v1/")):
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_UNKNOWN_SNAPSHOT")
        return value

    definitions = [
        ("signal_accepted", "signal_id"),
        ("order_intent_created", "intent_id"),
        ("order_created", "order_id"),
        ("fill", "fill_id"),
        ("unit_settlement", "settlement_id"),
        ("ledger_entry", "entry_id"),
    ]
    for event_type, field in definitions:
        for e in events:
            if e["event_type"] != event_type:
                continue
            raw = e["payload"][field]
            content = walk({k: v for k, v in e["payload"].items() if k != field})
            token = (
                field
                + ":"
                + digest_value(
                    {
                        "payload": content,
                        "event_time": e["event_time"],
                        "reason_code": e["reason_code"],
                        "sequence": e["sequence"],
                    }
                )
            )
            if ids.setdefault(raw, token) != token or inverse.setdefault(token, raw) != raw:
                raise n.InputError("ENTRY_VETO_FULL_ANCHOR_ID_BIJECTION")
    normalized_events = [walk({k: v for k, v in e.items() if k != "event_id"}) for e in events]
    result = {
        name: digest_value(walk(artifacts[name]))
        for name in names
        if name not in {"decisions.json", "engine-events.json"}
    }
    result["decisions.json"] = components.decision_anchor_digest(decisions, coordinates)
    result["engine-events.json"] = digest_value(sorted(normalized_events, key=canonical_json_bytes))
    return result


class EntryVetoRunner(components.ComponentEntryRunner):
    def __init__(self, runner: FormalStrategyRunner, variant: str) -> None:
        if variant not in VARIANTS:
            raise n.InputError("ENTRY_VETO_VARIANT")
        super().__init__(runner, "TC" if variant == "TCH" else "TR")
        self.successor_variant = variant

    def evaluate_successor(self, **kwargs: Any) -> FormalStrategyDecision:
        decision = self.evaluate_component(**kwargs)
        # Pure policy anchor on this identical context, not a third replay.
        reference = components.ComponentEntryRunner(
            FormalStrategyRunner(self.card, self.topology_digest), self.component_variant
        ).evaluate_component(**kwargs)
        if decision != reference:
            raise n.InputError("ENTRY_VETO_SAME_CONTEXT_POLICY_DRIFT")
        return replace(decision, intervention_id=VERSION + ":" + self.successor_variant)


class EntryVetoEngine(corrected.CorrectedRetrospectiveEngine):
    runtime_version = VERSION


class RiskEntryVetoRuntime(corrected.CorrectedRetrospectiveRuntime):
    runtime_version = VERSION
    activation_name = ACTIVATION
    engine_type = EntryVetoEngine

    def __init__(self, repository: Path, *args: Any, **kwargs: Any) -> None:
        self._successor_repository = repository
        self.component_request = load_request(repository)
        self.request_digest = digest_value(self.component_request)
        self.entry_runners: dict[str, EntryVetoRunner] = {}
        receipt = kwargs.get("consumer_admission", {})
        self.retained_index = receipt.get("retained_session_index")
        self.full_tr_anchor_sha = receipt.get("full_tr_anchor_sha256")
        self.result_role = receipt.get("result_role", "synthetic")
        super().__init__(repository, *args, **kwargs)

    def _adapter_binding(self) -> dict[str, Any]:
        binding = {
            "runtime_version": VERSION,
            "accounting_version": accounting.VERSION,
            "code_config_digest": digest_value(self.r2_plan["code_config_members"]),
            "experiment_id": VERSION,
            "experiment_request_digest": self.request_digest,
        }
        if self.retained_index is not None:
            binding["retained_session_index"] = self.retained_index
        if self.full_tr_anchor_sha is not None:
            binding["full_tr_anchor_sha256"] = self.full_tr_anchor_sha
        return binding

    def _build_session_index(self) -> dict[date, tuple[Any, Any]]:
        if self.retained_index is None:
            return super()._build_session_index()
        descriptor = self.retained_index
        proof_path = self.workspace.parent / "retained-index-proof.json"
        expected = {"schema_version", "source_acceptance_sha256", "mapping_descriptor"}
        if (
            set(descriptor) != expected
            or descriptor["schema_version"] != "quanthus.p3-entry-veto-retained-index.v1"
            or descriptor["source_acceptance_sha256"]
            != n._json(
                self.repository_for_proof
                / "config/research/p3-strat-003/risk-successor-proposal.v1.json"
            )["frozen_bindings"]["step2_acceptance_sha256"]
        ):
            raise n.InputError("ENTRY_VETO_RETAINED_INDEX_BINDING")
        mapping = descriptor["mapping_descriptor"]
        if n._sha(proof_path) != mapping["producer_proof_sha256"]:
            raise n.InputError("ENTRY_VETO_RETAINED_PROOF_HASH")
        proof = n._json(proof_path)
        if (
            proof.get("kind") != "entry-veto-role-specific-prerequisite-reuse-v1"
            or proof.get("role") != self.result_role
            or proof.get("source_acceptance_sha256") != descriptor["source_acceptance_sha256"]
            or proof.get("producer_semantics_unchanged") is not True
            or proof.get("cold_uses_primary_prerequisites") is not False
            or proof.get("input_identity") != checkpoint.input_identity(self.prepared, self.r2_plan)
            or proof.get("index_sha256") != mapping["index_sha256"]
            or mapping.get("prior_runtime_seconds") != 0
        ):
            raise n.InputError("ENTRY_VETO_RETAINED_PROOF_BINDING")
        return checkpoint.resume_index(
            self.prepared,
            self.workspace,
            self.r2_plan,
            self.snapshot_id,
            mapping,
            self.result_role,
            self._guard,
        )

    @property
    def repository_for_proof(self) -> Path:
        # The ordinary repository is provided before native runtime initialization.
        return self._successor_repository

    @staticmethod
    def token_for(variant_id: str) -> str:
        if variant_id not in VARIANTS:
            raise n.InputError("ENTRY_VETO_VARIANT")
        return VERSION + ":" + variant_id

    @staticmethod
    def variant_for(token: str | None) -> str:
        for name in VARIANTS:
            if token == VERSION + ":" + name:
                return name
        raise n.InputError("ENTRY_VETO_VARIANT")

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        return "variant." + RiskEntryVetoRuntime.variant_for(intervention_id)

    def _baseline_variant_directory(self) -> str:
        return "variant.TR"

    def _metrics_intervention_id(self, intervention_id: str | None) -> str | None:
        return None if self.variant_for(intervention_id) == "TR" else intervention_id

    def _held_intervention_id(self, intervention_id: str | None) -> None:
        self.variant_for(intervention_id)
        return None

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        variant = self.variant_for(intervention_id)
        return {
            **self._adapter_binding(),
            "original_request_digest": self.request.digest,
            "variant_id": variant,
            "components": VARIANTS[variant],
            "scope": self.r2_plan["scope"],
            "information_cutoff": self.r2_plan["information_cutoff"],
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "result_namespace": VERSION + "/" + self.variant_for(intervention_id),
            "components": VARIANTS[self.variant_for(intervention_id)],
            "successor_strategy_selected": False,
        }

    def _evaluate_entry_decision(
        self, runner: FormalStrategyRunner, *, intervention_id: str | None, **kwargs: Any
    ) -> FormalStrategyDecision:
        variant = self.variant_for(intervention_id)
        if variant not in self.entry_runners:
            self.entry_runners[variant] = EntryVetoRunner(runner, variant)
        return self.entry_runners[variant].evaluate_successor(**kwargs)

    def _verify_anchor(self, target: Path, variant: str) -> None:
        for name, sha in self.component_request["anchor_artifact_sha256"][variant].items():
            observed = (
                components.ledger_anchor_digest(
                    (target / name).read_bytes(),
                    current_snapshot=self.snapshot_id,
                    old_snapshot=self.component_request["anchor_snapshot_id"],
                )
                if name == "final-ledgers.json"
                else n._sha(target / name)
            )
            if observed != sha:
                raise n.InputError("COMPONENT_OLD_ANCHOR_MISMATCH")
        coordinates = {
            "entry:" + e.record.event_id: e.record.identity.security_id
            + ":"
            + e.record.identity.signal_date.isoformat()
            for e in self.events
        }
        observed = components.decision_anchor_digest(
            json.loads((target / "decisions.json").read_bytes()), coordinates
        )
        if observed != self.component_request["anchor_decision_sha256"][variant]:
            raise n.InputError("COMPONENT_OLD_DECISION_ANCHOR_MISMATCH")

        anchor_path = self.workspace.parent / "full-TR-anchor.json"
        if self.full_tr_anchor_sha is None or n._sha(anchor_path) != self.full_tr_anchor_sha:
            raise n.InputError("ENTRY_VETO_FULL_ANCHOR_BINDING")
        anchor = n._json(anchor_path)
        actual = business_bundle(
            {name: json.loads((target / name).read_bytes()) for name in anchor["digests"]},
            coordinates,
            snapshot=self.snapshot_id,
            anchor_snapshot=self.component_request["anchor_snapshot_id"],
            snapshot_suffixes=anchor["snapshot_suffixes"],
        )
        if actual != anchor["digests"] or anchor["source_result_role"] != self.result_role:
            raise n.InputError("ENTRY_VETO_FULL_TR_BUSINESS_MISMATCH")

    def execute_all(self) -> dict[str, Any]:
        results = {}
        for variant in ORDER:
            self._guard()
            target = self.workspace / ("variant." + variant)
            target.mkdir()
            try:
                evidence, artifacts = self.run_research_variant(variant, target)
                for name, payload in artifacts.items():
                    (target / name).write_bytes(payload)
                if not self.synthetic and variant == "TR":
                    self._verify_anchor(target, variant)
                results[variant] = evidence.replay_digest
            except Exception as exc:
                (self.workspace / "FAILED.json").write_bytes(
                    canonical_json_bytes(
                        {
                            **self._adapter_binding(),
                            "failed_variant": variant,
                            "failure_type": type(exc).__name__,
                            "completed_variants": list(results),
                            "complete": False,
                            "outputs_are_partial": True,
                        }
                    )
                )
                raise
        report = {
            **self._adapter_binding(),
            "variants": results,
            "mode": "SYNTHETIC_ONLY" if self.synthetic else "ADMITTED_EXPOSED_WINDOW_MECHANISM",
            "provider_builds": 1,
            "feature_panel_builds": 0,
            "session_index_builds": int(self.retained_index is None),
            "retained_own_role_index_reuses": int(self.retained_index is not None),
            "real_comparisons_executed": 0 if self.synthetic else 2,
            "information_cutoff": self.info.isoformat(),
            "old_business_anchors_verified": not self.synthetic,
            "production_authorized": False,
        }
        self._guard()
        (self.workspace / "COMPLETE.json").write_bytes(canonical_json_bytes(report))
        return report


def analyze_successor(
    request: Mapping[str, Any],
    series: Mapping[str, Sequence[Mapping[str, str]]],
    points: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, Any]:
    """Exactly one paired contrast; full points keep the incomplete tail."""
    if (
        set(series) != set(VARIANTS)
        or set(points) != set(VARIANTS)
        or request.get("contrast") != {"TCH": 1, "TR": -1}
        or request.get("analysis", {}).get("paired_block_sessions") != 22
        or request.get("analysis", {}).get("resamples") != 2000
    ):
        raise ValueError("ENTRY_VETO_ANALYSIS_BINDING")
    dates = [r["market_date"] for r in series["TR"]]
    if (
        dates != sorted(set(dates))
        or any([r["market_date"] for r in rows] != dates for rows in series.values())
        or any(not date(2025, 1, 1) <= date.fromisoformat(d) <= date(2026, 9, 18) for d in dates)
        or any(not x.is_finite() for p in points.values() for x in p.values())
    ):
        raise ValueError("ENTRY_VETO_ANALYSIS_DATES_METRICS")
    values = {v: [Decimal(r["return"]) for r in rows] for v, rows in series.items()}
    for rows in values.values():
        path_metrics(rows)
    count = len(dates) // 22
    if not count:
        raise ValueError("ENTRY_VETO_ANALYSIS_NO_BLOCK")
    seed = int(hashlib.sha256((digest_value(dict(request)) + ":TCH-TR").encode()).hexdigest(), 16)
    generator = random.Random(seed)
    draws: dict[str, list[Decimal]] = {"net_total_return": [], "maximum_drawdown": []}
    for _ in range(2000):
        sample = [generator.randrange(count) for _ in range(count)]
        metrics = {
            v: path_metrics([r for i in sample for r in values[v][i * 22 : (i + 1) * 22]])
            for v in VARIANTS
        }
        for key in draws:
            draws[key].append(metrics["TCH"][key] - metrics["TR"][key])
    delta = {k: points["TCH"][k] - points["TR"][k] for k in draws}
    # Exact predeclared resource screen, not original strategy acceptance.
    passed = (
        delta["net_total_return"] >= Decimal(".01") and delta["maximum_drawdown"] <= Decimal(".01")
    ) or (
        delta["maximum_drawdown"] <= Decimal("-.01")
        and delta["net_total_return"] >= Decimal("-.01")
    )
    return {
        "experiment_request_digest": digest_value(dict(request)),
        "contrast": "TCH-TR",
        "seed": str(seed),
        "point_delta": {k: str(v) for k, v in delta.items()},
        "paired_block_95_interval": {
            k: {
                "lower": str(sorted(xs)[math.floor(0.025 * 1999)]),
                "upper": str(sorted(xs)[math.floor(0.975 * 1999)]),
            }
            for k, xs in draws.items()
        },
        "full_window_sessions": len(dates),
        "complete_blocks": count,
        "incomplete_block_dates_excluded_only_from_resampling": dates[count * 22 :],
        "resource_screen_passed": passed,
        "decision": "FRESH_PILOT_MAY_BE_PRIORITIZED_REQUIRES_SEPARATE_AUTHORITY"
        if passed
        else "CLOSE_NO_MATERIAL_JUSTIFICATION_OR_INCONCLUSIVE",
        "successor_strategy_selected": False,
        "confirmatory_acceptance": False,
        "production_authorized": False,
        "fresh_pilot_authorized": False,
    }
