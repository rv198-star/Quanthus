from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.stage_learning import order_reference_feasibility as pilot
from quanthus.stage_learning.order_reference_protocol import PROTOCOL_DIGEST, REVIEW_SCHEMA
from quanthus.structure_v2_inputs import canonical, digest

from .test_structure_mvp_worker import _qualification_user
from .test_structure_order_worker import DispatchStore
from .test_structure_v2_inputs import row

CODE = "a" * 40
PREP_ID = "run_" + "b" * 32


def inputs():
    return {"code_revision": CODE, "run_config": pilot.CONFIG, "phase": "prepare"}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    values = []
    for code in ("000001", "300750", "600519"):
        for i in range(119):
            day = date(2020, 1, 2) + timedelta(days=i - 61)
            base = row(i, str(99 if i % 2 else 101), high=Decimal(106), low=Decimal(94))
            cutoff = base.cutoff.replace(year=day.year, month=day.month, day=day.day)
            values.append(replace(base, security_code=code, market_date=day, cutoff=cutoff))
    raw = b"".join(canonical(asdict(item)) + b"\n" for item in values)
    readiness = {"calendar_ref": "synthetic-session-calendar"}
    manifests = [
        {"files": {"source-inputs.jsonl": pilot.sha(raw),
                   "input-readiness.json": digest(readiness)},
         "run": str(i)} for i in range(2)
    ]
    source = {"source_inputs_digest": pilot.sha(raw),
              "manifest_sha256": list(map(digest, manifests))}
    binding = {"source_contract": source, "manifests": manifests, "readiness": readiness}
    monkeypatch.setattr(pilot, "source_contract", lambda: source)
    monkeypatch.setattr(pilot, "read_accepted_sources", lambda: (raw, binding))
    monkeypatch.setattr(pilot, "event_root", lambda: tmp_path)
    return values, raw, binding


def _prepare(tmp_path):
    target = tmp_path / "structure-runs" / PREP_ID
    pilot.prepare(target, inputs())
    return target, json.loads((target / "evidence.json").read_bytes())


def _opinions(evidence):
    source = pilot.load_config()["review"]
    out = []
    for case in evidence["cases"]:
        for role in ("short_term", "fund"):
            out.append({
                "schema_version": REVIEW_SCHEMA, "case_id": case["case_id"],
                "protocol_digest": PROTOCOL_DIGEST, "reviewer_id": "fixture-" + role,
                "role": role,
                "provenance": {k: source[k] for k in ("kind", "origin_id", "session_id")},
                "primary_window_sessions": 20, "recent_window_sessions": 10,
                "primary": {"stage_0": "FALSE", "stage_2": "FALSE"},
                "recent": {"width": "STABLE", "center": "STABLE"},
                "support_refs": [case["primary"]["range_width"]["revision_id"]],
                "contradiction_refs": [], "reason": "Synthetic fixture, not a market opinion.",
                "unknowns": [],
            })
    return out


def _resolve_input(root, evidence):
    return {**inputs(), "phase": "resolve", "prepared_run_id": PREP_ID,
            "prepared_manifest_sha256": (root / "READY").read_text().strip(),
            "opinions": _opinions(evidence)}


def _rebind(root, filename):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"][filename] = pilot.sha((root / filename).read_bytes())
    path.write_bytes(canonical(manifest))
    (root / "READY").write_text(pilot.sha(path.read_bytes()) + "\n")


def test_governed_config_and_selection_is_not_result_dependent(sources):
    rows, _, _ = sources
    cases = pilot.select_cases(rows)
    altered = [replace(r, high=Decimal(800), low=Decimal(1), close=Decimal(100)) for r in rows]
    assert pilot.select_cases(altered) == cases
    assert len(cases) == 12 and len(set(cases)) == 12
    assert _structure_input(inputs()) == inputs()
    assert _structure_request_digest(pilot.CONFIG) == pilot.CONFIG_DIGEST
    assert pilot.load_config()["authority"]["parameter_search"] is False


@pytest.mark.parametrize("patch", [
    {"phase": "tune"}, {"code_revision": "main"}, {"extra": True},
    {"run_config": "/tmp/arbitrary.json"}, {"opinions": []},
])
def test_invalid_prepare_input_is_rejected(patch):
    with pytest.raises((ValueError, RuntimeError)):
        _structure_input({**inputs(), **patch})


def test_prepare_cold_reconstruction_and_view_is_t_only(tmp_path, sources):
    root, evidence = _prepare(tmp_path)
    checked = pilot.verify_feasibility_artifact(
        root, code_revision=CODE, request_digest=pilot.CONFIG_DIGEST, job_input=inputs(),
    )
    assert checked["source_row_count"] == 357 and checked["case_count"] == 12
    assert checked["phase"] == "prepare"
    for view in checked["review_packets"]:
        assert len(view["history"]) == 61
        assert max(r[0] for r in view["history"]) <= view["case"]["market_date"]
        assert "stage_0" not in view["case"]["primary"]
    assert all(case["primary_available"] for case in evidence["cases"])
    with pytest.raises(FileExistsError):
        pilot.prepare(root, inputs())


def test_final_bundle_retains_all_raw_opinions_and_inconclusive_is_not_retried(tmp_path, sources):
    root, evidence = _prepare(tmp_path)
    job = _resolve_input(root, evidence)
    target = tmp_path / "resolved"
    pilot.resolve(target, job)
    result = pilot.verify_feasibility_artifact(
        target, code_revision=CODE, request_digest=pilot.CONFIG_DIGEST, job_input=job,
    )
    assert result["collection_disposition"] == "PASS_REAL_INPUT_REFERENCE_COLLECTION"
    assert result["coverage_disposition"] == "INCONCLUSIVE_REFERENCE_COVERAGE_NO_SUPPLEMENT"
    assert result["primary_reference_counts"] == {"NEITHER": 12}
    assert result["case_count"] == 12 and result["opinion_count"] == 24
    assert result["distinct_origin_count"] == 1
    assert not result["independent_validation"] and not result["market_reference_validated"]
    assert not result["parameter_search_authorized"]
    assert len(json.loads((target / "bundle/reviews.json").read_bytes())) == 24


def test_different_job_input_cannot_recover_wrong_sealed_result(tmp_path, sources):
    root, _ = _prepare(tmp_path)
    with pytest.raises(ValueError, match="job_input_binding"):
        pilot.verify_feasibility_artifact(
            root, code_revision=CODE, request_digest=pilot.CONFIG_DIGEST,
            job_input={**inputs(), "code_revision": "c" * 40},
        )


@pytest.mark.parametrize("change", ["raw_source", "selection", "extra_dir", "symlink"])
def test_rehashed_tampering_and_unsafe_inventory_are_rejected(tmp_path, sources, change):
    root, evidence = _prepare(tmp_path)
    if change == "raw_source":
        path = root / "source-inputs.jsonl"
        path.write_bytes(path.read_bytes().replace(b'"close":"101"', b'"close":"102"', 1))
        _rebind(root, path.name)
    elif change == "selection":
        evidence["cases"][0]["market_date"] = "2022-01-03"
        (root / "evidence.json").write_bytes(canonical(evidence))
        _rebind(root, "evidence.json")
    elif change == "extra_dir":
        (root / "hidden").mkdir()
    else:
        (root / "link").symlink_to(root / "evidence.json")
    with pytest.raises(ValueError):
        pilot.verify_feasibility_artifact(
            root, code_revision=CODE, request_digest=pilot.CONFIG_DIGEST,
        )


def test_forged_independent_origin_and_arbitrary_parent_rejected(tmp_path, sources):
    root, evidence = _prepare(tmp_path)
    job = _resolve_input(root, evidence)
    job["opinions"][0]["provenance"]["kind"] = "human"
    with pytest.raises(ValueError, match="provenance"):
        pilot.normalize_input(job)
    job = _resolve_input(root, evidence)
    job["prepared_run_id"] = "../../elsewhere"
    with pytest.raises(ValueError, match="parent_identity"):
        pilot.normalize_input(job)


def test_phase_parent_cannot_form_a_recursive_resolution_chain(tmp_path, sources):
    root, evidence = _prepare(tmp_path)
    job = _resolve_input(root, evidence)
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["job_input"]["phase"] = "resolve"
    (root / "manifest.json").write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="parent_must_be_prepare"):
        pilot.resolve(tmp_path / "bad", job)


def test_worker_dispatch_and_sealed_recovery_use_same_exact_dynamic_input(tmp_path, monkeypatch):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, _ = queue.submit(kind=STRUCTURE_MVP_JOB, actor_user_id=user.user_id,
                          idempotency_key="reference-pilot-prepare-fixture", input_ref=inputs())
    worker = EventWorker(store, worker_id="test", run_root=tmp_path,
                         lease_seconds=300, renew_seconds=30, terminate_seconds=1)
    commands = []

    def verify(root, *, code_revision, request_digest, job_input):
        assert code_revision == CODE and request_digest == pilot.CONFIG_DIGEST
        assert job_input == inputs()
        return {"artifact_ref": str(root), "row_count": 12, "p2_probe_count": 0,
                "p2_fill_count": 0, "phase": "prepare", "verified": True}

    class Child:
        returncode = 0

        def __init__(self, command, **_):
            commands.append(command)
            assert command[2] == "quanthus.stage_learning.order_reference_feasibility"
            assert json.loads(command[command.index("--job-input") + 1]) == inputs()
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module, "verify_feasibility_artifact", verify)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    assert store.get_job(job.job_id).state == "succeeded"
    assert len(commands) == 1
    observed = store.get_job(job.job_id)
    store.update_job(replace(observed, state="running"))
    worker._execute_structure(
        job.job_id, observed.claim_generation, inputs(), observed.input_digest,
    )
    assert store.get_job(job.job_id).state == "succeeded"
    assert len(commands) == 1
