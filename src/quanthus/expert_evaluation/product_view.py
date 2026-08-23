"""Digest-resolved, read-only product projection for formal expert evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.contracts import ExpertLifecycleDecision, ExpertScorecard
from quanthus.expert_evaluation.release_verification import (
    verify_formal_attribution_release,
)
from quanthus.expert_evaluation.runner import verify_expert_evaluation_run
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    artifact_from_bytes,
    digest_value,
)

PRODUCT_EXPERT_EVIDENCE_SCHEMA = "quanthus.product-expert-evidence.v1"
PRODUCT_EXPERT_PACKAGE_SCHEMA = "quanthus.product-expert-evidence-package.v1"
STRATEGY_CARD_SCHEMA = "quanthus.expert-strategy-card.v1"
STRATEGY_CARD_FIELDS = {
    "schema_version",
    "strategy_id",
    "strategy_version",
    "lifecycle",
    "owner",
    "hypothesis",
    "product_candidate",
    "population",
    "data",
    "experts",
    "shadow_experts",
    "aggregation",
    "position_and_execution",
    "interventions",
    "attribution_questions",
    "uncertainty",
    "resource_budget",
    "stop_conditions",
    "rollback",
    "non_claims",
    "governance",
    "semantic_digest",
}
STRATEGY_CARD_SEMANTIC_EXCLUDES = {
    "lifecycle",
    "owner",
    "governance",
    "semantic_digest",
}


class ProductExpertEvidenceError(RuntimeError):
    """Typed fail-closed error for an exact serving binding."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProductExpertEvidenceError("expert_evidence_schema_invalid", f"{field} differs")
    return value


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProductExpertEvidenceError("expert_evidence_schema_invalid", f"{field} differs")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ProductExpertEvidenceError("expert_evidence_schema_invalid", f"{field} differs")


def _read_json(path: Path, reason: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductExpertEvidenceError(reason, "bound artifact is unreadable") from exc


def _empty_view(state: str, reason_codes: list[str] | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "strategy_binding": None,
        "experts": [],
        "assessment_scorecards": [],
        "economic_effects": [],
        "disagreement_cases": [],
        "limitations": [],
        "lifecycle_decision": None,
        "source_digests": {},
        "reason_codes": reason_codes or [],
    }


class ProductExpertEvidence:
    """Resolve only configured immutable Runs and releases for one candidate version."""

    def __init__(
        self,
        config_path: Path,
        repository_root: Path,
        artifact_root: Path | None,
    ) -> None:
        self.config_path = config_path.resolve()
        self.repository_root = repository_root.resolve()
        self.artifact_root = artifact_root.resolve() if artifact_root is not None else None

    def candidate(self, strategy_id: str, strategy_version: str) -> dict[str, Any]:
        config = self._config()
        binding = next(
            (
                item
                for item in config["bindings"]
                if item["strategy_id"] == strategy_id
                and item["strategy_version"] == strategy_version
            ),
            None,
        )
        if binding is None:
            return _empty_view("not_applicable")
        if binding["package_id"] is None:
            return _empty_view("not_available", list(binding["missing_reason_codes"]))
        if self.artifact_root is None:
            return _empty_view("not_available", ["expert_artifact_root_unavailable"])
        package = next(
            (item for item in config["packages"] if item["package_id"] == binding["package_id"]),
            None,
        )
        if package is None or package["package_digest"] != binding["package_digest"]:
            raise ProductExpertEvidenceError(
                "expert_evidence_binding_drift", "serving binding does not resolve its package"
            )
        return self._available(package, strategy_id, strategy_version)

    def _config(self) -> dict[str, Any]:
        value = _object(
            _read_json(self.config_path, "expert_evidence_config_unreadable"),
            "config",
        )
        _exact_fields(value, {"schema_version", "config_version", "bindings", "packages"}, "config")
        if value["schema_version"] != PRODUCT_EXPERT_EVIDENCE_SCHEMA:
            raise ProductExpertEvidenceError(
                "expert_evidence_schema_invalid", "config schema differs"
            )
        bindings = _array(value["bindings"], "bindings")
        packages = _array(value["packages"], "packages")
        identities: set[tuple[str, str]] = set()
        for raw in bindings:
            item = _object(raw, "bindings[]")
            _exact_fields(
                item,
                {
                    "strategy_id",
                    "strategy_version",
                    "package_id",
                    "package_digest",
                    "missing_reason_codes",
                },
                "bindings[]",
            )
            identity = (item["strategy_id"], item["strategy_version"])
            if (
                not all(isinstance(part, str) and part for part in identity)
                or identity in identities
            ):
                raise ProductExpertEvidenceError(
                    "expert_evidence_schema_invalid", "binding identity differs"
                )
            identities.add(identity)
            missing = _array(item["missing_reason_codes"], "missing_reason_codes")
            if item["package_id"] is None:
                if item["package_digest"] is not None or not missing:
                    raise ProductExpertEvidenceError(
                        "expert_evidence_schema_invalid", "unavailable binding differs"
                    )
            elif (
                not isinstance(item["package_id"], str)
                or not isinstance(item["package_digest"], str)
                or missing
            ):
                raise ProductExpertEvidenceError(
                    "expert_evidence_schema_invalid", "available binding differs"
                )
        package_ids: set[str] = set()
        for raw in packages:
            package = _object(raw, "packages[]")
            self._verify_package_shape(package)
            package_id = str(package["package_id"])
            if package_id in package_ids:
                raise ProductExpertEvidenceError(
                    "expert_evidence_schema_invalid", "package identity is duplicated"
                )
            package_ids.add(package_id)
        if any(
            item["package_id"] is not None and item["package_id"] not in package_ids
            for item in bindings
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_binding_drift", "binding package is unavailable"
            )
        return value

    @staticmethod
    def _verify_package_shape(package: dict[str, Any]) -> None:
        _exact_fields(
            package,
            {
                "schema_version",
                "package_id",
                "package_digest",
                "candidate",
                "strategy_card",
                "evaluation_run",
                "assessment_runs",
                "attribution_release",
                "disagreement_cases",
                "lifecycle_decision",
            },
            "packages[]",
        )
        if package["schema_version"] != PRODUCT_EXPERT_PACKAGE_SCHEMA:
            raise ProductExpertEvidenceError(
                "expert_evidence_schema_invalid", "package schema differs"
            )
        payload = dict(package)
        observed = payload.pop("package_digest")
        if observed != digest_value(payload):
            raise ProductExpertEvidenceError(
                "expert_evidence_package_digest_mismatch", "package digest differs"
            )
        for field, expected in (
            ("candidate", {"strategy_id", "strategy_version"}),
            ("strategy_card", {"repository_key", "digest"}),
            ("evaluation_run", {"run_id", "manifest_digest", "scorecard_digest"}),
            ("attribution_release", {"release_id", "release_digest"}),
        ):
            _exact_fields(_object(package[field], field), expected, field)
        for item in _array(package["assessment_runs"], "assessment_runs"):
            _exact_fields(
                _object(item, "assessment_runs[]"),
                {
                    "assessment_id",
                    "assessment_version",
                    "run_id",
                    "manifest_digest",
                    "scorecard_digest",
                },
                "assessment_runs[]",
            )
        for item in _array(package["disagreement_cases"], "disagreement_cases"):
            _exact_fields(
                _object(item, "disagreement_cases[]"),
                {"case_id", "expert_id", "native_variant", "comparison_variant", "decision_key"},
                "disagreement_cases[]",
            )
        decision = package["lifecycle_decision"]
        if decision is not None:
            _exact_fields(
                _object(decision, "lifecycle_decision"),
                {"repository_key", "digest"},
                "lifecycle_decision",
            )

    def _available(
        self, package: dict[str, Any], strategy_id: str, strategy_version: str
    ) -> dict[str, Any]:
        candidate = _object(package["candidate"], "candidate")
        if (candidate["strategy_id"], candidate["strategy_version"]) != (
            strategy_id,
            strategy_version,
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_candidate_mismatch", "package candidate identity differs"
            )
        card_ref = _object(package["strategy_card"], "strategy_card")
        card = self._strategy_card(card_ref, strategy_id, strategy_version)
        card_digest = str(card["semantic_digest"])

        run_ref = _object(package["evaluation_run"], "evaluation_run")
        run = self._verify_run(run_ref)
        strategy_identity = _object(run.manifest.get("strategy_identity"), "strategy_identity")
        if (
            strategy_identity.get("strategy_id"),
            strategy_identity.get("strategy_version"),
            strategy_identity.get("card_digest"),
        ) != (strategy_id, strategy_version, card_digest):
            raise ProductExpertEvidenceError(
                "expert_evidence_run_mismatch", "formal evaluation Run strategy differs"
            )
        topology_digest = self._topology_digest(card, run.manifest)
        formal_scorecard = _object(
            _read_json(run.root / "scorecard.json", "expert_evidence_scorecard_unreadable"),
            "formal scorecard",
        )
        if (
            digest_value(formal_scorecard) != run_ref["scorecard_digest"]
            or formal_scorecard.get("strategy_id") != strategy_id
            or formal_scorecard.get("strategy_version") != strategy_version
            or formal_scorecard.get("strategy_card_digest") != card_digest
            or formal_scorecard.get("sealed_partition_access") is not False
            or formal_scorecard.get("execution_authorized") is not False
            or formal_scorecard.get("topology_manifest_digest") != topology_digest
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_scorecard_mismatch", "formal scorecard differs"
            )

        release_ref = _object(package["attribution_release"], "attribution_release")
        assert self.artifact_root is not None
        release_root = self.artifact_root / "attribution-releases" / str(release_ref["release_id"])
        try:
            release = verify_formal_attribution_release(release_root)
        except ExpertEvaluationError as exc:
            raise ProductExpertEvidenceError(
                exc.reason_code, "attribution release differs"
            ) from exc
        if (
            release.get("release_digest") != release_ref["release_digest"]
            or release.get("strategy_id") != strategy_id
            or release.get("strategy_version") != strategy_version
            or release.get("strategy_card_digest") != card_digest
            or release.get("sealed_partition_access") is not False
            or formal_scorecard.get("release_digest") != release_ref["release_digest"]
            or release.get("evaluation_bundle_digest")
            != formal_scorecard.get("pipeline_evaluation_digest")
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_release_mismatch", "formal attribution release differs"
            )

        assessment_scorecards = self._assessment_scorecards(package, formal_scorecard)
        experts = self._experts(card, assessment_scorecards, formal_scorecard)
        lifecycle = self._lifecycle_decision(package)
        disagreement = self._disagreement_cases(package, release_root)
        effects = self._economic_effects(formal_scorecard)
        source_digests: dict[str, Any] = {
            "package": package["package_digest"],
            "strategy_card": card_digest,
            "topology": topology_digest,
            "evaluation_run": run.manifest_digest,
            "formal_scorecard": run_ref["scorecard_digest"],
            "attribution_release": release_ref["release_digest"],
            "pipeline_evaluation": formal_scorecard["pipeline_evaluation_digest"],
            "assessment_scorecards": {
                item["assessment_id"]: item["source_digest"] for item in assessment_scorecards
            },
            "lifecycle_decision": lifecycle["digest"] if lifecycle is not None else None,
        }
        return {
            "state": "available",
            "strategy_binding": {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "strategy_card_digest": card_digest,
                "topology_manifest_digest": topology_digest,
                "package_id": package["package_id"],
                "package_digest": package["package_digest"],
                "evaluation_run_id": run.run_id,
                "attribution_release_id": release_ref["release_id"],
            },
            "experts": experts,
            "assessment_scorecards": assessment_scorecards,
            "economic_effects": effects,
            "disagreement_cases": disagreement,
            "limitations": list(formal_scorecard["limitations"]),
            "lifecycle_decision": lifecycle,
            "source_digests": source_digests,
            "reason_codes": [],
        }

    def _strategy_card(
        self,
        card_ref: Mapping[str, Any],
        strategy_id: str,
        strategy_version: str,
    ) -> dict[str, Any]:
        repository_key = str(card_ref["repository_key"])
        raw_path = self.repository_root / repository_key
        path = raw_path.resolve()
        if (
            self.repository_root not in path.parents
            or raw_path.is_symlink()
            or not repository_key.startswith("config/expert-strategies/")
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_strategy_mismatch", "strategy card path differs"
            )
        card = _object(
            _read_json(path, "expert_evidence_strategy_unreadable"),
            "strategy card",
        )
        _exact_fields(card, STRATEGY_CARD_FIELDS, "strategy card")
        governance = _object(card["governance"], "strategy card governance")
        _exact_fields(
            governance,
            {"approval_state", "approval_authority", "decision_reference"},
            "strategy card governance",
        )
        semantic_digest = digest_value(
            {
                key: value
                for key, value in card.items()
                if key not in STRATEGY_CARD_SEMANTIC_EXCLUDES
            }
        )
        product_candidate = _object(card["product_candidate"], "product_candidate")
        if (
            card["schema_version"] != STRATEGY_CARD_SCHEMA
            or card["strategy_id"] != strategy_id
            or card["strategy_version"] != strategy_version
            or product_candidate.get("strategy_id") != strategy_id
            or product_candidate.get("strategy_version") != strategy_version
            or card["lifecycle"] != "approved"
            or governance.get("approval_state") != "approved"
            or governance.get("approval_authority") != "human"
            or card["semantic_digest"] != semantic_digest
            or card_ref["digest"] != semantic_digest
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_strategy_mismatch", "formal strategy card differs"
            )
        return card

    @staticmethod
    def _topology_digest(card: Mapping[str, Any], run_manifest: Mapping[str, Any]) -> str:
        experts = [_object(item, "experts[]") for item in _array(card["experts"], "experts")]
        identities = [
            _object(item, "evaluation_identities[]")
            for item in _array(run_manifest.get("evaluation_identities"), "evaluation_identities")
        ]
        if len(experts) != 2 or len(identities) != 2:
            raise ProductExpertEvidenceError(
                "expert_evidence_topology_mismatch", "formal expert closure differs"
            )
        by_assessment = {str(item.get("assessment_id")): item for item in identities}
        if len(by_assessment) != len(identities):
            raise ProductExpertEvidenceError(
                "expert_evidence_topology_mismatch", "evaluation identities differ"
            )
        evaluation_specs: list[dict[str, Any]] = []
        expert_bindings: list[dict[str, Any]] = []
        policy_mappings: list[dict[str, Any]] = []
        for expert in experts:
            expert_id = str(expert.get("expert_id"))
            expert_version = str(expert.get("expert_version"))
            identity = by_assessment.get(expert_id)
            if identity is None or identity.get("assessment_version") != expert_version:
                raise ProductExpertEvidenceError(
                    "expert_evidence_topology_mismatch", "expert evaluation identity differs"
                )
            if expert.get("route") != "policy_only" and identity.get("digest") != expert.get(
                "evaluation_spec_digest"
            ):
                raise ProductExpertEvidenceError(
                    "expert_evidence_topology_mismatch", "expert evaluation digest differs"
                )
            evaluation_specs.append(
                {
                    "assessment_id": expert_id,
                    "assessment_version": expert_version,
                    "digest": identity.get("digest"),
                }
            )
            binding_digest = (
                expert.get("expert_semantic_digest")
                if expert.get("route") == "policy_only"
                else expert.get("assessment_digest")
            )
            expert_bindings.append(
                {
                    "expert_id": expert_id,
                    "expert_version": expert_version,
                    "semantic_digest": binding_digest,
                }
            )
            mapping = _object(expert.get("policy_mapping"), "policy_mapping")
            policy_mappings.append(
                {
                    "mapping_id": mapping.get("mapping_id"),
                    "mapping_version": mapping.get("mapping_version"),
                    "digest": mapping.get("mapping_digest"),
                }
            )
        position = _object(card["position_and_execution"], "position_and_execution")
        guardians = sorted(str(item) for item in _array(position["hard_guardians"], "guardians"))
        interventions = [
            str(_object(item, "interventions[]")["intervention_id"])
            for item in _array(card["interventions"], "interventions")
        ]
        topology = {
            "schema_version": "quanthus.formal-expert-topology-manifest.v1",
            "strategy_id": card["strategy_id"],
            "strategy_version": card["strategy_version"],
            "strategy_card_digest": card["semantic_digest"],
            "evaluation_specs": sorted(
                evaluation_specs,
                key=lambda item: (
                    str(item["assessment_id"]),
                    str(item["assessment_version"]),
                    str(item["digest"]),
                ),
            ),
            "expert_bindings": expert_bindings,
            "policy_mappings": policy_mappings,
            "hard_guardians": guardians,
            "legal_interventions": interventions,
        }
        return digest_value(topology)

    def _verify_run(self, run_ref: Mapping[str, Any]) -> Any:
        assert self.artifact_root is not None
        run_id = str(run_ref["run_id"])
        try:
            run = verify_expert_evaluation_run(
                self.artifact_root / "runs" / run_id,
                expected_run_id=run_id,
            )
        except ExpertEvaluationError as exc:
            raise ProductExpertEvidenceError(exc.reason_code, "expert Run differs") from exc
        if run.manifest_digest != run_ref["manifest_digest"]:
            raise ProductExpertEvidenceError(
                "expert_evidence_run_digest_mismatch", "expert Run digest differs"
            )
        return run

    def _assessment_scorecards(
        self, package: Mapping[str, Any], formal_scorecard: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw_ref in package["assessment_runs"]:
            ref = _object(raw_ref, "assessment_runs[]")
            run = self._verify_run(ref)
            try:
                artifact = artifact_from_bytes((run.root / "scorecard.json").read_bytes())
            except (OSError, ExpertEvaluationError) as exc:
                reason = (
                    exc.reason_code
                    if isinstance(exc, ExpertEvaluationError)
                    else "expert_evidence_scorecard_unreadable"
                )
                raise ProductExpertEvidenceError(reason, "assessment scorecard differs") from exc
            if (
                not isinstance(artifact, ExpertScorecard)
                or artifact.assessment_id != ref["assessment_id"]
                or artifact.assessment_version != ref["assessment_version"]
                or artifact.digest != ref["scorecard_digest"]
            ):
                raise ProductExpertEvidenceError(
                    "expert_evidence_scorecard_mismatch", "assessment scorecard identity differs"
                )
            specs = _array(
                _read_json(
                    run.root / "evaluation-specs.json",
                    "expert_evidence_spec_unreadable",
                ),
                "evaluation-specs",
            )
            spec = next(
                (
                    _object(item, "evaluation-specs[]")
                    for item in specs
                    if isinstance(item, dict)
                    and item.get("assessment_id") == artifact.assessment_id
                ),
                None,
            )
            if spec is None:
                raise ProductExpertEvidenceError(
                    "expert_evidence_spec_mismatch", "assessment spec is absent"
                )
            primary_metric_id = _object(spec.get("primary_metric"), "primary_metric")["metric_id"]
            primary = next(
                item for item in artifact.metric_results if item.metric_id == primary_metric_id
            )
            uncertainty = next(
                (item for item in artifact.uncertainty if item.metric_id == primary_metric_id),
                None,
            )
            result.append(
                {
                    "assessment_id": artifact.assessment_id,
                    "assessment_version": artifact.assessment_version,
                    "scorecard_kind": "expert_scorecard",
                    "primary_metric": primary.to_dict(),
                    "metrics": [item.to_dict() for item in artifact.metric_results],
                    "baseline": [item.to_dict() for item in artifact.baseline_comparisons],
                    "uncertainty": uncertainty.to_dict() if uncertainty is not None else None,
                    "stability": [item.to_dict() for item in artifact.stability],
                    "sample_ledger": artifact.sample_ledger.to_dict(),
                    "decision_change_counts": artifact.decision_change_counts.to_dict(),
                    "limitations": list(artifact.limitations),
                    "source_digest": artifact.digest,
                }
            )

        claims = {item["intervention_id"]: item for item in formal_scorecard["claims"]}
        position = claims.get("intervention.position-risk-base-policy")
        if not isinstance(position, dict):
            raise ProductExpertEvidenceError(
                "expert_evidence_claim_missing", "position-risk attribution claim is absent"
            )
        effects = {item["metric_id"]: item for item in position["metric_effects"]}
        position_primary = effects.get("action_change_rate")
        count_effect = effects.get("decision_change_counts")
        if position_primary is None or count_effect is None:
            raise ProductExpertEvidenceError(
                "expert_evidence_claim_missing", "position-risk assessment metrics are absent"
            )
        result.append(
            {
                "assessment_id": "expert.position-risk-review",
                "assessment_version": "1.0.0",
                "scorecard_kind": "bounded_intervention",
                "primary_metric": position_primary,
                "metrics": [position_primary, count_effect],
                "baseline": [],
                "uncertainty": {
                    "method": position["analysis_checks"][0]["method"],
                    "source_digest": position["analysis_checks"][0]["digest"],
                },
                "stability": [],
                "sample_ledger": {
                    "evaluated": None,
                    "changed": int(count_effect["intervention_value"]),
                },
                "decision_change_counts": {
                    "action_change_rate": position_primary["intervention_value"],
                    "changed": int(count_effect["intervention_value"]),
                },
                "limitations": [
                    "Policy-only assessment is reported through the preregistered "
                    "complete-strategy intervention, not a standalone forecast scorecard."
                ],
                "source_digest": position["digest"],
            }
        )
        return sorted(result, key=lambda item: item["assessment_id"])

    @staticmethod
    def _experts(
        card: Mapping[str, Any],
        scorecards: list[dict[str, Any]],
        formal_scorecard: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        by_id = {item["assessment_id"]: item for item in scorecards}
        claims = {item["intervention_id"]: item for item in formal_scorecard["claims"]}
        intervention_by_expert = {
            "expert.trend-stability": "intervention.trend-stability-abstain",
            "expert.position-risk-review": "intervention.position-risk-base-policy",
        }
        result = []
        population = _object(card["population"], "population")
        for raw in _array(card["experts"], "experts"):
            expert = dict(raw)
            assessment_id = str(expert["expert_id"])
            scorecard = by_id[assessment_id]
            mapping = dict(expert["policy_mapping"])
            claim = claims[intervention_by_expert[assessment_id]]
            result.append(
                {
                    "expert_id": assessment_id,
                    "expert_version": expert["expert_version"],
                    "role": expert["role"],
                    "mode": "active" if expert["role"] == "active" else "shadow",
                    "assessment_type": "policy_only"
                    if expert["route"] == "policy_only"
                    else "future_target_assessment",
                    "target": scorecard["primary_metric"]["metric_id"],
                    "decision_surfaces": list(expert["decision_surfaces"]),
                    "policy_mapping": {
                        "mapping_id": mapping["mapping_id"],
                        "mapping_version": mapping["mapping_version"],
                        "mapping_digest": mapping["mapping_digest"],
                        "effect_kinds": list(mapping["effect_kinds"]),
                    },
                    "applicability": {
                        "partition": population["required_partition"],
                        "start": population["applicability_start"],
                        "end": population["applicability_end"],
                    },
                    "coverage": ProductExpertEvidence._metric_value(scorecard, "metric.coverage"),
                    "abstention": ProductExpertEvidence._metric_value(
                        scorecard, "metric.abstention-rate"
                    ),
                    "decision_change_summary": {
                        "classification": claim["classification"],
                        "status": claim["status"],
                        "counts": scorecard["decision_change_counts"],
                    },
                }
            )
        return result

    @staticmethod
    def _metric_value(scorecard: Mapping[str, Any], metric_id: str) -> str | None:
        metrics = scorecard.get("metrics")
        if isinstance(metrics, list):
            for metric in metrics:
                if isinstance(metric, dict) and metric.get("metric_id") == metric_id:
                    value = metric.get("value")
                    return str(value) if value is not None else None
        return None

    @staticmethod
    def _economic_effects(formal_scorecard: Mapping[str, Any]) -> list[dict[str, Any]]:
        result = []
        for claim in formal_scorecard["claims"]:
            metrics = {
                item["metric_id"]: item
                for item in claim["metric_effects"]
                if item["metric_id"]
                in {
                    "capital_constrained_net_total_return_delta",
                    "maximum_drawdown_delta",
                    "action_change_rate",
                }
            }
            result.append(
                {
                    "intervention_id": claim["intervention_id"],
                    "classification": claim["classification"],
                    "status": claim["status"],
                    "metrics": metrics,
                    "source_digest": claim["digest"],
                }
            )
        result.sort(key=lambda item: item["intervention_id"])
        result.append(
            {
                "intervention_id": "interaction.unallocated",
                "classification": "unallocated",
                "status": "valid",
                "metrics": dict(formal_scorecard["interaction"]["effects"]),
                "source_digest": formal_scorecard["pipeline_evaluation_digest"],
            }
        )
        return result

    def _disagreement_cases(
        self, package: Mapping[str, Any], release_root: Path
    ) -> list[dict[str, Any]]:
        variants: dict[str, dict[str, dict[str, Any]]] = {}
        result: list[dict[str, Any]] = []
        for raw in package["disagreement_cases"]:
            ref = _object(raw, "disagreement_cases[]")
            for variant_id in (ref["native_variant"], ref["comparison_variant"]):
                if variant_id not in variants:
                    values = _array(
                        _read_json(
                            release_root
                            / "replays"
                            / "variants"
                            / str(variant_id)
                            / "decisions.json",
                            "expert_evidence_disagreement_unreadable",
                        ),
                        "decisions",
                    )
                    variants[str(variant_id)] = {
                        str(item["decision_key"]): _object(item, "decisions[]") for item in values
                    }
            native = variants[str(ref["native_variant"])].get(str(ref["decision_key"]))
            comparison = variants[str(ref["comparison_variant"])].get(str(ref["decision_key"]))
            if native is None or comparison is None:
                raise ProductExpertEvidenceError(
                    "expert_evidence_disagreement_mismatch", "preselected disagreement is absent"
                )
            result.append(
                {
                    "case_id": ref["case_id"],
                    "expert_id": ref["expert_id"],
                    "decision_key": ref["decision_key"],
                    "market_date": native["market_date"],
                    "surface": native["surface"],
                    "native": {
                        "action": native["final_action"],
                        "target_maximum_position": native["target_maximum_position"],
                        "reason_codes": list(native["reason_codes"]),
                        "decision_digest": native["formal_decision_digest"],
                    },
                    "comparison": {
                        "variant_id": ref["comparison_variant"],
                        "action": comparison["final_action"],
                        "target_maximum_position": comparison["target_maximum_position"],
                        "reason_codes": list(comparison["reason_codes"]),
                        "decision_digest": comparison["formal_decision_digest"],
                    },
                }
            )
        return result

    def _lifecycle_decision(self, package: Mapping[str, Any]) -> dict[str, Any] | None:
        ref = package["lifecycle_decision"]
        if ref is None:
            return None
        decision_ref = _object(ref, "lifecycle_decision")
        path = (self.repository_root / str(decision_ref["repository_key"])).resolve()
        if self.repository_root not in path.parents:
            raise ProductExpertEvidenceError(
                "expert_evidence_decision_path_invalid", "lifecycle decision escapes repository"
            )
        try:
            artifact = artifact_from_bytes(path.read_bytes())
        except (OSError, ExpertEvaluationError) as exc:
            reason = (
                exc.reason_code
                if isinstance(exc, ExpertEvaluationError)
                else "expert_evidence_decision_unreadable"
            )
            raise ProductExpertEvidenceError(reason, "lifecycle decision differs") from exc
        if (
            not isinstance(artifact, ExpertLifecycleDecision)
            or artifact.digest != decision_ref["digest"]
        ):
            raise ProductExpertEvidenceError(
                "expert_evidence_decision_mismatch", "lifecycle decision identity differs"
            )
        return artifact.to_dict()
