"""P3-STAGE-014-A deterministic admission-only Era x Board resource screen.

The screen reconstructs the exposure-aware metadata surface, removes the exact P013-D consumed
identity-windows, freezes a bounded deterministic candidate order, and reads market inputs only to
reproduce DailyInput/contiguous-segment readiness. It never builds Structure facts, morphology,
thresholds, references, candidate outputs or scores, and it never persists raw OHLC values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.stage_learning.development_resource_plan import (
    verify_artifact as verify_plan_artifact,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-development-resource-admission-screen.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class DevelopmentResourceAdmissionScreenError(ValueError):
    """Raised when the frozen P014-A admission-only contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentResourceAdmissionScreenError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DevelopmentResourceAdmissionScreenError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise DevelopmentResourceAdmissionScreenError(f"cannot_hash:{path.name}") from exc


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DevelopmentResourceAdmissionScreenError("invalid_date") from exc


def _rank(seed: str, *parts: object) -> str:
    return hashlib.sha256("|".join([seed, *(str(part) for part in parts)]).encode()).hexdigest()


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise DevelopmentResourceAdmissionScreenError(f"binding:{label}")
    return _object(path, label)


def load_config(path: Path) -> dict[str, Any]:
    value = _object(path, "admission_screen_config")
    if (
        value.get("schema_version") != "quanthus.stage-development-resource-admission-screen.v1"
        or value.get("work_item") != "P3-STAGE-014-A"
        or value.get("state") != "PREREGISTERED_ADMISSION_ONLY_BEFORE_INPUT_READ"
    ):
        raise DevelopmentResourceAdmissionScreenError("config_identity")
    order = value.get("candidate_order")
    admission = value.get("admission_contract")
    target = value.get("qualification_target")
    runtime = value.get("runtime")
    authority = value.get("authority")
    sections = (order, admission, target, runtime, authority)
    if not all(isinstance(item, Mapping) for item in sections):
        raise DevelopmentResourceAdmissionScreenError("config_sections")
    if (
        order.get("maximum_candidate_identities_per_cell") != 256
        or order.get("maximum_windows_per_identity") != 4
        or order.get("p013_exact_token_overlap_allowed") is not False
        or target.get("required_unique_qualified_identities_per_cell") != 32
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise DevelopmentResourceAdmissionScreenError("config_budget")
    for key in (
        "semantic_market_read",
        "structure_fact_build",
        "threshold_or_model_basis",
        "reference_opinion",
        "candidate_output",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise DevelopmentResourceAdmissionScreenError(f"authority:{key}")
    if authority.get("input_readiness_market_read") is not True:
        raise DevelopmentResourceAdmissionScreenError("authority_input_read")
    return value


def _spread_tokens(
    rows: Sequence[Mapping[str, Any]], *, count: int, seed: str, identity: str
) -> list[dict[str, Any]]:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            _day(row["window_start"]),
            _day(row["window_end"]),
            str(row["window_id"]),
        ),
    )
    if not ordered:
        return []
    keep = min(count, len(ordered))
    if len(ordered) <= keep:
        selected = ordered
    else:
        positions = [((2 * index + 1) * len(ordered)) // (2 * keep) for index in range(keep)]
        selected = [ordered[index] for index in positions]
    return sorted(
        selected,
        key=lambda row: (
            _rank(seed, identity, row["window_id"]),
            str(row["window_id"]),
        ),
    )


def freeze_candidate_surface(
    *,
    tokens: Sequence[Mapping[str, Any]],
    consumed: set[tuple[str, str]],
    model: Mapping[str, Any],
) -> list[dict[str, Any]]:
    policy = model["candidate_screen"]
    identity_seed = str(policy["identity_seed"])
    window_seed = str(policy["window_seed"])
    identity_cap = int(policy["maximum_candidate_identities_per_cell"])
    window_cap = int(policy["maximum_windows_per_identity"])
    expected_cells = tuple(str(value) for value in policy["cell_allocation_order"])
    by_cell_identity: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in tokens:
        identity = str(row["security_id"])
        window_id = str(row["window_id"])
        if (identity, window_id) in consumed:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in expected_cells:
            by_cell_identity[cell][identity].append(row)
    output: list[dict[str, Any]] = []
    for cell_order, cell in enumerate(expected_cells, start=1):
        identities = sorted(
            by_cell_identity[cell],
            key=lambda identity: (_rank(identity_seed, cell, identity), identity),
        )[:identity_cap]
        for identity_rank, identity in enumerate(identities, start=1):
            windows = _spread_tokens(
                by_cell_identity[cell][identity],
                count=window_cap,
                seed=window_seed,
                identity=identity,
            )
            for window_rank, row in enumerate(windows, start=1):
                output.append(
                    {
                        "security_id": identity,
                        "security_code": str(row["security_code"]),
                        "listing_id": str(row["listing_id"]),
                        "era": str(row["era"]),
                        "board_family": str(row["board_family"]),
                        "board_code": str(row["board_code"]),
                        "venue_id": str(row["venue_id"]),
                        "cell": cell,
                        "window_id": str(row["window_id"]),
                        "window_start": _day(row["window_start"]).isoformat(),
                        "window_end": _day(row["window_end"]).isoformat(),
                        "prior_exposure_level": str(row["exposure_level"]),
                        "cell_order": cell_order,
                        "identity_rank": identity_rank,
                        "window_rank": window_rank,
                    }
                )
    if {row["cell"] for row in output} != set(expected_cells):
        raise DevelopmentResourceAdmissionScreenError("candidate_cell_missing")
    return output


def _reason_for_attempts(attempts: Sequence[Any], expected: Mapping[str, Any]) -> tuple[bool, str]:
    if len(attempts) != 61:
        return False, "SESSION_GAP"
    rows = [attempt.row for attempt in attempts]
    if [row.session_index for row in rows] != list(range(61)):
        return False, "SESSION_GAP"
    if rows[-1].market_date != _day(expected["window_end"]):
        return False, "SESSION_GAP"
    if any(not attempt.admitted for attempt in attempts):
        identity_reasons = {"identity_unproven", "security_outside_decision_universe"}
        if any(attempt.reason in identity_reasons for attempt in attempts):
            return False, "IDENTITY_OR_LISTING_DISCONTINUITY"
        return False, "MISSING_OR_INVALID_DAILY_INPUT"
    if len({row.listing_id for row in rows}) != 1 or rows[0].listing_id != expected["listing_id"]:
        return False, "IDENTITY_OR_LISTING_DISCONTINUITY"
    if (
        len({row.security_id for row in rows}) != 1
        or rows[0].security_id != expected["security_id"]
    ):
        return False, "IDENTITY_OR_LISTING_DISCONTINUITY"
    if len({row.coordinate_ref for row in rows}) != 1 or any(row.reset_reason for row in rows):
        return False, "COORDINATE_RESET"
    return True, "61_SESSION_CONTIGUOUS_INPUT_READY"


def _screen_candidates(
    *,
    candidates: Sequence[Mapping[str, Any]],
    training_root: Path,
    node: Mapping[str, Any],
    spec_digest: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_window: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_window[str(row["window_id"])].append(row)
    ledger: list[dict[str, Any]] = []
    window_receipts: list[dict[str, Any]] = []
    for window_id in sorted(by_window):
        members = by_window[window_id]
        starts = {_day(row["window_start"]) for row in members}
        ends = {_day(row["window_end"]) for row in members}
        if len(starts) != 1 or len(ends) != 1:
            raise DevelopmentResourceAdmissionScreenError("candidate_window_shape")
        codes = tuple(sorted({str(row["security_code"]) for row in members}))
        scope = DevelopmentReadScope(next(iter(starts)), next(iter(starts)), next(iter(ends)))
        admissions, manifest, policy = read_development_admissions(
            training_root=training_root,
            node_config=node,
            scope=scope,
            security_codes=codes,
            spec_digest=spec_digest,
        )
        attempts_by_code: dict[str, list[Any]] = defaultdict(list)
        for attempt in admissions:
            attempts_by_code[attempt.row.security_code].append(attempt)
        for values in attempts_by_code.values():
            values.sort(key=lambda attempt: attempt.row.market_date)
        ordered_members = sorted(
            members,
            key=lambda item: (int(item["identity_rank"]), int(item["window_rank"])),
        )
        for row in ordered_members:
            attempts = attempts_by_code.get(str(row["security_code"]), [])
            qualified, reason = _reason_for_attempts(attempts, row)
            binding = digest([attempt.to_dict() for attempt in attempts])
            ledger.append(
                {
                    "security_id": str(row["security_id"]),
                    "security_code": str(row["security_code"]),
                    "era": str(row["era"]),
                    "board_family": str(row["board_family"]),
                    "cell": str(row["cell"]),
                    "window_id": str(row["window_id"]),
                    "window_start": str(row["window_start"]),
                    "window_end": str(row["window_end"]),
                    "identity_rank": int(row["identity_rank"]),
                    "window_rank": int(row["window_rank"]),
                    "qualified": qualified,
                    "reason": reason,
                    "source_binding_digest": binding,
                }
            )
        window_receipts.append(
            {
                "window_id": window_id,
                "scope": scope.to_dict(),
                "candidate_security_count": len(codes),
                "attempted": int(manifest["attempted"]),
                "admitted": int(manifest["admitted"]),
                "rejected": int(manifest["rejected"]),
                "manifest_digest": digest(manifest),
                "calendar_ref": policy.calendar_ref,
                "base_manifest_sha256": manifest["base_manifest_sha256"],
                "correction_manifest_sha256": manifest["correction_manifest_sha256"],
            }
        )
    return ledger, window_receipts


def _cell_summary(
    ledger: Sequence[Mapping[str, Any]], *, required: int
) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    all_pass = True
    cells = sorted({str(row["cell"]) for row in ledger})
    for cell in cells:
        rows = [row for row in ledger if row["cell"] == cell]
        identities = {str(row["security_id"]) for row in rows}
        qualified_rows = [row for row in rows if row["qualified"] is True]
        qualified_identities = {str(row["security_id"]) for row in qualified_rows}
        reasons = Counter(str(row["reason"]) for row in rows if row["qualified"] is not True)
        sufficient = len(qualified_identities) >= required
        all_pass &= sufficient
        output.append(
            {
                "cell": cell,
                "candidate_identity_count": len(identities),
                "candidate_window_count": len(rows),
                "qualified_unique_identity_count": len(qualified_identities),
                "qualified_window_count": len(qualified_rows),
                "failure_reason_counts": dict(sorted(reasons.items())),
                "required_unique_qualified_identity_count": required,
                "sufficient_for_plan_freeze": sufficient,
            }
        )
    return output, all_pass


def run_screen(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DevelopmentResourceAdmissionScreenError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _bound_json(root, config["bindings"]["admission_model"], "admission_model")
    closeout = _bound_json(root, config["bindings"]["p013_d_closeout"], "p013_d_closeout")
    _bound_json(root, config["bindings"]["capacity_census_result"], "capacity_result")
    plan_result = _bound_json(root, config["bindings"]["p013_exact_plan_result"], "plan_result")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if model.get("state") != "DESIGN_SELECTED_ADMISSION_SCREEN_REQUIRED_BEFORE_SEMANTIC_READ":
        raise DevelopmentResourceAdmissionScreenError("model_state")
    if closeout.get("state") != "CLOSED_PRE_REVIEW_INPUT_ADMISSION_SHORTFALL":
        raise DevelopmentResourceAdmissionScreenError("p013_closeout_state")
    if closeout.get("retry_authority", {}).get("same_experiment_retry") is not False:
        raise DevelopmentResourceAdmissionScreenError("p013_retry_authority")

    training_root = training_root.expanduser().resolve()
    artifact_binding = config["bindings"]["p013_exact_plan_artifact"]
    artifact_root = (training_root / str(artifact_binding["artifact_relative_path"])).resolve()
    verified = verify_plan_artifact(
        artifact_root,
        code_revision=str(artifact_binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != artifact_binding["manifest_sha256"]
        or verified.get("plan_digest") != artifact_binding["plan_digest"]
        or plan_result.get("artifact", {}).get("manifest_sha256")
        != artifact_binding["manifest_sha256"]
    ):
        raise DevelopmentResourceAdmissionScreenError("p013_plan_artifact_binding")
    exact_plan = _object(artifact_root / "exact-plan.json", "p013_exact_plan")
    exact_rows = exact_plan.get("rows")
    if not isinstance(exact_rows, list) or len(exact_rows) != 256:
        raise DevelopmentResourceAdmissionScreenError("p013_exact_plan_rows")
    consumed = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in exact_rows
        if isinstance(row, Mapping)
    }
    if len(consumed) != 256:
        raise DevelopmentResourceAdmissionScreenError("p013_consumed_token_count")

    tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root,
        training_root=training_root,
        config=config,
    )
    candidates = freeze_candidate_surface(tokens=tokens, consumed=consumed, model=model)
    if {row["security_id"] for row in candidates} & preserved_ids:
        raise DevelopmentResourceAdmissionScreenError("preserved_identity_in_candidates")
    config_digest = digest(config)
    ledger, receipts = _screen_candidates(
        candidates=candidates,
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
    )
    cell_summary, capacity_pass = _cell_summary(
        ledger,
        required=int(config["qualification_target"]["required_unique_qualified_identities_per_cell"]),
    )
    candidate_payload = {
        "schema_version": config["output"]["candidate_ledger_schema"],
        "candidate_order_frozen_before_input_read": True,
        "rows": ledger,
        "row_count": len(ledger),
        "raw_ohlc_retained": False,
        "structure_facts_built": False,
        "threshold_basis_computed": False,
        "reference_or_label_read": False,
        "candidate_output_computed": False,
        "candidate_scoring": False,
    }
    source_payload = {
        "schema_version": config["output"]["source_binding_schema"],
        "p013_exact_plan_manifest_sha256": verified["manifest_sha256"],
        "p013_exact_plan_digest": verified["plan_digest"],
        "p013_consumed_token_count": len(consumed),
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "window_receipts": receipts,
        "window_receipts_digest": digest(receipts),
        "preserved_2021_identity_count": len(preserved_ids),
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-014-A",
        "phase": "ADMISSION_ONLY_ERA_BOARD_RESOURCE_SCREEN",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "candidate_window_count": len(ledger),
        "candidate_unique_identity_count": len({row["security_id"] for row in ledger}),
        "qualified_window_count": sum(row["qualified"] is True for row in ledger),
        "qualified_unique_identity_count": len(
            {row["security_id"] for row in ledger if row["qualified"] is True}
        ),
        "cell_summary": cell_summary,
        "all_cells_have_required_qualified_capacity": capacity_pass,
        "candidate_ledger_digest": digest(candidate_payload),
        "source_binding_digest": digest(source_payload),
        "raw_ohlc_published": False,
        "raw_ohlc_retained": False,
        "structure_facts_built": False,
        "morphology_computed": False,
        "threshold_basis_computed": False,
        "reference_or_label_read": False,
        "candidate_output_computed": False,
        "candidate_scoring": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "qualified_plan_frozen": False,
        "semantic_job_authorized": False,
    }
    run_id = "stage-development-resource-admission-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "candidate-ledger.json": canonical(candidate_payload),
        "source-binding.json": canonical(source_payload),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": config_digest,
        "files": {name: _sha_bytes(raw) for name, raw in payloads.items()},
        "authority": {
            "raw_ohlc_published": False,
            "structure_facts_built": False,
            "threshold_basis_computed": False,
            "reference_or_label_read": False,
            "candidate_output_computed": False,
            "candidate_scoring": False,
            "preserved_2021_market_read": False,
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
        raise DevelopmentResourceAdmissionScreenError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-development-resource-admission-artifact.v1"
    ):
        raise DevelopmentResourceAdmissionScreenError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DevelopmentResourceAdmissionScreenError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"candidate-ledger.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DevelopmentResourceAdmissionScreenError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise DevelopmentResourceAdmissionScreenError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DevelopmentResourceAdmissionScreenError(f"artifact_shape:{name}")
        payloads[name] = value
    ledger = payloads["candidate-ledger.json"]
    summary = payloads["summary.json"]
    rows = ledger.get("rows")
    if not isinstance(rows, list) or not rows:
        raise DevelopmentResourceAdmissionScreenError("ledger_rows")
    allowed_keys = {
        "security_id",
        "security_code",
        "era",
        "board_family",
        "cell",
        "window_id",
        "window_start",
        "window_end",
        "identity_rank",
        "window_rank",
        "qualified",
        "reason",
        "source_binding_digest",
    }
    if any(not isinstance(row, Mapping) or set(row) != allowed_keys for row in rows):
        raise DevelopmentResourceAdmissionScreenError("ledger_row_fields")
    forbidden = {"open", "high", "low", "close", "volume", "threshold", "candidate", "label"}
    if any(any(key.lower() in forbidden for key in row) for row in rows):
        raise DevelopmentResourceAdmissionScreenError("ledger_raw_or_semantic_field")
    if ledger.get("raw_ohlc_retained") is not False:
        raise DevelopmentResourceAdmissionScreenError("ledger_raw_ohlc")
    for key in (
        "raw_ohlc_published",
        "raw_ohlc_retained",
        "structure_facts_built",
        "morphology_computed",
        "threshold_basis_computed",
        "reference_or_label_read",
        "candidate_output_computed",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "qualified_plan_frozen",
        "semantic_job_authorized",
    ):
        if summary.get(key) is not False:
            raise DevelopmentResourceAdmissionScreenError(f"summary_authority:{key}")
    reasons = {
        "61_SESSION_CONTIGUOUS_INPUT_READY",
        "MISSING_OR_INVALID_DAILY_INPUT",
        "IDENTITY_OR_LISTING_DISCONTINUITY",
        "COORDINATE_RESET",
        "SESSION_GAP",
        "SOURCE_BINDING_FAILURE",
    }
    if any(str(row["reason"]) not in reasons for row in rows):
        raise DevelopmentResourceAdmissionScreenError("ledger_reason")
    if digest(ledger) != summary.get("candidate_ledger_digest"):
        raise DevelopmentResourceAdmissionScreenError("summary_ledger_digest")
    source = payloads["source-binding.json"]
    if digest(source) != summary.get("source_binding_digest"):
        raise DevelopmentResourceAdmissionScreenError("summary_source_digest")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "candidate_window_count": summary.get("candidate_window_count"),
        "candidate_unique_identity_count": summary.get("candidate_unique_identity_count"),
        "qualified_window_count": summary.get("qualified_window_count"),
        "qualified_unique_identity_count": summary.get("qualified_unique_identity_count"),
        "cell_summary": summary.get("cell_summary"),
        "all_cells_have_required_qualified_capacity": summary.get(
            "all_cells_have_required_qualified_capacity"
        ),
        "raw_ohlc_retained": False,
        "semantic_job_authorized": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_screen(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
