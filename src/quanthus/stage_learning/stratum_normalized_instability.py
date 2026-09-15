"""P3-STAGE-017 artifact-only Era x Board normalized instability development.

The successor keeps the existing 3 x 3 instability profile coordinates but instantiates their
unlabeled quantile thresholds independently inside each frozen Era x Board cell. It builds all cell
bases/profile thresholds before opening the immutable review ledgers, scores only instability, and
never reads DWD or preserved evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_development import (
    load_preregistration as load_p012_preregistration,
)
from quanthus.stage_learning.combined_atomic_scoring import (
    _artifact_cases,
    _artifact_payloads,
    _combined_references,
    _ledger,
    _macro_atomic_metrics,
    _passes_atomic_macro_gate,
    _plain,
    _rate_sort_value,
    _reference_counts,
)
from quanthus.stage_learning.directional_boundary_development import case_features
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as instability_candidate_grid,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_instability,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    threshold_basis as instability_threshold_basis,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-stratum-normalized-instability.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class StratumNormalizedInstabilityError(ValueError):
    """The frozen P017 stratum-normalized instability contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise StratumNormalizedInstabilityError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StratumNormalizedInstabilityError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise StratumNormalizedInstabilityError(f"object_required:{label}")
    return value


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    path = path or root / DEFAULT_CONFIG
    value = _object(path, "p017_config")
    if (
        value.get("schema_version") != "quanthus.stage-stratum-normalized-instability.v1"
        or value.get("work_item") != "P3-STAGE-017-A"
        or value.get("state")
        != "PREREGISTERED_ARTIFACT_ONLY_STRATUM_NORMALIZED_INSTABILITY_BEFORE_SCORING"
    ):
        raise StratumNormalizedInstabilityError("config_identity")
    development = value.get("development_surface")
    normalization = value.get("normalization")
    surface = value.get("candidate_surface")
    envelope = value.get("envelope_successor_disposition")
    quality = value.get("quality_gate")
    selection = value.get("selection")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (
            development,
            normalization,
            surface,
            envelope,
            quality,
            selection,
            runtime,
            authority,
        )
    ):
        raise StratumNormalizedInstabilityError("config_sections")
    if (
        development.get("case_count") != 252
        or development.get("expected_cell_count") != 8
        or development.get("new_identity_discovery") is not False
        or development.get("new_random_sampling") is not False
        or development.get("case_replacement") is not False
        or development.get("reference_rewrite") is not False
        or development.get("rereview") is not False
        or normalization.get("kind") != "cell_local_unlabeled_quantile_basis"
        or normalization.get("label_or_reference_visibility_during_basis_build") is not False
        or normalization.get("candidate_output_visibility_during_basis_build") is not False
        or normalization.get("cross_cell_pooling") is not False
        or normalization.get("cross_cell_fallback") is not False
        or normalization.get("post_result_basis_change") is not False
        or surface.get("candidate_count") != 9
        or surface.get("expansion_profile_count") != 3
        or surface.get("broad_profile_count") != 3
        or surface.get("new_profile_coordinates") is not False
        or surface.get("candidate_expansion") is not False
        or surface.get("joint_pair_search_count") != 0
        or envelope.get("p012_fixed_additive_channel") != "RETIRED_FROM_P017_ELIGIBILITY"
        or envelope.get("post_result_threshold_tuning") is not False
        or envelope.get("new_envelope_candidate_search") is not False
        or quality.get("minimum_predictions") != 18
        or quality.get("minimum_macro_precision") != "0.78"
        or quality.get("minimum_macro_positive_reference_coverage") != "0.70"
        or quality.get("maximum_macro_definite_negative_false_positive_rate") != "0.10"
        or quality.get("numeric_threshold_relaxation") is not False
        or quality.get("envelope_incremental_gate_numeric_replacement") is not False
        or selection.get("freeze_at_most_one") is not True
        or selection.get("post_result_repick") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
        or runtime.get("worker_wall_seconds") != 900
    ):
        raise StratumNormalizedInstabilityError("config_budget")
    required_true = (
        "artifact_only_development_scoring",
        "cell_local_unlabeled_basis_build",
        "instability_candidate_scoring",
    )
    required_false = (
        "dwd_market_read",
        "reference_generation",
        "reference_rewrite",
        "candidate_surface_change",
        "profile_coordinate_change",
        "numeric_gate_relaxation",
        "fixed_relocation_scoring",
        "order_scoring",
        "projection_compatibility",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StratumNormalizedInstabilityError("authority_ceiling")
    return value


def _bound_repo_json(
    root: Path, binding: Mapping[str, Any], *, label: str, digest_key: str = "file_sha256"
) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get(digest_key) or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise StratumNormalizedInstabilityError(f"repo_binding:{label}")
    return _object(path, label)


def _merge_cases(
    p014_payloads: Mapping[str, Any], p015_payloads: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    old_cases, old_strata = _artifact_cases(p014_payloads, expected_count=192)
    new_cases, new_strata = _artifact_cases(p015_payloads, expected_count=60)
    if set(old_cases) & set(new_cases):
        raise StratumNormalizedInstabilityError("case_overlap")
    cases = {**old_cases, **new_cases}
    strata = {**old_strata, **new_strata}
    if len(cases) != 252 or len(strata) != 252 or set(cases) != set(strata):
        raise StratumNormalizedInstabilityError("case_closure")
    return cases, strata


def _feature_row(case: Mapping[str, Any]) -> dict[str, Any]:
    values = case_features(case)
    required = (
        "range_width20",
        "width_change20",
        "efficiency20_abs",
        "range_traversal20",
    )
    if any(values.get(name) is None for name in required):
        raise StratumNormalizedInstabilityError("feature_required_value")
    counter = values.get("counter_direction_excursion_share20")
    return {
        "range_width20": str(values["range_width20"]),
        "width_change20": str(values["width_change20"]),
        "efficiency20_abs": str(values["efficiency20_abs"]),
        "range_traversal20": str(values["range_traversal20"]),
        "counter_direction_excursion_share20": None if counter is None else str(counter),
    }


def _cell_basis_and_grid(
    cases: Mapping[str, Mapping[str, Any]],
    strata: Mapping[str, Mapping[str, str]],
    *,
    minimum_cases_per_cell: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for case_id in sorted(cases):
        grouped[str(strata[case_id]["cell"])].append(case_id)
    if len(grouped) != 8:
        raise StratumNormalizedInstabilityError("cell_count")

    bases: dict[str, Any] = {}
    cell_grids: dict[str, Any] = {}
    profile_keys: tuple[tuple[str, str], ...] | None = None
    for cell, ids in sorted(grouped.items()):
        if len(ids) < minimum_cases_per_cell:
            raise StratumNormalizedInstabilityError(f"cell_case_shortfall:{cell}")
        rows = [_feature_row(cases[case_id]) for case_id in ids]
        try:
            basis = instability_threshold_basis(rows)
            grid = instability_candidate_grid(basis)
        except Exception as exc:
            raise StratumNormalizedInstabilityError(f"cell_basis_or_grid:{cell}:{exc}") from exc
        keys = tuple(
            sorted((item.expansion_profile_id, item.broad_profile_id) for item in grid)
        )
        if len(keys) != 9 or len(set(keys)) != 9:
            raise StratumNormalizedInstabilityError(f"cell_profile_count:{cell}")
        if profile_keys is None:
            profile_keys = keys
        elif keys != profile_keys:
            raise StratumNormalizedInstabilityError("profile_topology_drift")
        bases[cell] = {
            **basis,
            "cell": cell,
            "case_count": len(ids),
            "case_identity_digest": digest(ids),
            "labels_used": False,
            "reference_opinions_used": False,
        }
        cell_grids[cell] = {
            f"{item.expansion_profile_id}|{item.broad_profile_id}": item
            for item in grid
        }
    if profile_keys is None:
        raise StratumNormalizedInstabilityError("profile_topology_missing")

    profile_grid = []
    for expansion_id, broad_id in profile_keys:
        key = f"{expansion_id}|{broad_id}"
        cell_candidates = {
            cell: cell_grids[cell][key].to_dict()
            for cell in sorted(cell_grids)
        }
        profile_id = digest(
            {
                "schema_version": "quanthus.stage-stratum-normalized-instability-profile.v1",
                "expansion_profile_id": expansion_id,
                "broad_profile_id": broad_id,
                "cell_candidates": cell_candidates,
            }
        )
        profile_grid.append(
            {
                "profile_id": profile_id,
                "expansion_profile_id": expansion_id,
                "broad_profile_id": broad_id,
                "cell_candidates": cell_candidates,
            }
        )
    return bases, {
        "schema_version": "quanthus.stage-stratum-normalized-instability-profile-grid.v1",
        "candidate_count": len(profile_grid),
        "profile_coordinate_source": "exact_P012_instability_3x3",
        "normalization_axis": "era_board_cell",
        "labels_used": False,
        "reference_opinions_used": False,
        "envelope_additive_channel_included": False,
        "profiles": profile_grid,
        "profile_grid_digest": digest(profile_grid),
    }


def _score_profiles(
    *,
    cases: Mapping[str, Mapping[str, Any]],
    strata: Mapping[str, Mapping[str, str]],
    references: Mapping[str, Mapping[str, Any]],
    profile_grid: Mapping[str, Any],
    bases: Mapping[str, Any],
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    case_ids = sorted(cases)
    profiles = profile_grid.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 9:
        raise StratumNormalizedInstabilityError("profile_grid_shape")
    trials = []
    for raw_profile in profiles:
        if not isinstance(raw_profile, Mapping):
            raise StratumNormalizedInstabilityError("profile_shape")
        expansion_id = str(raw_profile.get("expansion_profile_id") or "")
        broad_id = str(raw_profile.get("broad_profile_id") or "")
        cell_candidates: dict[str, Any] = {}
        for cell in sorted(bases):
            candidates = instability_candidate_grid(bases[cell])
            matched = [
                item
                for item in candidates
                if item.expansion_profile_id == expansion_id
                and item.broad_profile_id == broad_id
            ]
            if len(matched) != 1:
                raise StratumNormalizedInstabilityError("profile_candidate_binding")
            candidate = matched[0]
            if candidate.to_dict() != raw_profile["cell_candidates"][cell]:
                raise StratumNormalizedInstabilityError("profile_candidate_threshold_binding")
            cell_candidates[cell] = candidate
        predictions = {
            case_id: candidate_instability(
                cases[case_id], cell_candidates[str(strata[case_id]["cell"])]
            )
            for case_id in case_ids
        }
        metrics = _macro_atomic_metrics(
            case_ids=case_ids,
            references=references,
            predictions=predictions,
            proposition="structural_instability_20",
            strata=strata,
        )
        eligible = _passes_atomic_macro_gate(
            metrics=metrics,
            proposition="structural_instability_20",
            prereg=prereg,
        )
        trials.append(
            {
                "profile_id": str(raw_profile["profile_id"]),
                "expansion_profile_id": expansion_id,
                "broad_profile_id": broad_id,
                "metrics": metrics,
                "eligible": bool(eligible),
                "predictions": predictions,
            }
        )
    eligible = [item for item in trials if item["eligible"]]
    if not eligible:
        return {
            "disposition": "REVISE_OR_PARK_STRATUM_NORMALIZED_INSTABILITY_FAIL",
            "trials": trials,
            "selected": None,
            "selected_predictions": None,
        }
    selected = min(
        eligible,
        key=lambda item: (
            _rate_sort_value(item["metrics"]["macro"]["precision"], descending=True),
            _rate_sort_value(
                item["metrics"]["macro"]["definite_negative_false_positive_rate"],
                descending=False,
            ),
            _rate_sort_value(
                item["metrics"]["macro"]["positive_reference_coverage"], descending=True
            ),
            _rate_sort_value(item["metrics"]["pooled"]["precision"], descending=True),
            _rate_sort_value(
                item["metrics"]["pooled"]["definite_negative_false_positive_rate"],
                descending=False,
            ),
            _rate_sort_value(
                item["metrics"]["pooled"]["positive_reference_coverage"], descending=True
            ),
            item["profile_id"],
        ),
    )
    selected_predictions = {
        case_id: {
            "structural_instability_20": selected["predictions"][case_id],
            "era": strata[case_id]["era"],
            "board_family": strata[case_id]["board_family"],
            "cell": strata[case_id]["cell"],
        }
        for case_id in case_ids
    }
    return {
        "disposition": "PASS_STRATUM_NORMALIZED_INSTABILITY_FREEZE",
        "trials": trials,
        "selected": selected,
        "selected_predictions": selected_predictions,
    }


def _trial_public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": item["profile_id"],
        "expansion_profile_id": item["expansion_profile_id"],
        "broad_profile_id": item["broad_profile_id"],
        "metrics": _plain(item["metrics"]),
        "eligible": bool(item["eligible"]),
    }


def run_scoring(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StratumNormalizedInstabilityError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    p016_result = _bound_repo_json(
        root, config["bindings"]["p016_result"], label="p016_result"
    )
    _bound_repo_json(
        root, config["bindings"]["p016_scoring_contract"], label="p016_scoring_contract"
    )
    p015_result = _bound_repo_json(
        root,
        config["bindings"]["p015_reference_sufficiency_result"],
        label="p015_reference_sufficiency_result",
    )
    if (
        p016_result.get("state") != "CLOSED_INSTABILITY_DEVELOPMENT_FAIL"
        or p016_result.get("successor", {}).get("work_item") != "P3-STAGE-017"
        or p015_result.get("state") != "PASS_COMPLETE_RESERVE_REFERENCE_SUFFICIENCY"
    ):
        raise StratumNormalizedInstabilityError("upstream_disposition")

    prereg = load_p012_preregistration(root)
    if digest(prereg) != config["bindings"]["p012_preregistration"]["canonical_digest"]:
        raise StratumNormalizedInstabilityError("p012_preregistration_binding")

    training_root = training_root.expanduser().resolve()
    _, _, p014_payloads = _artifact_payloads(
        training_root=training_root,
        binding=config["bindings"]["p014_semantic_artifact"],
        required=("evidence.json", "case-strata.json"),
        label="p014_semantic",
    )
    _, _, p015_payloads = _artifact_payloads(
        training_root=training_root,
        binding=config["bindings"]["p015_additional_review_artifact"],
        required=("evidence.json", "case-strata.json"),
        label="p015_additional_review",
    )
    cases, strata = _merge_cases(p014_payloads, p015_payloads)

    # The unlabeled cell-local bases and profile thresholds are fully built before opening ledgers.
    bases, profile_grid = _cell_basis_and_grid(
        cases,
        strata,
        minimum_cases_per_cell=int(config["normalization"]["minimum_cases_per_cell"]),
    )

    p014_ledger = _ledger(
        training_root,
        config["bindings"]["p014_review_ledger"],
        expected_count=192,
        label="p014_review_ledger",
    )
    p015_ledger = _ledger(
        training_root,
        config["bindings"]["p015_review_ledger"],
        expected_count=60,
        label="p015_review_ledger",
    )
    references = _combined_references(p014_ledger, p015_ledger)
    if set(references) != set(cases):
        raise StratumNormalizedInstabilityError("reference_case_closure")
    reference_counts = _reference_counts(references)
    expected_counts = p015_result.get("combined_reference_resolution")
    if not isinstance(expected_counts, Mapping):
        raise StratumNormalizedInstabilityError("reference_result_shape")
    for proposition in (
        "structural_instability_20",
        "coherent_directional_relocation_20",
        "orderly_consolidation_20",
    ):
        if reference_counts["atomic"][proposition] != expected_counts.get(proposition):
            raise StratumNormalizedInstabilityError("reference_count_binding")

    result = _score_profiles(
        cases=cases,
        strata=strata,
        references=references,
        profile_grid=profile_grid,
        bases=bases,
        prereg=prereg,
    )
    public_trials = [_trial_public(item) for item in result["trials"]]
    score = {
        "schema_version": config["output"]["score_schema"],
        "work_item": "P3-STAGE-017-B",
        "disposition": result["disposition"],
        "reference_case_count": 252,
        "reference_counts": reference_counts,
        "normalization_axis": "era_board_cell",
        "cell_count": 8,
        "candidate_count": 9,
        "trials": public_trials,
        "eligible_candidate_count": sum(item["eligible"] for item in public_trials),
        "selected_profile": (
            None
            if result["selected"] is None
            else {
                "profile_id": result["selected"]["profile_id"],
                "expansion_profile_id": result["selected"]["expansion_profile_id"],
                "broad_profile_id": result["selected"]["broad_profile_id"],
                "metrics": _plain(result["selected"]["metrics"]),
            }
        ),
        "profile_grid_digest": profile_grid["profile_grid_digest"],
        "stratum_basis_digest": digest(bases),
        "envelope_additive_channel_used_for_eligibility": False,
        "envelope_incremental_gate_applied": False,
        "envelope_incremental_gate_disposition": (
            "NOT_APPLICABLE_MECHANISM_RETIRED_NOT_NUMERICALLY_RELAXED"
        ),
        "numeric_quality_thresholds": "exact_P012_absolute_instability_gates",
        "aggregate_contract": "exact_P016_pooled_count_plus_macro_era_board_rate",
        "dwd_market_read": False,
        "reference_rewrite": False,
        "preserved_2021_read": False,
        "2025_read": False,
    }
    predictions = {
        "schema_version": config["output"]["prediction_schema"],
        "available": result["selected_predictions"] is not None,
        "selected_profile_id": (
            None if result["selected"] is None else result["selected"]["profile_id"]
        ),
        "case_count": 0 if result["selected_predictions"] is None else 252,
        "predictions": (
            {} if result["selected_predictions"] is None else result["selected_predictions"]
        ),
        "projection_performed": False,
        "post_score_repick": False,
    }
    basis_payload = {
        "schema_version": config["output"]["basis_schema"],
        "normalization_axis": "era_board_cell",
        "cell_count": len(bases),
        "labels_used": False,
        "reference_opinions_used": False,
        "built_before_reference_ledger_read": True,
        "cells": bases,
        "basis_digest": digest(bases),
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-017-B",
        "phase": "STRATUM_NORMALIZED_INSTABILITY_DEVELOPMENT_SCORING",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "disposition": result["disposition"],
        "reference_case_count": 252,
        "cell_count": 8,
        "candidate_count": 9,
        "eligible_candidate_count": score["eligible_candidate_count"],
        "selected_profile_id": predictions["selected_profile_id"],
        "prediction_artifact_available": predictions["available"],
        "basis_built_before_reference_ledger_read": True,
        "envelope_additive_channel_used_for_eligibility": False,
        "fixed_relocation_scored": False,
        "order_scored": False,
        "projection_performed": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }

    run_id = "stage-stratum-normalized-instability-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "stratum-bases.json": canonical(_plain(basis_payload)),
        "profile-grid.json": canonical(_plain(profile_grid)),
        "instability-score.json": canonical(_plain(score)),
        "selected-predictions.json": canonical(_plain(predictions)),
        "reference-binding.json": canonical(
            {
                "p014_review_ledger_sha256": config["bindings"]["p014_review_ledger"]["sha256"],
                "p015_review_ledger_sha256": config["bindings"]["p015_review_ledger"]["sha256"],
                "combined_case_count": 252,
                "reference_counts": reference_counts,
                "reference_rewrite": False,
            }
        ),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "dwd_market_read": False,
            "reference_rewrite": False,
            "envelope_additive_eligibility": False,
            "fixed_relocation_scoring": False,
            "order_scoring": False,
            "projection_performed": False,
            "preserved_2021_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    manifest_sha = _sha(manifest_raw)
    (run_root / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": manifest_sha}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict):
        raise StratumNormalizedInstabilityError("artifact_manifest_shape")
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if _sha(manifest_raw) != ready:
        raise StratumNormalizedInstabilityError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-stratum-normalized-instability-artifact.v1"
    ):
        raise StratumNormalizedInstabilityError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StratumNormalizedInstabilityError("artifact_code_revision")
    expected = {
        "stratum-bases.json",
        "profile-grid.json",
        "instability-score.json",
        "selected-predictions.json",
        "reference-binding.json",
        "summary.json",
    }
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected:
        raise StratumNormalizedInstabilityError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if files.get(name) != _sha(raw):
            raise StratumNormalizedInstabilityError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise StratumNormalizedInstabilityError(f"artifact_payload_shape:{name}")
        payloads[name] = value
    bases = payloads["stratum-bases.json"]
    grid = payloads["profile-grid.json"]
    score = payloads["instability-score.json"]
    predictions = payloads["selected-predictions.json"]
    reference = payloads["reference-binding.json"]
    summary = payloads["summary.json"]
    if (
        bases.get("cell_count") != 8
        or len(bases.get("cells", {})) != 8
        or bases.get("labels_used") is not False
        or bases.get("reference_opinions_used") is not False
        or bases.get("built_before_reference_ledger_read") is not True
        or grid.get("candidate_count") != 9
        or len(grid.get("profiles", [])) != 9
        or grid.get("labels_used") is not False
        or grid.get("reference_opinions_used") is not False
        or grid.get("envelope_additive_channel_included") is not False
        or score.get("reference_case_count") != 252
        or score.get("candidate_count") != 9
        or len(score.get("trials", [])) != 9
        or score.get("envelope_additive_channel_used_for_eligibility") is not False
        or score.get("envelope_incremental_gate_applied") is not False
        or score.get("dwd_market_read") is not False
        or score.get("reference_rewrite") is not False
        or score.get("preserved_2021_read") is not False
        or score.get("2025_read") is not False
        or reference.get("combined_case_count") != 252
        or reference.get("reference_rewrite") is not False
        or summary.get("reference_case_count") != 252
        or summary.get("cell_count") != 8
        or summary.get("candidate_count") != 9
        or summary.get("basis_built_before_reference_ledger_read") is not True
        or summary.get("envelope_additive_channel_used_for_eligibility") is not False
        or summary.get("fixed_relocation_scored") is not False
        or summary.get("order_scored") is not False
        or summary.get("projection_performed") is not False
        or summary.get("dwd_market_read") is not False
        or summary.get("preserved_2021_read") is not False
        or summary.get("2022_2024_read") is not False
        or summary.get("2025_read") is not False
    ):
        raise StratumNormalizedInstabilityError("artifact_authority")
    if bases.get("basis_digest") != digest(bases.get("cells")):
        raise StratumNormalizedInstabilityError("artifact_basis_digest")
    if grid.get("profile_grid_digest") != digest(grid.get("profiles")):
        raise StratumNormalizedInstabilityError("artifact_profile_grid_digest")
    available = predictions.get("available") is True
    if available:
        if (
            predictions.get("case_count") != 252
            or len(predictions.get("predictions", {})) != 252
            or score.get("selected_profile") is None
            or score.get("eligible_candidate_count", 0) < 1
            or summary.get("prediction_artifact_available") is not True
        ):
            raise StratumNormalizedInstabilityError("artifact_pass_shape")
    else:
        if (
            predictions.get("case_count") != 0
            or predictions.get("predictions") != {}
            or score.get("selected_profile") is not None
            or score.get("eligible_candidate_count") != 0
            or summary.get("prediction_artifact_available") is not False
        ):
            raise StratumNormalizedInstabilityError("artifact_fail_shape")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": score.get("disposition"),
        "reference_case_count": 252,
        "cell_count": 8,
        "candidate_count": 9,
        "eligible_candidate_count": score.get("eligible_candidate_count"),
        "selected_profile_id": predictions.get("selected_profile_id"),
        "prediction_artifact_available": available,
        "basis_digest": bases.get("basis_digest"),
        "profile_grid_digest": grid.get("profile_grid_digest"),
        "dwd_market_read": False,
        "fixed_relocation_scored": False,
        "order_scored": False,
        "projection_performed": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--training-root", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_scoring(
            config_path=Path(args.config),
            training_root=Path(args.training_root),
            output_root=Path(args.output_root),
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(Path(args.artifact), code_revision=args.code_revision)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
