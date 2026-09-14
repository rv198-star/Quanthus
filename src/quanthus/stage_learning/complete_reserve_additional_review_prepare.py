"""Prepare blind primary-review evidence for the exact P015-B 60-row set.

The job reads only the frozen 61-session resource windows and publishes the same reviewer-visible
primary Structure facts, directional path facts, 21-close history and Era x Board strata used by
P014. It owns no reviewer, threshold, candidate, envelope, scoring or preserved-evaluation path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_successor_development_prepare import (
    _read_exact_plan_windows,
)
from quanthus.stage_learning.complete_reserve_additional_review_set import (
    verify_artifact as verify_additional_set_artifact,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    build_evidence,
    validate_evidence,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    _combined_feature_rows,
    _path_map,
    _retained_rows,
    _selected_path_facts,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json"
EVIDENCE_SCOPE_PATH = "config/stage-learning/order-reference-evidence-scope-p013.v1.json"
EVIDENCE_SCOPE_DIGEST = "sha256:54587544cbeebbceaa31b50a878180cec851743ab90e1e42aff06c87fd86de85"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class CompleteReserveAdditionalReviewPrepareError(ValueError):
    """The frozen P015-C1 blind-review prepare contract was violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompleteReserveAdditionalReviewPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise CompleteReserveAdditionalReviewPrepareError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise CompleteReserveAdditionalReviewPrepareError(f"cannot_hash:{path.name}") from exc


def _bound_file(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("file_sha256") or "")
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise CompleteReserveAdditionalReviewPrepareError(f"binding:{label}")
    return _object(path, label)


def _bound_canonical(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    value = _object(path, label)
    if digest(value) != str(binding.get("canonical_digest") or ""):
        raise CompleteReserveAdditionalReviewPrepareError(f"binding:{label}")
    return value


def load_config(path: Path) -> dict[str, Any]:
    value = _object(path, "additional_review_prepare_config")
    if (
        value.get("schema_version")
        != "quanthus.stage-complete-reserve-additional-review-prepare.v1"
        or value.get("work_item") != "P3-STAGE-015-C1"
        or value.get("state") != "PREREGISTERED_EXACT_60_PRIMARY_BLIND_REVIEW_PREPARE"
    ):
        raise CompleteReserveAdditionalReviewPrepareError("config_identity")
    resource = value.get("resource_set")
    market = value.get("market_read")
    prepare = value.get("review_prepare")
    review = value.get("review")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (resource, market, prepare, review, runtime, authority)
    ):
        raise CompleteReserveAdditionalReviewPrepareError("config_sections")
    if (
        resource.get("row_count") != 60
        or resource.get("all_matching_rows_included") is not True
        or resource.get("one_case_per_economic_identity") is not True
        or resource.get("case_t") != "exact_window_end_only"
        or resource.get("open_sessions_per_window") != 61
        or resource.get("fallback_or_replacement") is not False
        or resource.get("new_identity_discovery") is not False
        or resource.get("semantic_selection") is not False
        or resource.get("positive_count_stop") is not False
        or market.get("read_outside_exact_window") is not False
        or market.get("read_identity_history_outside_exact_window") is not False
        or prepare.get("expected_case_count") != 60
        or prepare.get("read_existing_p014_opinions") is not False
        or prepare.get("publish_threshold_basis") is not False
        or prepare.get("publish_candidate_grid") is not False
        or prepare.get("publish_envelope_measurements") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise CompleteReserveAdditionalReviewPrepareError("config_budget")
    if review.get("roles") != ["short_term", "fund"] or review.get(
        "same_origin_simulation"
    ) is not True:
        raise CompleteReserveAdditionalReviewPrepareError("review_contract")
    for key in (
        "candidate_output_visible",
        "threshold_basis_visible",
        "envelope_measurement_visible",
        "existing_p014_opinions_visible",
        "future_rows_visible",
        "preserved_2021_visible",
    ):
        if review.get(key) is not False:
            raise CompleteReserveAdditionalReviewPrepareError(f"review_visibility:{key}")
    required_true = (
        "implementation",
        "result_bearing_market_read",
        "structure_fact_build",
        "blind_review_packet_publication",
    )
    required_false = (
        "existing_p014_opinion_read",
        "reference_opinion_generation_inside_job",
        "threshold_basis_computation",
        "candidate_output_computation",
        "envelope_measurement_computation",
        "candidate_scoring",
        "selection_transport_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise CompleteReserveAdditionalReviewPrepareError("authority")
    return value


def _verified_set(
    training_root: Path, binding: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_additional_set_artifact(
        artifact_root, code_revision=str(binding["code_revision"])
    )
    if (
        verified.get("manifest_sha256") != binding["manifest_sha256"]
        or verified.get("set_digest") != binding["set_digest"]
        or verified.get("identity_digest") != binding["identity_digest"]
        or verified.get("row_count") != binding["row_count"]
    ):
        raise CompleteReserveAdditionalReviewPrepareError("additional_set_artifact_binding")
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    raw = (artifact_root / "additional-review-set.json").read_bytes()
    if manifest.get("files", {}).get("additional-review-set.json") != _sha_bytes(raw):
        raise CompleteReserveAdditionalReviewPrepareError("additional_set_file_binding")
    value = json.loads(raw)
    rows = value.get("rows") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, dict)
        or not isinstance(rows, list)
        or len(rows) != 60
        or digest(rows) != binding["set_digest"]
    ):
        raise CompleteReserveAdditionalReviewPrepareError("additional_set_payload_binding")
    return value, verified


def _runtime_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw in rows:
        row = dict(raw)
        identity = str(row["security_id"])
        if identity in identities:
            raise CompleteReserveAdditionalReviewPrepareError("duplicate_identity")
        identities.add(identity)
        output.append({**row, "open_session_count": 61})
    if len(output) != 60:
        raise CompleteReserveAdditionalReviewPrepareError("resource_count")
    return output


def _exact_selection(
    rows: Sequence[Mapping[str, Any]], feature_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    available = {
        (str(row["security_code"]), row["market_date"]): row
        for row in feature_rows
        if isinstance(row.get("market_date"), date)
    }
    selected: list[dict[str, Any]] = []
    for row in rows:
        day = date.fromisoformat(str(row["window_end"]))
        feature = available.get((str(row["security_code"]), day))
        if feature is None:
            raise CompleteReserveAdditionalReviewPrepareError(
                "readiness_drift:window_end_required_facts_unavailable"
            )
        selected.append(
            {
                "security_id": str(row["security_id"]),
                "security_code": str(row["security_code"]),
                "market_date": day.isoformat(),
                "era": str(row["era"]),
                "board_family": str(row["board_family"]),
                "cell": str(row["cell"]),
                "window_id": str(row["window_id"]),
                "window_start": str(row["window_start"]),
                "window_end": str(row["window_end"]),
                "plan_role": str(row["plan_role"]),
                "plan_slot": int(row["plan_slot"]),
                "readiness_source_member_closure_digest": str(
                    row["readiness_source_member_closure_digest"]
                ),
                "feature_digest": digest(
                    {
                        key: value
                        for key, value in feature.items()
                        if key not in {"security_code", "market_date"}
                    }
                ),
            }
        )
    if len(selected) != 60 or len({row["security_id"] for row in selected}) != 60:
        raise CompleteReserveAdditionalReviewPrepareError("selection_identity_count")
    return selected


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise CompleteReserveAdditionalReviewPrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    result = _bound_file(root, config["bindings"]["p015_b_result"], "p015_b_result")
    closeout = _bound_file(root, config["bindings"]["p014_closeout"], "p014_closeout")
    _bound_canonical(
        root, config["bindings"]["atomic_review_contract"], "atomic_review_contract"
    )
    protocol = _bound_canonical(root, config["bindings"]["evidence_protocol"], "evidence_protocol")
    node = _bound_file(root, config["bindings"]["training_node"], "training_node")
    if result.get("state") != "PASS_COMPLETE_ADDITIONAL_REVIEW_SET_FROZEN":
        raise CompleteReserveAdditionalReviewPrepareError("p015_b_result_state")
    if closeout.get("review_ledger", {}).get("ledger_sha256") != config["bindings"][
        "p014_closeout"
    ]["review_ledger_sha256"]:
        raise CompleteReserveAdditionalReviewPrepareError("p014_review_ledger_binding")

    training_root = training_root.expanduser().resolve()
    frozen_set, set_verified = _verified_set(
        training_root, config["bindings"]["additional_review_set_artifact"]
    )
    raw_rows = frozen_set.get("rows")
    if not isinstance(raw_rows, list):
        raise CompleteReserveAdditionalReviewPrepareError("additional_set_rows")
    rows = _runtime_rows(raw_rows)
    admissions, read_binding, calendar_ref = _read_exact_plan_windows(
        training_root=training_root,
        node=node,
        rows=rows,
    )
    report_start = min(date.fromisoformat(str(item["window_start"])) for item in rows)
    report_end = max(date.fromisoformat(str(item["window_end"])) for item in rows)
    observations, fact_build_counts = build_order_observations(
        admissions,
        report_start=report_start,
        report_end=report_end,
    )
    path_values = _path_map(admissions, report_start=report_start, report_end=report_end)
    feature_rows = _combined_feature_rows(observations, path_values)
    selected = _exact_selection(rows, feature_rows)
    retained = _retained_rows(admissions, selected)
    selected_codes = tuple(sorted({item["security_code"] for item in selected}))
    evidence = build_evidence(
        retained,
        [
            (item["security_code"], date.fromisoformat(item["market_date"]))
            for item in selected
        ],
        InputPolicy(EVIDENCE_SCOPE_DIGEST, selected_codes, calendar_ref),
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata={
            (item["security_code"], date.fromisoformat(item["market_date"])): "representative"
            for item in selected
        },
        protocol_override=protocol,
        protocol_digest=EVIDENCE_SCOPE_DIGEST,
    )
    validate_evidence(evidence, expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    if len(evidence.get("cases", [])) != 60:
        raise CompleteReserveAdditionalReviewPrepareError("evidence_case_count")
    selected_paths = _selected_path_facts(evidence, path_values)
    selected_by_key = {
        (item["security_code"], item["market_date"]): item for item in selected
    }
    case_strata: dict[str, dict[str, Any]] = {}
    for case in evidence["cases"]:
        key = (str(case["security_code"]), str(case["market_date"]))
        selected_case = selected_by_key.get(key)
        if not isinstance(selected_case, Mapping):
            raise CompleteReserveAdditionalReviewPrepareError("case_stratum_join")
        case_strata[str(case["case_id"])] = {
            "era": selected_case["era"],
            "board_family": selected_case["board_family"],
            "cell": selected_case["cell"],
            "plan_role": selected_case["plan_role"],
            "plan_slot": selected_case["plan_slot"],
            "window_id": selected_case["window_id"],
            "readiness_source_member_closure_digest": selected_case[
                "readiness_source_member_closure_digest"
            ],
        }
    if len(case_strata) != 60:
        raise CompleteReserveAdditionalReviewPrepareError("case_strata_count")
    cell_counts = Counter(item["cell"] for item in selected)
    expected_cells = result.get("cell_counts")
    if not isinstance(expected_cells, Mapping) or dict(sorted(cell_counts.items())) != dict(
        sorted((str(key), int(value)) for key, value in expected_cells.items())
    ):
        raise CompleteReserveAdditionalReviewPrepareError("cell_count_binding")

    selection = {
        "schema_version": config["output"]["selection_schema"],
        "rows": selected,
        "row_count": 60,
        "set_digest": set_verified["set_digest"],
        "all_frozen_rows_selected": True,
        "semantic_selection": False,
        "positive_count_stop": False,
        "existing_p014_opinions_read": False,
    }
    source_binding = {
        "schema_version": config["output"]["source_binding_schema"],
        "additional_set_job_id": config["bindings"]["additional_review_set_artifact"]["job_id"],
        "additional_set_manifest_sha256": set_verified["manifest_sha256"],
        "additional_set_digest": set_verified["set_digest"],
        "p014_review_ledger_sha256": config["bindings"]["p014_closeout"][
            "review_ledger_sha256"
        ],
        "market_read_binding": read_binding,
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "price_rows_outside_exact_window": False,
        "existing_p014_opinions_read": False,
        "threshold_basis_computed": False,
        "candidate_output_computed": False,
        "envelope_measurement_computed": False,
        "selection_transport_read": False,
        "preserved_2021_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-015-C1",
        "phase": "PREPARE_EXACT_60_PRIMARY_BLIND_REVIEW",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "resource_set_count": 60,
        "development_selected_case_count": 60,
        "development_shortfall_count": 0,
        "selected_cell_counts": dict(sorted(cell_counts.items())),
        "development_selection_digest": digest(selection),
        "development_evidence_digest": digest(evidence),
        "path_facts_digest": digest(selected_paths),
        "case_strata_digest": digest(case_strata),
        "fact_build_counts": fact_build_counts,
        "existing_p014_review_ledger_sha256": config["bindings"]["p014_closeout"][
            "review_ledger_sha256"
        ],
        "existing_p014_opinions_read": False,
        "threshold_basis_computed": False,
        "candidate_output_computed": False,
        "envelope_measurement_computed": False,
        "selection_transport_read": False,
        "opinion_count": 0,
        "review_ready": True,
        "preserved_2021_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    run_id = "stage-complete-reserve-additional-review-prepare-" + datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "source-binding.json": canonical(source_binding),
        "review-selection.json": canonical(selection),
        "evidence.json": canonical(evidence),
        "path-facts.json": canonical(selected_paths),
        "case-strata.json": canonical(case_strata),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise CompleteReserveAdditionalReviewPrepareError("evidence_size_budget")
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "files": {name: _sha_bytes(raw) for name, raw in payloads.items()},
        "authority": {
            "existing_p014_opinions_read": False,
            "threshold_basis_computed": False,
            "candidate_output_computed": False,
            "envelope_measurement_computed": False,
            "candidate_scoring": False,
            "selection_transport_read": False,
            "preserved_2021_market_data_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    ready = _sha_bytes(manifest_raw)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if _sha_bytes(manifest_raw) != ready:
        raise CompleteReserveAdditionalReviewPrepareError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-complete-reserve-additional-review-prepare-artifact.v1"
    ):
        raise CompleteReserveAdditionalReviewPrepareError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise CompleteReserveAdditionalReviewPrepareError("artifact_code_revision")
    files = manifest.get("files")
    expected = {
        "source-binding.json",
        "review-selection.json",
        "evidence.json",
        "path-facts.json",
        "case-strata.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise CompleteReserveAdditionalReviewPrepareError("artifact_inventory")
    payloads: dict[str, Any] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise CompleteReserveAdditionalReviewPrepareError(f"artifact_file_digest:{name}")
        payloads[name] = json.loads(raw)
    evidence = payloads["evidence.json"]
    validate_evidence(evidence, expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    selection = payloads["review-selection.json"]
    paths = payloads["path-facts.json"]
    strata = payloads["case-strata.json"]
    source = payloads["source-binding.json"]
    summary = payloads["summary.json"]
    cases = evidence.get("cases")
    if (
        not isinstance(cases, list)
        or len(cases) != 60
        or selection.get("row_count") != 60
        or len(selection.get("rows", [])) != 60
        or selection.get("all_frozen_rows_selected") is not True
        or selection.get("semantic_selection") is not False
        or selection.get("positive_count_stop") is not False
        or selection.get("existing_p014_opinions_read") is not False
        or not isinstance(paths.get("cases"), Mapping)
        or len(paths["cases"]) != 60
        or not isinstance(strata, Mapping)
        or len(strata) != 60
        or summary.get("development_selected_case_count") != 60
        or summary.get("development_shortfall_count") != 0
        or summary.get("review_ready") is not True
    ):
        raise CompleteReserveAdditionalReviewPrepareError("artifact_denominator")
    if (
        digest(selection) != summary.get("development_selection_digest")
        or digest(evidence) != summary.get("development_evidence_digest")
        or digest(paths) != summary.get("path_facts_digest")
        or digest(strata) != summary.get("case_strata_digest")
    ):
        raise CompleteReserveAdditionalReviewPrepareError("artifact_summary_binding")
    for key in (
        "existing_p014_opinions_read",
        "threshold_basis_computed",
        "candidate_output_computed",
        "envelope_measurement_computed",
        "selection_transport_read",
        "preserved_2021_market_data_read",
        "2022_2024_read",
        "2025_read",
    ):
        if source.get(key) is not False or summary.get(key) is not False:
            raise CompleteReserveAdditionalReviewPrepareError(f"artifact_authority:{key}")
    if not all(
        isinstance(case, Mapping)
        and str(case.get("case_id") or "") in strata
        and str(case.get("case_id") or "") in paths["cases"]
        for case in cases
    ):
        raise CompleteReserveAdditionalReviewPrepareError("artifact_case_binding")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "development_selected_case_count": 60,
        "development_shortfall_count": 0,
        "selected_cell_counts": summary.get("selected_cell_counts"),
        "development_selection_digest": summary.get("development_selection_digest"),
        "development_evidence_digest": summary.get("development_evidence_digest"),
        "path_facts_digest": summary.get("path_facts_digest"),
        "case_strata_digest": summary.get("case_strata_digest"),
        "existing_p014_review_ledger_sha256": summary.get(
            "existing_p014_review_ledger_sha256"
        ),
        "existing_p014_opinions_read": False,
        "threshold_basis_computed": False,
        "candidate_output_computed": False,
        "envelope_measurement_computed": False,
        "selection_transport_read": False,
        "opinion_count": 0,
        "review_ready": True,
        "preserved_2021_market_data_read": False,
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
        result = run_prepare(
            config_path=Path(args.config),
            training_root=Path(args.training_root),
            output_root=Path(args.output_root),
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(Path(args.artifact), code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
