"""Deep qualification/verifier for the Structure Order Fact Family.

Qualification consumes only two previously accepted immutable F9 artifacts and never opens DWD or
a market-data provider. Each source artifact first passes its original deep verifier.
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
from quanthus.structure_f9_artifact import verify_structure_f9_artifact
from quanthus.structure_order_facts import (
    StructureOrderFacts,
    build_structure_order_facts,
    structure_order_counts,
)
from quanthus.structure_v2_inputs import (
    DailyInput,
    InputPolicy,
    admit_daily,
    calculation_spec,
    canonical,
    digest,
)

REQUEST_SCHEMA = "quanthus.structure-order-qualification.v1"
RESULT_SCHEMA = "quanthus.structure-order-qualification-result.v1"
MANIFEST_SCHEMA = "quanthus.structure-order-qualification-artifact.v1"
FILES = frozenset(
    {"structure-order-a.json", "structure-order-b.json", "summary.json", "runtime.json"}
)


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _row(raw: dict[str, Any]) -> DailyInput:
    if set(raw) != {field.name for field in fields(DailyInput)}:
        raise ValueError("Structure Order source-member schema differs")
    data = dict(raw)
    data["market_date"] = date.fromisoformat(data["market_date"])
    data["cutoff"] = datetime.fromisoformat(data["cutoff"])
    for name in ("high", "low", "close", "volume"):
        data[name] = None if data[name] is None else Decimal(data[name])
    return DailyInput(**data)


def load_structure_order_contract(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "fact_family_id",
        "display_name_zh",
        "display_name_en",
        "work_item",
        "purpose",
        "source_profile",
        "reused_fact_family",
        "concepts",
        "semantics",
        "authority",
        "claim_limits",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Structure Order contract fields differ")
    if (
        raw["schema_version"] != "quanthus.structure-order-fact-family.v1"
        or raw["fact_family_id"] != "structure-order-v1"
        or raw["display_name_zh"] != "结构秩序事实族"
        or raw["display_name_en"] != "Structure Order Fact Family"
        or raw["work_item"] != "P3-STRUCT-007-A"
        or raw["source_profile"] != "config/stage-learning/third-group-profile.v1.json"
        or raw["reused_fact_family"] != "config/structure-trend-evolution.v1.json"
    ):
        raise ValueError("Structure Order contract identity differs")
    if raw["concepts"] != {
        "structure.geometry.range-width": {"windows": [10, 20, 60]},
        "structure.geometry.width-change": {
            "windows": [10, 20],
            "comparison": "immediately_prior_equal_nonoverlap_window",
        },
        "structure.geometry.range-center-shift": {
            "windows": [10, 20],
            "comparison": "immediately_prior_equal_nonoverlap_window",
        },
    }:
        raise ValueError("Structure Order concept/window contract differs")
    expected_semantics = {
        "current_windows_include_t": True,
        "same_listing_coordinate_required": True,
        "hidden_session_gap_resets_history": True,
        "nonadmitted_row_resets_history": True,
        "range_width_uses_midpoint_normalization": True,
        "width_change_prior_zero_width_is_unavailable": True,
        "center_shift_remains_available_when_prior_width_is_zero": True,
        "directional_efficiency_is_reused_not_redefined": True,
        "order_or_disorder_label_forbidden": True,
        "stage_thresholds_in_fact_identity": False,
    }
    if raw["semantics"] != expected_semantics:
        raise ValueError("Structure Order semantic invariants differ")
    expected_authority = {
        "formal_structure_fact_implementation_authorized": True,
        "immutable_accepted_source_artifact_qualification_authorized": True,
        "market_data_read_authorized": False,
        "dwd_provider_open_authorized": False,
        "stage_projection_authorized": False,
        "parameter_search_authorized": False,
        "official_label_card_publication_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if raw["authority"] != expected_authority:
        raise ValueError("Structure Order contract authority differs")
    return raw


def load_structure_order_qualification(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "qualification_id",
        "work_item",
        "fact_contract",
        "source_f9",
        "scope",
        "authority",
        "acceptance",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Structure Order qualification fields differ")
    if (
        raw["schema_version"] != REQUEST_SCHEMA
        or raw["qualification_id"] != "structure-order-facts-from-accepted-f9-source-artifacts-v1"
        or raw["work_item"] != "P3-STRUCT-007-C"
        or raw["fact_contract"] != "config/structure-order-facts.v1.json"
    ):
        raise ValueError("Structure Order qualification identity differs")
    source = raw["source_f9"]
    expected_source = {
        "code_revision": "44571610b1da1792a80bbd45ee7f180d59e8495a",
        "request_digest": "sha256:33ae7300f6b4ab61e32d0f84bbf94739c7f78f220bd424bed945c888aaa88849",
        "source_inputs_digest": (
            "sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac"
        ),
        "run_ids": [
            "run_237a3e986b49490381f380a7ed5d4d14",
            "run_f371d3f2cb0440aea2c49ceaad1c237b",
        ],
        "manifest_sha256": [
            "sha256:88a2a1a57d2cef4d3f75b55c36ded41d42b737082644c8232eeecf6497de3c90",
            "sha256:429d298651e7d6596c4490b0e2a1b3c359c26e5d8081e918392450aafa944373",
        ],
    }
    if source != expected_source:
        raise ValueError("Structure Order frozen source identity differs")
    if raw["scope"] != {
        "report_start": "2020-01-02",
        "report_end": "2020-03-31",
        "security_codes": ["000001", "300750", "600519"],
        "minimum_contiguous_history_sessions": 60,
        "review_sample_limit": 12,
    }:
        raise ValueError("Structure Order qualification scope differs")
    expected_authority = {
        "immutable_f9_artifact_read_authorized": True,
        "market_data_read_authorized": False,
        "dwd_provider_open_authorized": False,
        "stage_projection_authorized": False,
        "parameter_search_authorized": False,
        "official_label_card_publication_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if raw["authority"] != expected_authority:
        raise ValueError("Structure Order qualification authority differs")
    if not isinstance(raw["acceptance"], dict) or any(
        value is not True for value in raw["acceptance"].values()
    ):
        raise ValueError("Structure Order qualification acceptance differs")
    return raw


def _event_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))).resolve()


def _source_admissions(source_root: Path) -> tuple[tuple[Any, ...], bytes]:
    summary = _load(source_root / "summary.json")
    request = summary["request"]
    readiness = _load(source_root / "input-readiness.json")
    source_bytes = (source_root / "source-inputs.jsonl").read_bytes()
    rows = tuple(
        _row(json.loads(line))
        for line in source_bytes.decode("utf-8").splitlines()
        if line.strip()
    )
    policy = InputPolicy(
        digest(calculation_spec(request)),
        tuple(request["security_codes"]),
        readiness["calendar_ref"],
    )
    return tuple(admit_daily(row, policy) for row in rows), source_bytes


def _review_sample(facts: StructureOrderFacts, *, limit: int, seed: str) -> list[dict[str, Any]]:
    by_slot: dict[tuple[str, str], list[Any]] = {}
    for item in facts.measurements:
        by_slot.setdefault((item.security_code, item.market_date.isoformat()), []).append(item)
    ranked = sorted(
        by_slot,
        key=lambda key: digest({"seed": seed, "security_code": key[0], "market_date": key[1]}),
    )[:limit]
    output = []
    for key in ranked:
        values = {}
        for item in sorted(
            by_slot[key],
            key=lambda value: (
                value.concept_id,
                value.window_sessions,
                value.comparison_window_sessions or 0,
            ),
        ):
            suffix = str(item.window_sessions)
            if item.comparison_window_sessions is not None:
                suffix += f"_vs_{item.comparison_window_sessions}"
            values[f"{item.concept_id}:{suffix}"] = (
                None if item.value is None else format(item.value, "f")
            )
        output.append({"security_code": key[0], "market_date": key[1], "values": values})
    return output


def build_structure_order_qualification(
    request: dict[str, Any], *, event_root: Path | None = None
) -> tuple[dict[str, bytes], dict[str, Any]]:
    project_root = find_project_root()
    contract = load_structure_order_contract(project_root / request["fact_contract"])
    source = request["source_f9"]
    run_root = _event_root(event_root) / "structure-runs"
    report_start = date.fromisoformat(request["scope"]["report_start"])
    report_end = date.fromisoformat(request["scope"]["report_end"])
    payloads: list[bytes] = []
    source_rows: list[bytes] = []
    results: list[dict[str, Any]] = []
    for index, run_id in enumerate(source["run_ids"]):
        root = (run_root / run_id).resolve()
        if root.parent != run_root.resolve():
            raise ValueError("Structure Order source run escapes governed event root")
        verified = verify_structure_f9_artifact(
            root,
            code_revision=source["code_revision"],
            request_digest=source["request_digest"],
        )
        if verified["manifest_sha256"] != source["manifest_sha256"][index]:
            raise ValueError("Structure Order source manifest differs")
        admissions, source_bytes = _source_admissions(root)
        if "sha256:" + hashlib.sha256(source_bytes).hexdigest() != source["source_inputs_digest"]:
            raise ValueError("Structure Order source-input payload differs")
        facts = build_structure_order_facts(
            admissions,
            report_start=report_start,
            report_end=report_end,
        )
        fact_payload = canonical(facts.semantic())
        payloads.append(fact_payload)
        source_rows.append(source_bytes)
        results.append(
            {
                "run_id": run_id,
                "source_manifest_sha256": verified["manifest_sha256"],
                "structure_order_digest": facts.digest,
                "counts": structure_order_counts(facts),
                "review_sample": _review_sample(
                    facts,
                    limit=int(request["scope"]["review_sample_limit"]),
                    seed=request["qualification_id"],
                ),
            }
        )
    if source_rows[0] != source_rows[1]:
        raise ValueError("accepted F9 source-input payloads are not byte-identical")
    if (
        payloads[0] != payloads[1]
        or results[0]["structure_order_digest"] != results[1]["structure_order_digest"]
    ):
        raise ValueError("Structure Order Facts are not reproducible across accepted source runs")
    if results[0]["review_sample"] != results[1]["review_sample"]:
        raise ValueError("Structure Order deterministic review sample differs across source runs")
    summary = {
        "schema_version": RESULT_SCHEMA,
        "qualification_id": request["qualification_id"],
        "fact_family_id": contract["fact_family_id"],
        "fact_contract_digest": digest(contract),
        "source_inputs_digest": source["source_inputs_digest"],
        "structure_order_digest": results[0]["structure_order_digest"],
        "source_run_count": 2,
        "source_results": results,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "stage_projection_authorized": False,
        "parameter_search_authorized": False,
        "official_label_card_authorized": False,
        "decision": "PASS_BOUNDED_STRUCTURE_ORDER_FACT_FAMILY",
    }
    runtime = {
        "schema_version": "quanthus.structure-order-qualification-runtime.v1",
        "source_artifact_reads": 2,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "source_kind": "immutable_accepted_f9_source_artifacts",
    }
    return {
        "structure-order-a.json": payloads[0],
        "structure-order-b.json": payloads[1],
        "summary.json": canonical(summary),
        "runtime.json": canonical(runtime),
    }, summary


def verify_structure_order_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    expected = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {path.name for path in root.iterdir()} != expected:
        raise ValueError("Structure Order artifact file set differs")
    if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
        raise ValueError("Structure Order artifact contains indirect members")
    manifest = _load(root / "manifest.json")
    expected_manifest = {
        "schema_version",
        "code_revision",
        "request_digest",
        "research_only",
        "execution_authorized",
        "fact_contract_digest",
        "structure_order_digest",
        "source_inputs_digest",
        "result_digest",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest:
        raise ValueError("Structure Order manifest schema differs")
    if (
        manifest["schema_version"] != MANIFEST_SCHEMA
        or manifest["code_revision"] != code_revision
        or manifest["request_digest"] != request_digest
        or manifest["research_only"] is not True
        or manifest["execution_authorized"] is not False
        or set(manifest["files"]) != FILES
    ):
        raise ValueError("Structure Order manifest authority or identity differs")
    for name in FILES:
        observed = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"][name] != observed:
            raise ValueError("Structure Order artifact file digest differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if _load(root / "READY") != {
        "schema_version": "quanthus.structure-order-qualification-ready.v1",
        "manifest_sha256": manifest_sha,
    }:
        raise ValueError("Structure Order READY binding differs")
    project_root = find_project_root()
    request = load_structure_order_qualification(
        project_root / "config/structure-order-qualification.v1.json"
    )
    if digest(request) != request_digest:
        raise ValueError("Structure Order request digest differs")
    rebuilt, expected_summary = build_structure_order_qualification(request)
    for name in FILES:
        if rebuilt[name] != (root / name).read_bytes():
            raise ValueError("Structure Order deep reconstruction differs")
    summary = _load(root / "summary.json")
    if summary != expected_summary or digest(summary) != manifest["result_digest"]:
        raise ValueError("Structure Order summary/result digest differs")
    if (
        manifest["fact_contract_digest"] != summary["fact_contract_digest"]
        or manifest["structure_order_digest"] != summary["structure_order_digest"]
        or manifest["source_inputs_digest"] != summary["source_inputs_digest"]
    ):
        raise ValueError("Structure Order manifest semantic binding differs")
    runtime = _load(root / "runtime.json")
    if runtime["market_data_reads"] != 0 or runtime["dwd_provider_opens"] != 0:
        raise ValueError("Structure Order qualification accessed market data")
    counts = summary["source_results"][0]["counts"]
    return {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "fact_contract_digest": summary["fact_contract_digest"],
        "structure_order_digest": summary["structure_order_digest"],
        "source_inputs_digest": summary["source_inputs_digest"],
        "result_digest": manifest["result_digest"],
        "source_run_count": 2,
        "row_count": counts["available_observation_count"],
        "counts": counts,
        "review_sample": summary["source_results"][0]["review_sample"],
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "stage_projection_authorized": False,
        "official_label_card_authorized": False,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "execution_authorized": False,
    }
