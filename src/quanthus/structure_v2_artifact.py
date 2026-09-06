"""Bounded stdlib-only artifact verifier for the existing Event Worker.

No DWD access, provider opening or backtest import. Reconstruct only the small published daily
slice; validate exact typed business schema and audit membership, not just self-consistent hashes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.evidence_consumption import (
    ConsumedRevision,
    FactConsumptionEvent,
    GenerationReceipt,
    PublicationReceipt,
    validate_consumption_binding,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import (
    DailyInput,
    InputPolicy,
    admit_daily,
    build_readiness,
    calculation_spec,
    canonical,
    digest,
)
from quanthus.structure_v2_review import report_counts, select_review_sample
from quanthus.temporal_contracts import TemporalQuery

FILES = frozenset({"input-readiness.json", "source-inputs.jsonl", "structure.json",
                   "summary.json", "audit.json", "runtime.json"})
MANIFEST_SCHEMA = "quanthus.structure-v2-artifact.v1"


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _row(raw: dict[str, Any]) -> DailyInput:
    if set(raw) != {field.name for field in fields(DailyInput)}:
        raise ValueError("v2 source-member schema differs")
    data = dict(raw)
    data["market_date"] = date.fromisoformat(data["market_date"])
    data["cutoff"] = datetime.fromisoformat(data["cutoff"])
    for name in ("high", "low", "close", "volume"):
        data[name] = None if data[name] is None else Decimal(data[name])
    return DailyInput(**data)


def verify_structure_v2_artifact(
    root: Path, *, code_revision: str, request_digest: str,
) -> dict[str, Any]:
    root = root.resolve()
    if (not root.is_dir()
        or {path.name for path in root.iterdir()} != FILES | {"manifest.json", "READY"}):
        raise ValueError("v2 artifact file set differs")
    if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
        raise ValueError("v2 artifact contains indirect or non-file members")
    if sum(path.stat().st_size for path in root.iterdir()) > 64 * 1024 * 1024:
        raise ValueError("v2 bounded verification byte budget exceeded")
    manifest, ready = _load(root / "manifest.json"), _load(root / "READY")
    if set(manifest) != {"schema_version", "code_revision", "request_digest", "files",
                         "research_only", "execution_authorized", "result_digest",
                         "structure_digest", "input_readiness_digest"}:
        raise ValueError("v2 manifest schema differs")
    if (manifest["schema_version"] != MANIFEST_SCHEMA
        or manifest["code_revision"] != code_revision
        or manifest["request_digest"] != request_digest
        or manifest["research_only"] is not True or manifest["execution_authorized"] is not False
        or set(manifest["files"]) != FILES):
        raise ValueError("v2 manifest authority or input identity differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if ready != {"schema_version": "quanthus.structure-v2-ready.v1",
                 "manifest_sha256": manifest_sha}:
        raise ValueError("v2 READY binding differs")
    for name in FILES:
        actual = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"][name] != actual:
            raise ValueError("v2 artifact file hash differs")
    summary = _load(root / "summary.json")
    if set(summary) != {
        "schema_version", "request", "code_revision", "research_only", "execution_authorized",
        "economic_claim", "knowledge_view", "structure_digest", "input_readiness_digest",
        "adm_manifest", "report_counts", "consumer_decisions", "p2_probes", "review_sample",
    }:
        raise ValueError("v2 summary schema differs")
    request = summary["request"]
    if (digest(request) != request_digest or summary["code_revision"] != code_revision
        or summary["research_only"] is not True or summary["execution_authorized"] is not False
        or summary["economic_claim"] is not False or summary["knowledge_view"] != "retrospective"):
        raise ValueError("v2 summary scope/authority differs")
    if digest(summary) != manifest["result_digest"]:
        raise ValueError("v2 result semantic digest differs")
    readiness = _load(root / "input-readiness.json")
    if digest(readiness) != manifest["input_readiness_digest"]:
        raise ValueError("v2 source readiness digest differs")
    policy = InputPolicy(
        digest(calculation_spec(request)), tuple(request["security_codes"]),
        readiness["calendar_ref"],
    )
    source_lines = (root / "source-inputs.jsonl").read_text().splitlines()
    if len(source_lines) > 5000:
        raise ValueError("v2 source-row verification budget exceeded")
    source = tuple(_row(json.loads(line)) for line in source_lines)
    admissions = tuple(admit_daily(row, policy) for row in source)
    rebuilt_readiness = build_readiness(
        admissions, policy, readiness["provider_identity"],
        report_start=date.fromisoformat(request["report_start"]),
        report_end=date.fromisoformat(request["report_end"]),
    )
    if canonical(rebuilt_readiness) != canonical(readiness):
        raise ValueError("v2 readiness denominator or admission differs")
    structure = build_structure_v2(admissions, spec_digest=digest(calculation_spec(request)),
                                   calendar_ref=policy.calendar_ref,
                                   lookbacks=tuple(request["lookbacks"]))
    if (structure.digest != manifest["structure_digest"]
        or canonical(structure.semantic()) != (root / "structure.json").read_bytes()):
        raise ValueError("v2 causal reconstruction or exact business schema differs")
    expected_adm = {"schema_version": "quanthus.structure-v2-adm.v1",
                    "content_digest": structure.digest,
                    "spec_digest": digest(calculation_spec(request)),
                    "input_readiness_digest": digest(readiness), "research_only": True,
                    "execution_authorized": False, "knowledge_view": "retrospective"}
    if summary["adm_manifest"] != expected_adm:
        raise ValueError("v2 ADM manifest identity differs")
    if (canonical(report_counts(structure, request)) != canonical(summary["report_counts"])
        or canonical(select_review_sample(structure, request))
        != canonical(summary["review_sample"])):
        raise ValueError("v2 report denominator or preregistered blind sample differs")
    audit = _load(root / "audit.json")
    if set(audit) != {"schema_version", "generations", "publications", "events"}:
        raise ValueError("v2 audit schema differs")
    generations, publications = {}, {}
    for raw in audit["generations"]:
        raw = {**raw, "completed_at": datetime.fromisoformat(raw["completed_at"]),
               "output_revision_ids": tuple(raw["output_revision_ids"])}
        item = GenerationReceipt(**raw)
        if item.code_revision != code_revision or item.receipt_id in generations:
            raise ValueError("v2 generation code or identity differs")
        generations[item.receipt_id] = item
    for raw in audit["publications"]:
        item = PublicationReceipt(**{
            **raw, "servable_at": datetime.fromisoformat(raw["servable_at"]),
        })
        if (item.artifact_digest != manifest["files"]["structure.json"]
            or item.receipt_id in publications):
            raise ValueError("v2 published payload identity differs")
        publications[item.receipt_id] = item
    measurements = {item.identity.revision_id: item for item in structure.measurements}
    decisions = {item["decision_key"]: item for item in summary["consumer_decisions"]}
    if (len(decisions) != len(summary["consumer_decisions"])
        or len(audit["events"]) != len(decisions)):
        raise ValueError("v2 consumer/audit denominator differs")
    first, last = (date.fromisoformat(request[key]) for key in ("report_start", "report_end"))
    targets = {(item.security_code, item.market_date, item.scale, item.anchor_key)
               for item in structure.episodes if item.phase != "censored"
               and first <= item.market_date <= last}
    expected_revisions = {item.identity.revision_id for item in structure.measurements
                          if (item.security_code, item.market_date, item.scale,
                              item.anchor_key) in targets}
    if {item["revision_id"] for item in decisions.values()} != expected_revisions:
        raise ValueError("v2 consumer omitted or added qualified targets")
    observed_decisions = set()
    for raw in audit["events"]:
        raw = dict(raw)
        if raw.pop("schema_version") != "quanthus.fact-consumption-event.v1":
            raise ValueError("v2 consumption event schema differs")
        raw["query"] = TemporalQuery(**{
            **raw["query"],
            "knowledge_cutoff": datetime.fromisoformat(raw["query"]["knowledge_cutoff"]),
        })
        for name in ("consumed_at", "decision_at", "recorded_at"):
            raw[name] = datetime.fromisoformat(raw[name])
        raw["inputs"] = tuple(ConsumedRevision(**item) for item in raw["inputs"])
        event = FactConsumptionEvent(**raw)
        if (event.consumer_id != "expert.structure-v2-compatibility"
            or event.query.view != "retrospective"
            or event.query.purpose != "counterfactual_recomputation"):
            raise ValueError("v2 consumer/query authority differs")
        if len(event.inputs) != 1 or event.decision_id in observed_decisions:
            raise ValueError("v2 target invocation audit is not unique")
        observed_decisions.add(event.decision_id)
        item = event.inputs[0]
        measurement = measurements[item.revision_id]
        validate_consumption_binding(
            event, measurement.identity,
            generations[item.generation_ref], publications[item.publication_ref],
        )
        decision = decisions[event.decision_id]
        if decision["revision_id"] != item.revision_id:
            raise ValueError("v2 decision consumes a different revision")
        expected_score = max(Decimal(-1), min(Decimal(1), measurement.value))
        expected_action = "trial_entry" if expected_score >= 0 else "no_action"
        identity = measurement.identity
        expected_decision = {
            "decision_key": digest({"observation": identity.observation_key,
                                    "expert": "expert.structure-v2-compatibility"}),
            "security_code": measurement.security_code, "market_date": measurement.market_date,
            "scale": measurement.scale, "anchor_key": measurement.anchor_key,
            "observation_key": identity.observation_key, "revision_id": identity.revision_id,
            "spec_digest": measurement.spec_digest, "score": expected_score,
            "action": expected_action,
        }
        if canonical(decision) != canonical(expected_decision):
            raise ValueError("v2 direct consumer score/action/target does not reconcile")
    if observed_decisions != set(decisions):
        raise ValueError("v2 audited decisions differ")
    for probe in summary["p2_probes"]:
        if (digest(probe["result"]) != probe["result_digest"]
            or probe["fill_count"] != sum(event["event_type"] == "fill"
                                          for event in probe["result"]["events"])
            or probe["result"]["manifest"].get("data_manifest_sha256") != digest(expected_adm)):
            raise ValueError("v2 P2 result/ADM/fill evidence differs")
        decision = decisions[probe["decision_key"]]
        if (decision["action"] != "trial_entry" or probe["market_date"] != decision["market_date"]
            or probe["security_code"] != decision["security_code"]
            or probe["execution_date"] <= probe["market_date"]):
            raise ValueError("v2 P2 decision/session reconciliation differs")
    if len(summary["p2_probes"]) > request["maximum_p2_probes"]:
        raise ValueError("v2 P2 probe budget exceeded")
    runtime = _load(root / "runtime.json")
    if runtime["provider_builds"] != 1 or runtime["cross_job_cache_hits"] != 0:
        raise ValueError("v2 cold prerequisite build accounting differs")
    return {
        "artifact_ref": str(root), "manifest_sha256": manifest_sha,
        "result_digest": manifest["result_digest"], "structure_digest": structure.digest,
        "adm_manifest_digest": digest(expected_adm),
        "input_readiness_digest": manifest["input_readiness_digest"],
        "knowledge_view": "retrospective", "row_count": readiness["attempted"],
        "input_counts": {key: readiness[key] for key in ("attempted", "admitted", "rejected")},
        "fact_counts": summary["report_counts"], "consumer_count": len(decisions),
        "audit_event_count": len(audit["events"]),
        "p2_probe_count": len(summary["p2_probes"]),
        "p2_fill_count": sum(item["fill_count"] for item in summary["p2_probes"]),
        "review_sample": summary["review_sample"],
        "runtime": runtime, "execution_authorized": False,
    }
