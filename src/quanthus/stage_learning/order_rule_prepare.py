"""P3-STAGE-007-B bounded source-isolation and fixed-case prepare job.

This job may read only the declared 2019-2021 development source. It reconstructs the exact
Stage-004 512-security cohort against the accepted artifact, conservatively excludes every mapped
economic identity behind that cohort plus the Stage-006 securities, freezes one 2021 case set, and
publishes T-only reference evidence. It does not generate opinions, score the rule, search
parameters, read arbitration/future partitions, or publish official labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_proposition_development import (
    build_order_observations,
    verify_order_proposition_artifact,
)
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    PROTOCOL_DIGEST,
    build_evidence,
    validate_evidence,
)
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST
from quanthus.stage_learning.order_rule_validation import (
    MAX_SCAN_ROWS,
    VALIDATION_DIGEST,
    SamplingObservation,
    isolated_pool,
    select_cases,
    verify_old_cohort,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
    read_development_identity_members,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/order-rule-prepare.v1.json"
ARTIFACT_SCHEMA = "quanthus.stage-order-rule-prepare-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-order-rule-prepare-summary.v1"
_SOURCE_BINDING_SCHEMA = "quanthus.stage-order-rule-source-binding.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class OrderRulePrepareError(ValueError):
    """The prepare run or its retained artifact violates the frozen Stage-007 boundary."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderRulePrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise OrderRulePrepareError(f"{label}_must_be_object")
    return value


def _day(block: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(block[key]))
    except (KeyError, ValueError) as exc:
        raise OrderRulePrepareError(f"invalid_date:{key}") from exc


def _progress(started: float, stage: str, **values: object) -> None:
    print(json.dumps({
        "stage": stage,
        "elapsed_seconds": time.perf_counter() - started,
        **values,
    }, sort_keys=True), flush=True)


def load_prepare_config(path: Path) -> dict[str, Any]:
    config = _object(path, "prepare_config")
    if config.get("schema_version") != "quanthus.stage-order-rule-prepare.v1":
        raise OrderRulePrepareError("prepare_config_schema")
    if config.get("work_item") != "P3-STAGE-007-B":
        raise OrderRulePrepareError("prepare_work_item")
    bindings = config.get("bindings")
    if not isinstance(bindings, Mapping) or bindings != {
        "reference_protocol_digest": PROTOCOL_DIGEST,
        "rule_digest": RULE_DIGEST,
        "validation_digest": VALIDATION_DIGEST,
    }:
        raise OrderRulePrepareError("prepare_frozen_bindings")
    limits = config.get("limits")
    if not isinstance(limits, Mapping) or (
        limits.get("maximum_candidate_codes") != 10000
        or limits.get("maximum_identity_members") != 100000
        or limits.get("maximum_pool_securities") != 512
        or limits.get("maximum_scan_rows") != MAX_SCAN_ROWS
        or limits.get("maximum_cases") != 160
        or limits.get("maximum_evidence_bytes") != MAX_BYTES
        or limits.get("worker_wall_seconds") != 2700
    ):
        raise OrderRulePrepareError("prepare_limits_drift")
    authority = config.get("authority")
    if not isinstance(authority, Mapping):
        raise OrderRulePrepareError("prepare_authority_missing")
    for key in (
        "opinion_generation", "candidate_scoring", "parameter_search", "future_outcome_access",
        "arbitration_access", "permanent_blind_access", "official_labels", "primary_stage",
        "daily_or_strategy", "trading",
    ):
        if authority.get(key) is not False:
            raise OrderRulePrepareError("prepare_authority_expanded")
    for key in (
        "development_data_read", "identity_metadata_read", "old_cohort_metadata_reconstruction",
        "fixed_case_selection", "reference_evidence_publication",
    ):
        if authority.get(key) is not True:
            raise OrderRulePrepareError("prepare_required_authority_missing")
    return config


def _old_source_spec(old_config: Mapping[str, Any]) -> str:
    return digest({
        "schema_version": "quanthus.stage-order-proposition-source-spec.v1",
        "development_id": old_config["development_id"],
        "trend_fact_contract_digest": old_config["trend_fact_contract_digest"],
        "order_fact_contract_digest": old_config["order_fact_contract_digest"],
        "official_stage_labels": False,
    })


def _retained_rows(admissions: Sequence[Any], selected: Sequence[Mapping[str, Any]]) -> list[Any]:
    by_code: dict[str, list[Any]] = defaultdict(list)
    for attempt in admissions:
        by_code[attempt.row.security_code].append(attempt.row)
    rows = []
    for item in selected:
        code = str(item["security_code"])
        day = date.fromisoformat(str(item["market_date"]))
        history = [row for row in by_code[code] if row.market_date <= day][-61:]
        if len(history) != 61:
            raise OrderRulePrepareError("selected_case_missing_61_session_history")
        rows.extend(history)
    unique = {(row.security_code, row.market_date): row for row in rows}
    if len(unique) != len(rows):
        raise OrderRulePrepareError("one_case_per_identity_history_overlap_unexpected")
    return [unique[key] for key in sorted(unique)]


def _selected_strata(selected: Sequence[Mapping[str, Any]]) -> dict[tuple[str, date], str]:
    result = {}
    for item in selected:
        key = str(item["security_code"]), date.fromisoformat(str(item["market_date"]))
        if item["panel"] == "coverage":
            result[key] = "representative"
        elif item["bucket"] == "boundary":
            result[key] = "boundary"
        else:
            result[key] = "enriched"
    return result


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise OrderRulePrepareError("exact_code_revision_required")
    started = time.perf_counter()
    root = find_project_root()
    config = load_prepare_config(config_path)
    node_config = _object(root / str(config["training_node_config"]), "training_node_config")
    old = config["old_run"]
    old_config = _object(root / str(old["development_config"]), "old_development_config")
    if digest(old_config) != old["development_config_digest"]:
        raise OrderRulePrepareError("old_development_config_drift")

    training_root = training_root.expanduser().resolve()
    old_artifact = (training_root / str(old["artifact_relative_path"])).resolve()
    stage_runs = (training_root / "runs/stage-worker").resolve()
    try:
        old_artifact.relative_to(stage_runs)
    except ValueError as exc:
        raise OrderRulePrepareError("old_artifact_outside_stage_runs") from exc
    verified_old = verify_order_proposition_artifact(
        old_artifact, code_revision=str(old["code_revision"])
    )
    if verified_old["manifest_sha256"] != old["manifest_sha256"]:
        raise OrderRulePrepareError("old_artifact_manifest_mismatch")
    manifest_bytes = (old_artifact / "manifest.json").read_bytes()
    summary_bytes = (old_artifact / "summary.json").read_bytes()
    old_manifest = json.loads(manifest_bytes)
    old_summary = json.loads(summary_bytes)
    if old_manifest.get("development_config_digest") != old["development_config_digest"]:
        raise OrderRulePrepareError("old_manifest_config_binding_mismatch")

    window = old_config["development_window"]
    ranked_old = discover_development_security_population(
        training_root=training_root,
        node_config=node_config,
        scan_start=_day(window, "cohort_scan_start"),
        cohort_date=_day(window, "cohort_date"),
        minimum_observations=int(window["minimum_pre_cohort_observations"]),
        seed=str(window["cohort_seed"]),
    )
    cohort_size = int(old["cohort_size"])
    if len(ranked_old) < cohort_size:
        raise OrderRulePrepareError("old_population_smaller_than_frozen_cohort")
    old_codes = verify_old_cohort(
        manifest_bytes, summary_bytes, tuple(sorted(ranked_old[:cohort_size]))
    )
    _progress(started, "old-cohort-reconstructed", cohort_size=len(old_codes))

    old_scope = DevelopmentReadScope(
        _day(window, "warmup_start"), _day(window, "report_start"), _day(window, "report_end")
    )
    old_admissions, old_source_manifest, _old_policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=old_scope,
        security_codes=old_codes,
        spec_digest=_old_source_spec(old_config),
    )
    if digest(old_source_manifest) != old_summary.get("source_manifest_digest"):
        raise OrderRulePrepareError("old_source_manifest_reconstruction_mismatch")
    old_attempt_count = len(old_admissions)
    del old_admissions
    _progress(started, "old-source-binding-reconstructed", attempts=old_attempt_count)

    scope = config["source_scope"]
    candidate_codes = discover_development_security_population(
        training_root=training_root,
        node_config=node_config,
        scan_start=_day(scope, "candidate_scan_start"),
        cohort_date=_day(scope, "candidate_cohort_date"),
        minimum_observations=int(scope["candidate_minimum_observations"]),
        seed=str(scope["candidate_seed"]),
    )
    if len(candidate_codes) > int(config["limits"]["maximum_candidate_codes"]):
        raise OrderRulePrepareError("candidate_population_budget")
    stage006_codes = tuple(
        str(code) for code in json.loads(
            (root / "config/stage-order-rule-screen-delivery.v1.json").read_text(encoding="utf-8")
        )["validation"]["excluded_stage006_codes_to_resolve_to_economic_identities"]
    )
    required_codes = tuple(sorted(set(old_codes) | set(stage006_codes)))
    historical_members = read_development_identity_members(
        training_root=training_root,
        node_config=node_config,
        security_codes=required_codes,
        start=_day(scope, "identity_start"),
        end=_day(scope, "identity_end"),
    )
    candidate_members = read_development_identity_members(
        training_root=training_root,
        node_config=node_config,
        security_codes=tuple(candidate_codes),
        start=_day(scope, "identity_start"),
        end=_day(scope, "identity_end"),
        active_on=_day(scope, "candidate_cohort_date"),
    )
    pairs = sorted({(item["security_code"], item["security_id"])
                    for item in (*historical_members, *candidate_members)})
    if len(pairs) > int(config["limits"]["maximum_identity_members"]):
        raise OrderRulePrepareError("identity_member_budget")
    identity_members = [
        {"security_code": code, "security_id": identity} for code, identity in pairs
    ]
    isolation = isolated_pool(old_codes, identity_members)
    pool = isolation["pool"]
    if not pool:
        raise OrderRulePrepareError("isolated_pool_empty")
    if len(pool) > int(config["limits"]["maximum_pool_securities"]):
        raise OrderRulePrepareError("isolated_pool_budget")
    _progress(
        started, "isolated-pool-ready", candidate_population=len(candidate_codes),
        identity_members=len(identity_members),
        excluded_identities=len(isolation["excluded_economic_ids"]),
        pool_size=len(pool),
    )

    pool_codes = tuple(sorted(pool.values()))
    development_scope = DevelopmentReadScope(
        _day(scope, "history_start"), _day(scope, "report_start"), _day(scope, "report_end")
    )
    admissions, source_manifest, source_policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=development_scope,
        security_codes=pool_codes,
        spec_digest=PROTOCOL_DIGEST,
    )
    report_start, report_end = _day(scope, "report_start"), _day(scope, "report_end")
    full_observations, build_counts = build_order_observations(
        admissions, report_start=report_start, report_end=report_end,
        progress=lambda done, total, count: (
            _progress(started, "formal-facts-progress", securities_processed=done,
                      securities_total=total, available_observations=count)
            if done % 32 == 0 or done == total else None
        ),
    )
    observed = {(item.security_code, item.market_date): item for item in full_observations}
    code_to_identity = {code: identity for identity, code in pool.items()}
    sampling = []
    for attempt in admissions:
        row = attempt.row
        if row.market_date.year != 2021:
            continue
        identity = code_to_identity[row.security_code]
        if row.security_id and row.security_id != identity:
            raise OrderRulePrepareError("pool_code_economic_identity_changed_in_report_year")
        value = observed.get((row.security_code, row.market_date))
        sampling.append(SamplingObservation(
            security_id=identity,
            security_code=row.security_code,
            market_date=row.market_date,
            available=value is not None,
            efficiency=None if value is None else value.efficiency20_abs,
            width=None if value is None else value.range_width20,
            width_change=None if value is None else value.width_change20,
            center_shift=None if value is None else value.center_shift20_abs,
        ))
    selection = select_cases(pool, sampling)
    selected = selection["selected"]
    if len(selected) > int(config["limits"]["maximum_cases"]):
        raise OrderRulePrepareError("selected_case_budget")
    _progress(
        started, "selection-ready", selected=len(selected), shortfalls=selection["shortfalls"]
    )

    retained_rows = _retained_rows(admissions, selected)
    selected_codes = tuple(sorted(item["security_code"] for item in selected))
    evidence_policy = InputPolicy(PROTOCOL_DIGEST, selected_codes, source_policy.calendar_ref)
    evidence = build_evidence(
        retained_rows,
        [(item["security_code"], date.fromisoformat(item["market_date"])) for item in selected],
        evidence_policy,
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata=_selected_strata(selected),
        progress=lambda value: _progress(started, "evidence-" + value["stage"],
                                         rows=value["rows"], bytes=value["bytes"]),
    )
    cases = {(case["security_code"], case["market_date"]): case for case in evidence["cases"]}
    for item in selected:
        key = item["security_code"], item["market_date"]
        case = cases.get(key)
        if case is None or case["security_id"] != item["security_id"]:
            raise OrderRulePrepareError("selected_case_identity_differs_from_evidence")
        if digest(SamplingObservation.from_case(case)) != item["observation_digest"]:
            raise OrderRulePrepareError("selected_observation_differs_from_retained_evidence")

    source_binding = {
        "schema_version": _SOURCE_BINDING_SCHEMA,
        "old_job_id": old["job_id"],
        "old_code_revision": old["code_revision"],
        "old_manifest_sha256": old["manifest_sha256"],
        "old_cohort_codes": list(old_codes),
        "old_cohort_digest": digest(tuple(sorted(old_codes))),
        "old_source_manifest_digest": digest(old_source_manifest),
        "candidate_population_count": len(candidate_codes),
        "candidate_population_digest": digest(tuple(candidate_codes)),
        "identity_members_digest": isolation["identity_members_digest"],
        "excluded_economic_ids": isolation["excluded_economic_ids"],
        "pool": pool,
        "pool_digest": digest(pool),
        "source_manifest_digest": digest(source_manifest),
        "base_manifest_sha256": source_manifest["base_manifest_sha256"],
        "correction_manifest_sha256": source_manifest["correction_manifest_sha256"],
        "development_read_scope": development_scope.to_dict(),
        "source_isolation_verified": True,
        "old_160_contexts_read_back": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-007-B",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "rule_digest": RULE_DIGEST,
        "validation_digest": VALIDATION_DIGEST,
        "reference_protocol_digest": PROTOCOL_DIGEST,
        "source_isolation_verified": True,
        "old_cohort_size": len(old_codes),
        "excluded_economic_identity_count": len(isolation["excluded_economic_ids"]),
        "candidate_population_count": len(candidate_codes),
        "pool_size": len(pool),
        "source_attempt_count": len(admissions),
        "sampling_attempt_count": len(sampling),
        "fact_build_counts": build_counts,
        "selected_case_count": len(selected),
        "selection_digest": selection["selection_digest"],
        "selection_shortfalls": selection["shortfalls"],
        "evidence_digest": digest(evidence),
        "evidence_source_row_count": len(evidence["source_rows"]),
        "opinion_count": 0,
        "candidate_scored": False,
        "parameter_search": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "official_labels_emitted": False,
        "primary_stage_emitted": False,
        "review_ready": True,
        "elapsed_seconds": time.perf_counter() - started,
    }
    run_id = "stage-order-rule-prepare-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "source-binding.json": canonical(source_binding),
        "selection.json": canonical(selection),
        "evidence.json": canonical(evidence),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise OrderRulePrepareError("evidence_payload_budget")
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "source_isolation_verified": True,
        "opinion_generation": False,
        "candidate_scoring": False,
        "parameter_search": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    _progress(started, "published", run_id=run_id, selected=len(selected))
    return {**summary, "run_root": str(run_root), "run_id": run_id, "manifest_sha256": ready}


def verify_prepare_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "prepare_manifest")
    if manifest.get("schema_version") != ARTIFACT_SCHEMA:
        raise OrderRulePrepareError("prepare_manifest_schema")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise OrderRulePrepareError("prepare_ready_unreadable") from exc
    if ready != _sha(manifest_bytes):
        raise OrderRulePrepareError("prepare_ready_manifest_mismatch")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise OrderRulePrepareError("prepare_code_revision_mismatch")
    expected_files = {"source-binding.json", "selection.json", "evidence.json", "summary.json"}
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected_files:
        raise OrderRulePrepareError("prepare_inventory_mismatch")
    payloads = {}
    for name in sorted(expected_files):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise OrderRulePrepareError("prepare_file_digest_mismatch:" + name)
        payloads[name] = json.loads(raw)
    binding = payloads["source-binding.json"]
    selection = payloads["selection.json"]
    evidence = payloads["evidence.json"]
    summary = payloads["summary.json"]
    if (
        binding.get("source_isolation_verified") is not True
        or binding.get("old_160_contexts_read_back") is not False
        or binding.get("future_outcome_read") is not False
        or binding.get("arbitration_read") is not False
        or binding.get("permanent_blind_read") is not False
        or summary.get("source_isolation_verified") is not True
        or summary.get("opinion_count") != 0
        or summary.get("candidate_scored") is not False
        or summary.get("parameter_search") is not False
        or summary.get("official_labels_emitted") is not False
        or summary.get("primary_stage_emitted") is not False
        or summary.get("review_ready") is not True
    ):
        raise OrderRulePrepareError("prepare_authority_or_source_claim_mismatch")
    if selection.get("selection_digest") != summary.get("selection_digest"):
        raise OrderRulePrepareError("prepare_selection_summary_binding")
    body = {key: value for key, value in selection.items() if key != "selection_digest"}
    if digest(body) != selection["selection_digest"]:
        raise OrderRulePrepareError("prepare_selection_digest_mismatch")
    if digest(evidence) != summary.get("evidence_digest"):
        raise OrderRulePrepareError("prepare_evidence_summary_binding")
    validate_evidence(evidence)
    selected = selection.get("selected")
    cases = {(case["security_id"], case["market_date"]): case for case in evidence["cases"]}
    if not isinstance(selected, list) or len(selected) != len(cases):
        raise OrderRulePrepareError("prepare_case_denominator_mismatch")
    for item in selected:
        case = cases.get((item["security_id"], item["market_date"]))
        if case is None or case["security_code"] != item["security_code"]:
            raise OrderRulePrepareError("prepare_case_identity_mismatch")
        if digest(SamplingObservation.from_case(case)) != item["observation_digest"]:
            raise OrderRulePrepareError("prepare_case_observation_mismatch")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected_files),
        "source_isolation_verified": True,
        "old_cohort_size": summary["old_cohort_size"],
        "excluded_economic_identity_count": summary["excluded_economic_identity_count"],
        "candidate_population_count": summary["candidate_population_count"],
        "pool_size": summary["pool_size"],
        "selected_case_count": summary["selected_case_count"],
        "selection_digest": summary["selection_digest"],
        "selection_shortfalls": summary["selection_shortfalls"],
        "evidence_digest": summary["evidence_digest"],
        "evidence_source_row_count": summary["evidence_source_row_count"],
        "opinion_count": 0,
        "candidate_scored": False,
        "parameter_search": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "official_labels_emitted": False,
        "primary_stage_emitted": False,
        "review_ready": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_prepare(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_prepare_artifact(args.artifact, code_revision=args.code_revision)
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
