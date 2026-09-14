"""P3-STAGE-016 artifact-only independent atomic scoring.

This runner opens the preregistered P014 threshold/candidate artifacts only after P016-A is frozen.
It scores the immutable P015 252-case blind-reference set with pooled count gates and the P014
Era x Board equal-weight macro rate contract. It freezes I/R/O predictions independently and does
not perform Stage projection or any preserved-evaluation read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_development import (
    PROPOSITIONS,
    _atomic_metrics,
    instability_candidate_grid,
    instability_truth,
    order_truth,
    relocation_truth,
    stage2_candidate_grid,
)
from quanthus.stage_learning.atomic_proposition_development import (
    load_execution as load_p012_execution,
)
from quanthus.stage_learning.atomic_proposition_development import (
    load_preregistration as load_p012_preregistration,
)
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.structure_directional_path_envelope import assess_close_path_envelope
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-combined-atomic-scoring.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class CombinedAtomicScoringError(ValueError):
    """The frozen P016 artifact-only scoring contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise CombinedAtomicScoringError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CombinedAtomicScoringError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise CombinedAtomicScoringError(f"object_required:{label}")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise CombinedAtomicScoringError("macro_denominator_empty")
    return sum(values, Decimal(0)) / Decimal(len(values))


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    path = path or root / DEFAULT_CONFIG
    value = _object(path, "p016_scoring_config")
    if (
        value.get("schema_version") != "quanthus.stage-combined-atomic-scoring.v1"
        or value.get("work_item") != "P3-STAGE-016-A"
        or value.get("state")
        != "PREREGISTERED_ARTIFACT_ONLY_ATOMIC_SCORING_BEFORE_CANDIDATE_ACCESS"
    ):
        raise CombinedAtomicScoringError("config_identity")
    surface = value.get("candidate_surface")
    aggregate = value.get("aggregate_contract")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(isinstance(item, Mapping) for item in (surface, aggregate, runtime, authority)):
        raise CombinedAtomicScoringError("config_sections")
    if (
        surface.get("instability_candidate_count") != 9
        or surface.get("relocation_candidate_count") != 1
        or surface.get("order_profile_count") != 3
        or surface.get("joint_pair_search_count") != 0
        or surface.get("candidate_expansion") is not False
        or surface.get("threshold_recompute") is not False
        or surface.get("threshold_retune") is not False
        or surface.get("post_score_repick") is not False
        or aggregate.get("cell_count") != 8
        or aggregate.get("count_gates") != "pooled_counts_exact_P012"
        or aggregate.get("rate_gates")
        != "macro_cell_equal_weight_same_numeric_thresholds_as_P012"
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise CombinedAtomicScoringError("config_budget")
    required_false = (
        "projection_compatibility",
        "candidate_reselection_after_projection",
        "dwd_market_read",
        "reference_generation",
        "reference_rewrite",
        "candidate_surface_change",
        "numeric_gate_change",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if any(authority.get(key) is not False for key in required_false):
        raise CombinedAtomicScoringError("authority_ceiling")
    return value


def _bound_repo_json(
    root: Path, binding: Mapping[str, Any], *, label: str, digest_key: str = "file_sha256"
) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get(digest_key) or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise CombinedAtomicScoringError(f"repo_binding:{label}")
    return _object(path, label)


def _artifact_payloads(
    *,
    training_root: Path,
    binding: Mapping[str, Any],
    required: Sequence[str],
    label: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    artifact = (training_root / str(binding.get("artifact_relative_path") or "")).resolve()
    manifest_raw = (artifact / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict):
        raise CombinedAtomicScoringError(f"artifact_manifest_shape:{label}")
    ready = (artifact / "READY").read_text(encoding="ascii").strip()
    expected_manifest = str(binding.get("manifest_sha256") or "")
    if _sha(manifest_raw) != ready or ready != expected_manifest:
        raise CombinedAtomicScoringError(f"artifact_manifest_binding:{label}")
    if manifest.get("code_revision") != binding.get("code_revision"):
        raise CombinedAtomicScoringError(f"artifact_revision_binding:{label}")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise CombinedAtomicScoringError(f"artifact_files:{label}")
    payloads: dict[str, Any] = {}
    for name in required:
        raw = (artifact / name).read_bytes()
        if files.get(name) != _sha(raw):
            raise CombinedAtomicScoringError(f"artifact_file_binding:{label}:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CombinedAtomicScoringError(f"artifact_payload_shape:{label}:{name}")
        payloads[name] = value
    return artifact, manifest, payloads


def _history_case(case: Mapping[str, Any], source_rows: Mapping[str, Any]) -> dict[str, Any]:
    refs = case.get("history_refs")
    if not isinstance(refs, list) or len(refs) < 21:
        raise CombinedAtomicScoringError("case_history_refs")
    history = []
    for ref in refs[-21:]:
        row = source_rows.get(str(ref))
        if not isinstance(row, Mapping) or digest(dict(row)) != ref:
            raise CombinedAtomicScoringError("case_history_binding")
        if str(row.get("market_date")) > str(case.get("market_date")):
            raise CombinedAtomicScoringError("case_future_row")
        history.append(
            [
                ref,
                row.get("market_date"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("coordinate_epoch"),
                row.get("reset_reason"),
            ]
        )
    return {
        "case_id": str(case.get("case_id") or ""),
        "security_code": str(case.get("security_code") or ""),
        "security_id": str(case.get("security_id") or ""),
        "market_date": str(case.get("market_date") or ""),
        "primary_available": case.get("primary_available") is True,
        "primary": dict(case.get("primary") or {}),
        "history": history,
    }


def _artifact_cases(
    payloads: Mapping[str, Any], *, expected_count: int
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    evidence = payloads["evidence.json"]
    source_rows = evidence.get("source_rows")
    cases = evidence.get("cases")
    strata = payloads["case-strata.json"]
    if (
        not isinstance(source_rows, Mapping)
        or not isinstance(cases, list)
        or not isinstance(strata, Mapping)
    ):
        raise CombinedAtomicScoringError("artifact_case_shape")
    if len(cases) != expected_count or len(strata) != expected_count:
        raise CombinedAtomicScoringError("artifact_case_count")
    output: dict[str, dict[str, Any]] = {}
    normalized_strata: dict[str, dict[str, str]] = {}
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise CombinedAtomicScoringError("artifact_case_row")
        case = _history_case(raw, source_rows)
        case_id = case["case_id"]
        stratum = strata.get(case_id)
        if not case_id or case_id in output or not isinstance(stratum, Mapping):
            raise CombinedAtomicScoringError("artifact_case_identity")
        cell = str(stratum.get("cell") or "")
        era = str(stratum.get("era") or "")
        board = str(stratum.get("board_family") or "")
        if cell != f"{era}/{board}":
            raise CombinedAtomicScoringError("artifact_case_stratum")
        output[case_id] = case
        normalized_strata[case_id] = {
            "era": era,
            "board_family": board,
            "cell": cell,
            "window_id": str(stratum.get("window_id") or ""),
        }
    return output, normalized_strata


def _ledger(
    training_root: Path, binding: Mapping[str, Any], *, expected_count: int, label: str
) -> dict[str, Any]:
    path = (training_root / str(binding.get("relative_path") or "")).resolve()
    expected = str(binding.get("sha256") or "")
    if _file_sha(path) != expected:
        raise CombinedAtomicScoringError(f"ledger_sha:{label}")
    ledger = _object(path, label)
    opinions = ledger.get("opinions")
    if not isinstance(opinions, list) or len(opinions) != expected_count:
        raise CombinedAtomicScoringError(f"ledger_count:{label}")
    return ledger


def _resolved_reference(row: Mapping[str, Any]) -> dict[str, str]:
    short = row.get("short_term")
    fund = row.get("fund")
    if not isinstance(short, Mapping) or not isinstance(fund, Mapping):
        raise CombinedAtomicScoringError("reference_roles")
    reference: dict[str, str] = {}
    for proposition in PROPOSITIONS:
        left = str(short.get(proposition) or "")
        right = str(fund.get(proposition) or "")
        if left not in {"TRUE", "FALSE", "UNKNOWN"} or right not in {
            "TRUE",
            "FALSE",
            "UNKNOWN",
        }:
            raise CombinedAtomicScoringError("reference_truth")
        reference[proposition] = (
            left if left == right and left in {"TRUE", "FALSE"} else "AMBIGUOUS"
        )
    short_projection = project_atomic_primary(short, available=True)["status"]
    fund_projection = project_atomic_primary(fund, available=True)["status"]
    reference["projected_primary_reference"] = (
        short_projection if short_projection == fund_projection else "AMBIGUOUS"
    )
    return reference


def _combined_references(
    old_ledger: Mapping[str, Any], new_ledger: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for ledger in (old_ledger, new_ledger):
        for row in ledger["opinions"]:
            if not isinstance(row, Mapping):
                raise CombinedAtomicScoringError("ledger_opinion_shape")
            case_id = str(row.get("case_id") or "")
            if not case_id or case_id in output:
                raise CombinedAtomicScoringError("ledger_case_identity")
            output[case_id] = _resolved_reference(row)
    if len(output) != 252:
        raise CombinedAtomicScoringError("combined_reference_count")
    return output


def _reference_counts(references: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    atomic = {name: Counter() for name in PROPOSITIONS}
    projected = Counter()
    for reference in references.values():
        for proposition in PROPOSITIONS:
            atomic[proposition][reference[proposition]] += 1
        projected[reference["projected_primary_reference"]] += 1
    return {
        "atomic": {name: dict(sorted(counts.items())) for name, counts in atomic.items()},
        "projected": dict(sorted(projected.items())),
    }


def _envelope_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    history = case.get("history")
    if not isinstance(history, list) or len(history) != 21:
        raise CombinedAtomicScoringError("envelope_history")
    assessment = assess_close_path_envelope([row[4] for row in history]).to_dict()
    body = {
        "status": assessment["status"],
        "reason": assessment["reason"],
        "profile": assessment["profile"],
        "events": assessment["events"],
        "descriptors": assessment["descriptors"],
    }
    return {**body, "value_digest": digest(body)}


def _macro_atomic_metrics(
    *,
    case_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, str],
    proposition: str,
    strata: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    pooled = _atomic_metrics(case_ids, references, predictions, proposition)
    by_cell: dict[str, list[str]] = defaultdict(list)
    for case_id in case_ids:
        by_cell[str(strata[case_id]["cell"])].append(case_id)
    if len(by_cell) != 8:
        raise CombinedAtomicScoringError("macro_cell_count")
    cells = {
        cell: _atomic_metrics(ids, references, predictions, proposition)
        for cell, ids in sorted(by_cell.items())
    }
    precisions: list[Decimal] = []
    coverages: list[Decimal] = []
    fprs: list[Decimal] = []
    for metrics in cells.values():
        precision = metrics["precision"]
        precisions.append(Decimal(0) if precision is None else precision)
        if metrics["definite_positive_references"] > 0:
            coverage = metrics["positive_reference_coverage"]
            if coverage is None:
                raise CombinedAtomicScoringError("macro_coverage_missing")
            coverages.append(coverage)
        if metrics["definite_negative_references"] > 0:
            fpr = metrics["definite_negative_false_positive_rate"]
            if fpr is None:
                raise CombinedAtomicScoringError("macro_fpr_missing")
            fprs.append(fpr)
    macro = {
        "precision": _mean(precisions),
        "positive_reference_coverage": _mean(coverages),
        "definite_negative_false_positive_rate": _mean(fprs),
        "precision_cell_count": len(precisions),
        "coverage_cell_count": len(coverages),
        "fpr_cell_count": len(fprs),
    }
    return {"pooled": pooled, "macro": macro, "per_cell": cells}


def _group_diagnostics(
    *,
    case_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, str],
    proposition: str,
    strata: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for field in ("era", "board_family"):
        groups: dict[str, list[str]] = defaultdict(list)
        for case_id in case_ids:
            groups[str(strata[case_id][field])].append(case_id)
        output[field] = {
            key: _atomic_metrics(ids, references, predictions, proposition)
            for key, ids in sorted(groups.items())
        }
    return output


def _passes_atomic_macro_gate(
    *, metrics: Mapping[str, Any], proposition: str, prereg: Mapping[str, Any]
) -> bool:
    quality = prereg["atomic_quality_gates"][
        {
            "structural_instability_20": "instability",
            "coherent_directional_relocation_20": "relocation",
            "orderly_consolidation_20": "order",
        }[proposition]
    ]
    sufficiency = prereg["development_sufficiency"]
    prefix = {
        "structural_instability_20": "instability",
        "coherent_directional_relocation_20": "relocation",
        "orderly_consolidation_20": "order",
    }[proposition]
    pooled = metrics["pooled"]
    macro = metrics["macro"]
    return bool(
        pooled["complete_reference_count"] >= sufficiency["minimum_complete_cases"]
        and pooled["definite_positive_references"]
        >= sufficiency[f"minimum_definite_{prefix}_positive_references"]
        and pooled["definite_negative_references"]
        >= sufficiency[f"minimum_definite_{prefix}_negative_references"]
        and pooled["predictions"] >= quality["minimum_predictions"]
        and macro["precision"] >= Decimal(str(quality["minimum_precision"]))
        and macro["positive_reference_coverage"]
        >= Decimal(str(quality["minimum_positive_reference_coverage"]))
        and macro["definite_negative_false_positive_rate"]
        <= Decimal(str(quality["maximum_definite_negative_false_positive_rate"]))
    )


def _reference_sufficient(
    references: Mapping[str, Mapping[str, Any]], case_ids: Sequence[str], prereg: Mapping[str, Any]
) -> bool:
    dummy = {case_id: "FALSE" for case_id in case_ids}
    sufficiency = prereg["development_sufficiency"]
    for proposition in PROPOSITIONS:
        metrics = _atomic_metrics(case_ids, references, dummy, proposition)
        prefix = {
            "structural_instability_20": "instability",
            "coherent_directional_relocation_20": "relocation",
            "orderly_consolidation_20": "order",
        }[proposition]
        if (
            metrics["complete_reference_count"] < sufficiency["minimum_complete_cases"]
            or metrics["definite_positive_references"]
            < sufficiency[f"minimum_definite_{prefix}_positive_references"]
            or metrics["definite_negative_references"]
            < sufficiency[f"minimum_definite_{prefix}_negative_references"]
        ):
            return False
    return True


def _rate_sort_value(value: Any, *, descending: bool) -> Decimal:
    observed = value
    if observed is None:
        observed = Decimal(0)
    if not isinstance(observed, Decimal):
        observed = Decimal(str(observed))
    return -observed if descending else observed


def score_atomic(
    *,
    cases: Mapping[str, Mapping[str, Any]],
    references: Mapping[str, Mapping[str, Any]],
    strata: Mapping[str, Mapping[str, str]],
    basis: Mapping[str, Any],
    candidate_grid: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    case_ids = sorted(cases)
    if (
        set(case_ids) != set(references)
        or set(case_ids) != set(strata)
        or set(case_ids) != set(envelopes)
    ):
        raise CombinedAtomicScoringError("scoring_identity_closure")
    if not _reference_sufficient(references, case_ids, prereg):
        return {
            "disposition": prereg["no_pass_disposition"]["atomic_sufficiency_fail"],
            "atomic_freeze": None,
            "frozen_predictions": None,
        }

    instability_candidates = tuple(instability_candidate_grid(basis))
    expected_i = [item.to_dict() for item in instability_candidates]
    if candidate_grid.get("instability_candidates") != expected_i:
        raise CombinedAtomicScoringError("instability_candidate_grid_binding")
    order_candidates = tuple(stage2_candidate_grid(basis))
    expected_o = [item.to_dict() for item in order_candidates]
    if candidate_grid.get("order_candidates") != expected_o:
        raise CombinedAtomicScoringError("order_candidate_grid_binding")

    i_trials = []
    for candidate in instability_candidates:
        observed = {
            case_id: instability_truth(cases[case_id], candidate, envelopes[case_id])
            for case_id in case_ids
        }
        base_predictions = {case_id: item["base"] for case_id, item in observed.items()}
        enhanced_predictions = {case_id: item["enhanced"] for case_id, item in observed.items()}
        base_metrics = _macro_atomic_metrics(
            case_ids=case_ids,
            references=references,
            predictions=base_predictions,
            proposition="structural_instability_20",
            strata=strata,
        )
        metrics = _macro_atomic_metrics(
            case_ids=case_ids,
            references=references,
            predictions=enhanced_predictions,
            proposition="structural_instability_20",
            strata=strata,
        )
        incremental_tp = (
            metrics["pooled"]["true_positive"] - base_metrics["pooled"]["true_positive"]
        )
        incremental_fp = (
            metrics["pooled"]["false_positive"] - base_metrics["pooled"]["false_positive"]
        )
        quality = prereg["atomic_quality_gates"]["instability"]
        eligible = bool(
            _passes_atomic_macro_gate(
                metrics=metrics,
                proposition="structural_instability_20",
                prereg=prereg,
            )
            and incremental_tp
            >= int(quality["minimum_incremental_true_positives_vs_same_profile_base"])
            and (
                incremental_tp > incremental_fp
                if quality["incremental_true_positives_must_exceed_incremental_false_positives"]
                else True
            )
        )
        i_trials.append(
            {
                "candidate": candidate,
                "base_metrics": base_metrics,
                "metrics": metrics,
                "incremental_true_positive": incremental_tp,
                "incremental_false_positive": incremental_fp,
                "eligible": eligible,
                "predictions": enhanced_predictions,
            }
        )
    eligible_i = [item for item in i_trials if item["eligible"]]
    if not eligible_i:
        return {
            "disposition": prereg["no_pass_disposition"]["no_eligible_instability_candidate"],
            "instability_trials": i_trials,
            "atomic_freeze": None,
            "frozen_predictions": None,
        }
    selected_i = min(
        eligible_i,
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
            -int(item["incremental_true_positive"]),
            item["candidate"].candidate_id,
        ),
    )

    relocation_predictions = {case_id: relocation_truth(cases[case_id]) for case_id in case_ids}
    relocation_metrics = _macro_atomic_metrics(
        case_ids=case_ids,
        references=references,
        predictions=relocation_predictions,
        proposition="coherent_directional_relocation_20",
        strata=strata,
    )
    if not _passes_atomic_macro_gate(
        metrics=relocation_metrics,
        proposition="coherent_directional_relocation_20",
        prereg=prereg,
    ):
        return {
            "disposition": prereg["no_pass_disposition"]["fixed_relocation_gate_fail"],
            "instability_trials": i_trials,
            "selected_instability": selected_i,
            "relocation_metrics": relocation_metrics,
            "atomic_freeze": None,
            "frozen_predictions": None,
        }

    o_trials = []
    for candidate in order_candidates:
        predictions = {case_id: order_truth(cases[case_id], candidate) for case_id in case_ids}
        metrics = _macro_atomic_metrics(
            case_ids=case_ids,
            references=references,
            predictions=predictions,
            proposition="orderly_consolidation_20",
            strata=strata,
        )
        eligible = _passes_atomic_macro_gate(
            metrics=metrics,
            proposition="orderly_consolidation_20",
            prereg=prereg,
        )
        o_trials.append(
            {
                "candidate": candidate,
                "metrics": metrics,
                "eligible": eligible,
                "predictions": predictions,
            }
        )
    eligible_o = [item for item in o_trials if item["eligible"]]
    if not eligible_o:
        return {
            "disposition": prereg["no_pass_disposition"]["no_eligible_order_profile"],
            "instability_trials": i_trials,
            "selected_instability": selected_i,
            "relocation_metrics": relocation_metrics,
            "order_trials": o_trials,
            "atomic_freeze": None,
            "frozen_predictions": None,
        }
    selected_o = min(
        eligible_o,
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
            item["candidate"].order_profile_id,
        ),
    )

    freeze = {
        "schema_version": "quanthus.stage-combined-atomic-freeze.v1",
        "instability_candidate": selected_i["candidate"].to_dict(),
        "fixed_envelope_channel": prereg["instability_candidate_surface"]["fixed_additive_channel"],
        "relocation": {
            "source": prereg["relocation_surface"]["source"],
            "threshold_retune": False,
        },
        "order_candidate": selected_o["candidate"].to_dict(),
        "resolver": prereg["projection_compatibility_gate"]["resolver"],
        "aggregate_contract": "pooled_count_plus_macro_era_board_rate_v1",
    }
    frozen_predictions = {
        case_id: {
            "structural_instability_20": selected_i["predictions"][case_id],
            "coherent_directional_relocation_20": relocation_predictions[case_id],
            "orderly_consolidation_20": selected_o["predictions"][case_id],
            "era": strata[case_id]["era"],
            "board_family": strata[case_id]["board_family"],
            "cell": strata[case_id]["cell"],
        }
        for case_id in case_ids
    }
    return {
        "disposition": "PASS_INDEPENDENT_ATOMIC_FREEZE_FOR_PROJECTION",
        "instability_trials": i_trials,
        "selected_instability": selected_i,
        "relocation_metrics": relocation_metrics,
        "relocation_diagnostics": _group_diagnostics(
            case_ids=case_ids,
            references=references,
            predictions=relocation_predictions,
            proposition="coherent_directional_relocation_20",
            strata=strata,
        ),
        "order_trials": o_trials,
        "selected_order": selected_o,
        "atomic_freeze": freeze,
        "atomic_freeze_digest": digest(freeze),
        "frozen_predictions": frozen_predictions,
    }


def _trial_public(item: Mapping[str, Any], *, instability: bool) -> dict[str, Any]:
    candidate = item["candidate"]
    value = {
        "candidate": candidate.to_dict(),
        "metrics": _plain(item["metrics"]),
        "eligible": bool(item["eligible"]),
    }
    if instability:
        value.update(
            {
                "base_metrics": _plain(item["base_metrics"]),
                "incremental_true_positive": int(item["incremental_true_positive"]),
                "incremental_false_positive": int(item["incremental_false_positive"]),
            }
        )
    return value


def run_scoring(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise CombinedAtomicScoringError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    p015_result = _bound_repo_json(
        root, config["bindings"]["p015_reference_sufficiency_result"], label="p015_result"
    )
    p014_closeout = _bound_repo_json(
        root, config["bindings"]["p014_closeout"], label="p014_closeout"
    )
    p014_prereg = _bound_repo_json(
        root, config["bindings"]["p014_preregistration"], label="p014_prereg"
    )
    p012_prereg = load_p012_preregistration(root)
    p012_execution = load_p012_execution(root)
    if (
        digest(p012_prereg) != config["bindings"]["p012_preregistration"]["canonical_digest"]
        or digest(p012_execution) != config["bindings"]["p012_execution"]["canonical_digest"]
        or p015_result.get("state") != "PASS_COMPLETE_RESERVE_REFERENCE_SUFFICIENCY"
        or p014_closeout.get("state") != "CLOSED_ATOMIC_REFERENCE_SUFFICIENCY_FAIL"
        or p014_prereg.get("review", {}).get("primary_aggregate")
        != "macro_equal_weight_across_eight_era_board_cells"
        or p014_prereg.get("review", {}).get("secondary_aggregate")
        != "pooled_micro_descriptive_only"
    ):
        raise CombinedAtomicScoringError("governing_contract_binding")

    training_root = training_root.expanduser().resolve()
    _p014_root, _p014_manifest, p014 = _artifact_payloads(
        training_root=training_root,
        binding=config["bindings"]["p014_semantic_artifact"],
        required=(
            "evidence.json",
            "path-facts.json",
            "case-strata.json",
            "envelope-measurements.json",
            "threshold-basis.json",
            "candidate-grid.json",
        ),
        label="p014_semantic",
    )
    _p015_root, _p015_manifest, p015 = _artifact_payloads(
        training_root=training_root,
        binding=config["bindings"]["p015_additional_review_artifact"],
        required=("evidence.json", "path-facts.json", "case-strata.json"),
        label="p015_additional",
    )
    old_ledger = _ledger(
        training_root,
        config["bindings"]["p014_review_ledger"],
        expected_count=192,
        label="p014_review_ledger",
    )
    new_ledger = _ledger(
        training_root,
        config["bindings"]["p015_review_ledger"],
        expected_count=60,
        label="p015_review_ledger",
    )
    if new_ledger.get("frozen_p014_review", {}).get("ledger_sha256") != config["bindings"][
        "p014_review_ledger"
    ]["sha256"]:
        raise CombinedAtomicScoringError("incremental_ledger_parent_binding")

    old_cases, old_strata = _artifact_cases(p014, expected_count=192)
    new_cases, new_strata = _artifact_cases(p015, expected_count=60)
    if set(old_cases) & set(new_cases):
        raise CombinedAtomicScoringError("old_new_case_overlap")
    cases = {**old_cases, **new_cases}
    strata = {**old_strata, **new_strata}
    references = _combined_references(old_ledger, new_ledger)
    if set(cases) != set(references) or len(cases) != 252:
        raise CombinedAtomicScoringError("case_reference_closure")
    counts = _reference_counts(references)
    expected = config["reference_contract"]["expected_counts"]
    for proposition in PROPOSITIONS:
        if counts["atomic"].get(proposition) != expected[proposition]:
            raise CombinedAtomicScoringError("reference_count_binding")
    if counts["projected"] != config["reference_contract"]["expected_projected_reference_counts"]:
        raise CombinedAtomicScoringError("projected_reference_count_binding")

    basis = p014["threshold-basis.json"]
    grid = p014["candidate-grid.json"]
    old_envelopes = p014["envelope-measurements.json"]
    if (
        basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or grid.get("threshold_basis_digest") != digest(basis)
        or grid.get("instability_candidate_count") != 9
        or grid.get("order_profile_count") != 3
        or grid.get("relocation_candidate_count") != 1
        or grid.get("joint_pair_search_count") != 0
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
        or grid.get("fixed_envelope_channel")
        != p012_prereg["instability_candidate_surface"]["fixed_additive_channel"]
    ):
        raise CombinedAtomicScoringError("candidate_basis_contract")
    old_envelope_cases = old_envelopes.get("cases")
    if (
        old_envelopes.get("review_transport_visible") is not False
        or not isinstance(old_envelope_cases, Mapping)
        or set(old_envelope_cases) != set(old_cases)
    ):
        raise CombinedAtomicScoringError("p014_envelope_binding")
    envelopes = {case_id: dict(value) for case_id, value in old_envelope_cases.items()}
    for case_id, case in new_cases.items():
        envelopes[case_id] = _envelope_from_case(case)

    result = score_atomic(
        cases=cases,
        references=references,
        strata=strata,
        basis=basis,
        candidate_grid=grid,
        envelopes=envelopes,
        prereg=p012_prereg,
    )
    public: dict[str, Any] = {
        "schema_version": "quanthus.stage-combined-atomic-scoring-result.v1",
        "work_item": "P3-STAGE-016-B",
        "disposition": result["disposition"],
        "reference_counts": counts,
        "reference_case_count": len(cases),
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(grid),
        "combined_envelope_digest": digest(envelopes),
        "instability_trials": [
            _trial_public(item, instability=True) for item in result.get("instability_trials", [])
        ],
        "joint_pair_search_count": 0,
        "candidate_surface_changed": False,
        "threshold_recomputed": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2025_read": False,
    }
    if result.get("selected_instability") is not None:
        public["selected_instability"] = _trial_public(
            result["selected_instability"], instability=True
        )
    if result.get("relocation_metrics") is not None:
        public["relocation_metrics"] = _plain(result["relocation_metrics"])
    if result.get("relocation_diagnostics") is not None:
        public["relocation_diagnostics"] = _plain(result["relocation_diagnostics"])
    if result.get("order_trials") is not None:
        public["order_trials"] = [
            _trial_public(item, instability=False) for item in result["order_trials"]
        ]
    if result.get("selected_order") is not None:
        public["selected_order"] = _trial_public(result["selected_order"], instability=False)
    public["atomic_freeze"] = _plain(result.get("atomic_freeze"))
    public["atomic_freeze_digest"] = result.get("atomic_freeze_digest")

    frozen_predictions = result.get("frozen_predictions")
    prediction_payload = {
        "schema_version": "quanthus.stage-combined-atomic-predictions.v1",
        "available": frozen_predictions is not None,
        "atomic_freeze_digest": result.get("atomic_freeze_digest"),
        "predictions": {} if frozen_predictions is None else frozen_predictions,
        "case_count": 0 if frozen_predictions is None else len(frozen_predictions),
        "projection_performed": False,
        "post_score_repick": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-016-B",
        "phase": "INDEPENDENT_ATOMIC_SCORING",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "disposition": result["disposition"],
        "reference_case_count": len(cases),
        "atomic_freeze_available": result.get("atomic_freeze") is not None,
        "atomic_freeze_digest": result.get("atomic_freeze_digest"),
        "instability_trial_count": len(public["instability_trials"]),
        "order_trial_count": len(public.get("order_trials", [])),
        "relocation_fixed": True,
        "joint_pair_search_count": 0,
        "projection_performed": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2025_read": False,
    }

    run_id = "stage-combined-atomic-scoring-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "atomic-score.json": canonical(_plain(public)),
        "frozen-predictions.json": canonical(_plain(prediction_payload)),
        "reference-binding.json": canonical(
            {
                "p014_review_ledger_sha256": config["bindings"]["p014_review_ledger"]["sha256"],
                "p015_review_ledger_sha256": config["bindings"]["p015_review_ledger"]["sha256"],
                "combined_case_count": 252,
                "reference_counts": counts,
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
            "projection_performed": False,
            "candidate_reselection": False,
            "preserved_2021_read": False,
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
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if not isinstance(manifest, dict) or _sha(manifest_raw) != ready:
        raise CombinedAtomicScoringError("artifact_manifest")
    if manifest.get("schema_version") != "quanthus.stage-combined-atomic-scoring-artifact.v1":
        raise CombinedAtomicScoringError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise CombinedAtomicScoringError("artifact_revision")
    files = manifest.get("files")
    expected = {
        "atomic-score.json",
        "frozen-predictions.json",
        "reference-binding.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise CombinedAtomicScoringError("artifact_files")
    payloads: dict[str, Any] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if files[name] != _sha(raw):
            raise CombinedAtomicScoringError(f"artifact_file_digest:{name}")
        payloads[name] = json.loads(raw)
    summary = payloads["summary.json"]
    score = payloads["atomic-score.json"]
    predictions = payloads["frozen-predictions.json"]
    if (
        summary.get("reference_case_count") != 252
        or summary.get("joint_pair_search_count") != 0
        or summary.get("projection_performed") is not False
        or summary.get("dwd_market_read") is not False
        or summary.get("preserved_2021_read") is not False
        or summary.get("2025_read") is not False
        or score.get("candidate_surface_changed") is not False
        or score.get("threshold_recomputed") is not False
        or score.get("joint_pair_search_count") != 0
        or predictions.get("projection_performed") is not False
        or predictions.get("post_score_repick") is not False
    ):
        raise CombinedAtomicScoringError("artifact_authority")
    if predictions.get("available") is True:
        if predictions.get("case_count") != 252 or len(predictions.get("predictions", {})) != 252:
            raise CombinedAtomicScoringError("prediction_count")
        if predictions.get("atomic_freeze_digest") != summary.get("atomic_freeze_digest"):
            raise CombinedAtomicScoringError("prediction_freeze_binding")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "reference_case_count": summary.get("reference_case_count"),
        "atomic_freeze_available": summary.get("atomic_freeze_available"),
        "atomic_freeze_digest": summary.get("atomic_freeze_digest"),
        "instability_trial_count": summary.get("instability_trial_count"),
        "order_trial_count": summary.get("order_trial_count"),
        "relocation_fixed": summary.get("relocation_fixed"),
        "joint_pair_search_count": 0,
        "projection_performed": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
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
    print(json.dumps(_plain(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
