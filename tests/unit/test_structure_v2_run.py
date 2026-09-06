from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date

import pytest

from quanthus.policy import find_project_root
from quanthus.structure_v2_artifact import FILES, verify_structure_v2_artifact
from quanthus.structure_v2_inputs import (
    InputPolicy,
    admit_daily,
    calculation_spec,
    canonical,
    digest,
)
from quanthus.structure_v2_run import REQUEST_PATH, load_request, run_from_admissions, write_result

from .test_structure_facts_run import _frames
from .test_structure_v2_inputs import provider, row

ROOT = find_project_root()
CODE = "b" * 40


def fixture():
    frames = _frames()
    request = load_request(ROOT / REQUEST_PATH)
    request.update(security_codes=["600001"], scan_start="2020-01-02",
                   report_start="2020-01-02", report_end="2020-01-24")
    policy = InputPolicy(digest(calculation_spec(request)), ("600001",), "calendar-v1")
    values = []
    for index, frame in enumerate(frames):
        bar = frame.decision_view.bar("600001")
        member = replace(row(index), security_code="600001", exchange="SSE",
                         high=bar.high_price, low=bar.low_price, close=bar.close_price,
                         volume=bar.volume)
        values.append(admit_daily(member, policy))
    return request, tuple(values), {frame.snapshot.market_date: frame for frame in frames}, policy


def publish(root):
    request, admissions, frames, policy = fixture()
    ready, structure, summary, audit, stages = run_from_admissions(
        request, admissions, frames, policy, provider(), code_revision=CODE,
    )
    runtime = {"provider_builds": 1, "cross_job_cache_hits": 0, "stages": stages}
    write_result(root, request, admissions, ready, structure, summary, audit, runtime)
    return request, summary, audit


def rehash(root):
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"] = {name: "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
                         for name in FILES}
    manifest["result_digest"] = digest(json.loads((root / "summary.json").read_bytes()))
    (root / "manifest.json").write_bytes(canonical(manifest))
    (root / "READY").write_bytes(canonical({
        "schema_version": "quanthus.structure-v2-ready.v1", "manifest_sha256": digest(manifest),
    }))


def test_full_synthetic_pipeline_semantics_repeat_but_actual_audit_times_differ(tmp_path):
    request, first, audit_a = publish(tmp_path / "A")
    _, second, audit_b = publish(tmp_path / "B")
    a = verify_structure_v2_artifact(
        tmp_path / "A", code_revision=CODE, request_digest=digest(request),
    )
    b = verify_structure_v2_artifact(
        tmp_path / "B", code_revision=CODE, request_digest=digest(request),
    )
    assert digest(first) == digest(second)
    assert a["structure_digest"] == b["structure_digest"]
    assert a["consumer_count"] == a["audit_event_count"] == 3
    assert a["p2_probe_count"] == a["p2_fill_count"] == 3
    assert canonical(audit_a) != canonical(audit_b)
    assert a["knowledge_view"] == "retrospective" and not a["execution_authorized"]


@pytest.mark.parametrize("target", [
    "business_extra_field", "audit_substitution", "partial_input", "review_substitution",
    "consumer_omission", "p2_fill_count",
])
def test_deep_verifier_rejects_hash_consistent_semantic_tampering(tmp_path, target):
    root = tmp_path / "artifact"
    request, _, _ = publish(root)
    if target == "business_extra_field":
        path = root / "structure.json"
        data = json.loads(path.read_bytes())
        data["measurements"][0]["coarse_state"] = "buy-now"
        path.write_bytes(canonical(data))
    elif target == "audit_substitution":
        path = root / "audit.json"
        data = json.loads(path.read_bytes())
        data["events"][0]["inputs"][0]["artifact_digest"] = "sha256:" + "f" * 64
        path.write_bytes(canonical(data))
    elif target in {"review_substitution", "consumer_omission", "p2_fill_count"}:
        path = root / "summary.json"
        data = json.loads(path.read_bytes())
        if target == "review_substitution":
            data["review_sample"][0]["frozen_level"] = "999"
        elif target == "p2_fill_count":
            data["p2_probes"][0]["fill_count"] += 1
        else:
            removed = data["consumer_decisions"].pop()
            audit_path = root / "audit.json"
            audit = json.loads(audit_path.read_bytes())
            audit["events"] = [event for event in audit["events"]
                               if event["decision_id"] != removed["decision_key"]]
            audit_path.write_bytes(canonical(audit))
        path.write_bytes(canonical(data))
    else:
        path = root / "source-inputs.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[20]["finality"] = "partial"
        path.write_bytes(b"".join(canonical(item) + b"\n" for item in rows))
    rehash(root)
    with pytest.raises(ValueError):
        verify_structure_v2_artifact(root, code_revision=CODE, request_digest=digest(request))


def test_extending_report_scope_does_not_change_algorithm_or_prior_fact_identity():
    request, admissions, frames, policy = fixture()
    earlier = dict(request, report_end="2020-01-23")
    assert digest(earlier) != digest(request)
    assert digest(calculation_spec(earlier)) == digest(calculation_spec(request))
    _, short, _, _, _ = run_from_admissions(
        earlier, tuple(item for item in admissions if item.row.market_date <= date(2020, 1, 23)),
        frames, policy, provider(), code_revision=CODE,
    )
    _, long, _, _, _ = run_from_admissions(
        request, admissions, frames, policy, provider(), code_revision=CODE,
    )
    assert [item.to_dict() for item in short.measurements] == [
        item.to_dict() for item in long.measurements if item.market_date <= date(2020, 1, 23)
    ]


def test_request_is_frozen_and_blocks_scope_and_policy_substitution(tmp_path):
    request = load_request(ROOT / REQUEST_PATH)
    assert request["lookbacks"] == [20, 60]
    assert date.fromisoformat(request["report_end"]) == date(2020, 3, 31)
    request["knowledge_view"] = "public_reconstruction"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError):
        load_request(path)
