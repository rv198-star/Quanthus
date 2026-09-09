"""Deep verifier for Stage-10/11 Projection Contract qualification artifacts.

The qualification reads only two previously accepted immutable Support Evolution/F9 artifacts.
It never opens DWD or a market-data provider. Verification reconstructs the source F9 facts from
published source rows, applies the formal projection contract, and compares every Stage-10/11
predicate with the retained research projection.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_projection import (
    load_stage_projection_contract,
    project_support_evolution_stages,
    projection_counts,
)
from quanthus.structure_f9_artifact import verify_structure_f9_artifact
from quanthus.structure_support_facts import build_support_failure_facts, support_fact_spec
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import (
    DailyInput,
    InputPolicy,
    admit_daily,
    calculation_spec,
    canonical,
    digest,
)

REQUEST_SCHEMA = "quanthus.stock-stage-projection-qualification.v1"
RESULT_SCHEMA = "quanthus.stock-stage-projection-qualification-result.v1"
MANIFEST_SCHEMA = "quanthus.stock-stage-projection-qualification-artifact.v1"
FILES = frozenset({"projection-a.json", "projection-b.json", "summary.json", "runtime.json"})


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _row(raw: dict[str, Any]) -> DailyInput:
    if set(raw) != {field.name for field in fields(DailyInput)}:
        raise ValueError("projection qualification source-member schema differs")
    data = dict(raw)
    data["market_date"] = date.fromisoformat(data["market_date"])
    data["cutoff"] = datetime.fromisoformat(data["cutoff"])
    for name in ("high", "low", "close", "volume"):
        data[name] = None if data[name] is None else Decimal(data[name])
    return DailyInput(**data)


def load_projection_qualification_request(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "qualification_id",
        "work_item",
        "projection_contract",
        "source_f9",
        "authority",
        "acceptance",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("projection qualification request fields differ")
    if (
        raw["schema_version"] != REQUEST_SCHEMA
        or raw["qualification_id"]
        != "stock-stage-10-11-projection-immutable-f9-reproduction-v1"
        or raw["work_item"] != "P3-STAGE-002-C"
        or raw["projection_contract"] != "config/stage-10-11-projection.v1.json"
    ):
        raise ValueError("projection qualification request identity differs")
    source = raw["source_f9"]
    if not isinstance(source, dict) or set(source) != {
        "code_revision",
        "request_digest",
        "support_facts_digest",
        "legacy_projection_digest",
        "run_ids",
        "expected_counts",
    }:
        raise ValueError("projection qualification source binding differs")
    run_ids = source["run_ids"]
    if (
        not isinstance(run_ids, list)
        or len(run_ids) != 2
        or len(set(run_ids)) != 2
        or any(
            not isinstance(item, str)
            or not item.startswith("run_")
            or len(item) != 36
            for item in run_ids
        )
    ):
        raise ValueError("projection qualification requires two exact source run ids")
    expected_counts = source["expected_counts"]
    if not isinstance(expected_counts, dict) or expected_counts != {
        "support_process_observation_count": 241,
        "stage_10_confirmation_count": 2,
        "stage_10_context_count": 10,
        "stage_11_rebound_condition_count": 6,
    }:
        raise ValueError("projection qualification expected counts differ")
    authority = raw["authority"]
    expected_authority = {
        "immutable_f9_artifact_read_authorized": True,
        "market_data_read_authorized": False,
        "dwd_provider_open_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "official_label_card_publication_authorized": False,
        "primary_stage_arbitration_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if authority != expected_authority:
        raise ValueError("projection qualification authority differs")
    acceptance = raw["acceptance"]
    if not isinstance(acceptance, dict) or set(acceptance) != {
        "source_artifacts_must_pass_original_f9_deep_verifier",
        "new_projection_digest_must_match_across_source_runs",
        "legacy_stage_10_predicate_must_match_every_observation",
        "legacy_stage_11_predicate_must_match_every_observation",
        "primary_stage_must_be_null_for_every_observation",
        "nested_candidate_count_must_equal_stage_11_condition_count",
        "no_market_data_or_provider_access",
    } or any(value is not True for value in acceptance.values()):
        raise ValueError("projection qualification acceptance contract differs")
    return raw


def _reconstruct_source_facts(source_root: Path) -> tuple[Any, list[dict[str, Any]]]:
    summary = _load(source_root / "summary.json")
    request = summary["request"]
    readiness = _load(source_root / "input-readiness.json")
    source_lines = (source_root / "source-inputs.jsonl").read_text(encoding="utf-8").splitlines()
    source_rows = tuple(_row(json.loads(line)) for line in source_lines)
    policy = InputPolicy(
        digest(calculation_spec(request)),
        tuple(request["security_codes"]),
        readiness["calendar_ref"],
    )
    admissions = tuple(admit_daily(row, policy) for row in source_rows)
    structure = build_structure_v2(
        admissions,
        spec_digest=digest(calculation_spec(request)),
        calendar_ref=policy.calendar_ref,
        lookbacks=tuple(request["lookbacks"]),
    )
    facts = build_support_failure_facts(
        admissions,
        structure,
        spec_digest=digest(support_fact_spec(request)),
        maximum_sessions=int(request["support_horizon_sessions"]),
    )
    legacy = _load(source_root / "projection.json")
    if not isinstance(legacy, list):
        raise ValueError("legacy projection artifact must be an array")
    return facts, legacy


def _event_root(value: Path | None = None) -> Path:
    root = value or Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
    return root.resolve()


def build_projection_qualification(
    request: dict[str, Any],
    *,
    event_root: Path | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    project_root = find_project_root()
    contract = load_stage_projection_contract(project_root / request["projection_contract"])
    source = request["source_f9"]
    run_root = _event_root(event_root) / "structure-runs"
    projection_payloads: list[bytes] = []
    source_results: list[dict[str, Any]] = []

    for run_id in source["run_ids"]:
        root = (run_root / run_id).resolve()
        if root.parent != run_root.resolve():
            raise ValueError("projection source run escapes governed event root")
        verified = verify_structure_f9_artifact(
            root,
            code_revision=source["code_revision"],
            request_digest=source["request_digest"],
        )
        if (
            verified["support_facts_digest"] != source["support_facts_digest"]
            or verified["projection_digest"] != source["legacy_projection_digest"]
        ):
            raise ValueError("projection source F9 identity differs")
        facts, legacy = _reconstruct_source_facts(root)
        if facts.digest != source["support_facts_digest"]:
            raise ValueError("reconstructed Support Evolution Facts differ")
        formal = project_support_evolution_stages(facts, contract)
        formal_by_key = {
            (item.process_id, item.market_date.isoformat()): item for item in formal.observations
        }
        legacy_by_key = {
            (str(item["process_id"]), str(item["market_date"])): item for item in legacy
        }
        if set(formal_by_key) != set(legacy_by_key):
            raise ValueError("formal and legacy projection observation grains differ")
        stage10_mismatch = 0
        stage11_mismatch = 0
        for key, observed in formal_by_key.items():
            previous = legacy_by_key[key]
            if observed.stage_10_context_active is not bool(previous["stage_10_active"]):
                stage10_mismatch += 1
            if observed.stage_11_rebound_condition_active is not bool(previous["stage_11_active"]):
                stage11_mismatch += 1
        if stage10_mismatch or stage11_mismatch:
            raise ValueError("formal projection changes retained Stage-10/11 predicates")
        counts = projection_counts(formal)
        expected = source["expected_counts"]
        if (
            len(facts.observations) != expected["support_process_observation_count"]
            or counts["stage_10_confirmation_count"] != expected["stage_10_confirmation_count"]
            or counts["stage_10_context_count"] != expected["stage_10_context_count"]
            or counts["stage_11_rebound_condition_count"]
            != expected["stage_11_rebound_condition_count"]
            or counts["nested_10_11_candidate_count"]
            != counts["stage_11_rebound_condition_count"]
            or counts["primary_stage_non_null_count"] != 0
        ):
            raise ValueError("formal projection counts violate frozen acceptance")
        payload = canonical(formal.semantic())
        projection_payloads.append(payload)
        source_results.append(
            {
                "run_id": run_id,
                "source_manifest_sha256": verified["manifest_sha256"],
                "support_facts_digest": facts.digest,
                "legacy_projection_digest": verified["projection_digest"],
                "formal_projection_digest": formal.digest,
                "counts": counts,
                "legacy_stage_10_predicate_mismatch_count": stage10_mismatch,
                "legacy_stage_11_predicate_mismatch_count": stage11_mismatch,
            }
        )

    formal_digests = {item["formal_projection_digest"] for item in source_results}
    if len(formal_digests) != 1 or projection_payloads[0] != projection_payloads[1]:
        raise ValueError("formal projection is not reproducible across accepted F9 cold runs")
    formal_digest = next(iter(formal_digests))
    summary = {
        "schema_version": RESULT_SCHEMA,
        "qualification_id": request["qualification_id"],
        "projection_id": contract.projection_id,
        "projection_version": contract.version,
        "projection_contract_digest": contract.contract_digest,
        "source_support_facts_digest": source["support_facts_digest"],
        "source_legacy_projection_digest": source["legacy_projection_digest"],
        "formal_projection_digest": formal_digest,
        "source_results": source_results,
        "source_run_count": 2,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "primary_stage_authorized": False,
        "official_label_card_authorized": False,
        "decision": "PASS_FORMAL_PROJECTION_CONTRACT",
    }
    runtime = {
        "schema_version": "quanthus.stock-stage-projection-qualification-runtime.v1",
        "source_artifact_reads": 2,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "cross_job_cache_hits": 0,
        "source_kind": "immutable_accepted_f9_artifacts",
    }
    payloads = {
        "projection-a.json": projection_payloads[0],
        "projection-b.json": projection_payloads[1],
        "summary.json": canonical(summary),
        "runtime.json": canonical(runtime),
    }
    return payloads, summary


def verify_stage_projection_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    root = root.resolve()
    expected = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {path.name for path in root.iterdir()} != expected:
        raise ValueError("stage projection artifact file set differs")
    if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
        raise ValueError("stage projection artifact contains indirect members")
    manifest = _load(root / "manifest.json")
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "code_revision",
        "request_digest",
        "files",
        "result_digest",
        "formal_projection_digest",
        "projection_contract_digest",
        "source_support_facts_digest",
        "research_only",
        "execution_authorized",
    }:
        raise ValueError("stage projection manifest schema differs")
    if (
        manifest["schema_version"] != MANIFEST_SCHEMA
        or manifest["code_revision"] != code_revision
        or manifest["request_digest"] != request_digest
        or manifest["research_only"] is not True
        or manifest["execution_authorized"] is not False
        or set(manifest["files"]) != FILES
    ):
        raise ValueError("stage projection manifest authority or identity differs")
    for name in FILES:
        observed = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"][name] != observed:
            raise ValueError("stage projection artifact file digest differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if _load(root / "READY") != {
        "schema_version": "quanthus.stock-stage-projection-qualification-ready.v1",
        "manifest_sha256": manifest_sha,
    }:
        raise ValueError("stage projection READY binding differs")
    summary = _load(root / "summary.json")
    if digest(summary) != manifest["result_digest"]:
        raise ValueError("stage projection result digest differs")
    project_root = find_project_root()
    request = load_projection_qualification_request(
        project_root / "config/stage-10-11-projection-qualification.v1.json"
    )
    if digest(request) != request_digest:
        raise ValueError("stage projection request digest differs")
    rebuilt, expected_summary = build_projection_qualification(request)
    for name in FILES:
        if rebuilt[name] != (root / name).read_bytes():
            raise ValueError("stage projection deep reconstruction differs")
    if summary != expected_summary:
        raise ValueError("stage projection summary reconstruction differs")
    if (
        manifest["formal_projection_digest"] != summary["formal_projection_digest"]
        or manifest["projection_contract_digest"] != summary["projection_contract_digest"]
        or manifest["source_support_facts_digest"] != summary["source_support_facts_digest"]
    ):
        raise ValueError("stage projection manifest semantic binding differs")
    runtime = _load(root / "runtime.json")
    if runtime["market_data_reads"] != 0 or runtime["dwd_provider_opens"] != 0:
        raise ValueError("stage projection qualification accessed market data")
    counts = summary["source_results"][0]["counts"]
    return {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "result_digest": manifest["result_digest"],
        "formal_projection_digest": summary["formal_projection_digest"],
        "projection_contract_digest": summary["projection_contract_digest"],
        "source_support_facts_digest": summary["source_support_facts_digest"],
        "source_run_count": summary["source_run_count"],
        "row_count": counts["observation_count"],
        "stage_10_confirmation_count": counts["stage_10_confirmation_count"],
        "stage_10_context_count": counts["stage_10_context_count"],
        "stage_11_rebound_condition_count": counts["stage_11_rebound_condition_count"],
        "nested_10_11_candidate_count": counts["nested_10_11_candidate_count"],
        "primary_stage_non_null_count": counts["primary_stage_non_null_count"],
        "legacy_predicate_compatibility": True,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "execution_authorized": False,
        "official_label_card_authorized": False,
    }
