import io
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus import r9_calibration as cal
from quanthus import r9_study as study
from quanthus.stage_learning import head2_revision as hr


def pool_rows():
    rows = []
    groups = (
        ("train", "2020-03-31"),
        ("train", "2020-04-30"),
        ("guard", "2020-07-31"),
        ("update", "2020-08-31"),
        ("evaluation", "2020-11-30"),
        ("evaluation", "2020-12-31"),
    )
    for group, (role, anchor) in enumerate(groups):
        end = date.fromisoformat(anchor)
        for i in range(256):
            key = f"synthetic-{group}-{i}"
            sample = key + ("-positive" if i < 32 else "-negative")
            width, change = 0.03 + i / 1000, -0.4 if i < 128 else 0.3 + i / 1000
            identity = {
                k: key + k for k in ("company_id", "security_id", "listing_id", "episode_id")
            }
            identity.update(
                role=role,
                anchor=anchor,
                sample_id=sample,
                sessions=[
                    {"date": (end - timedelta(days=39 - j)).isoformat(), "session_id": key + str(j)}
                    for j in range(40)
                ],
            )
            rows.append(
                {
                    "identity": identity,
                    "available": True,
                    "reason": "available",
                    "features": [0.1, width, change, 0.01, 0.2, 0.5, 0.0],
                    "primary_facts": {
                        k: {
                            "value": str(v),
                            "reason": "available",
                            "revision_id": "sha256:" + "a" * 64,
                        }
                        for k, v in {
                            "directional_efficiency": 0.1,
                            "range_width": width,
                            "width_change": change,
                            "center_shift": 0.01,
                        }.items()
                    },
                    "packet": {
                        "case_id": sample,
                        "available": True,
                        "HLC40": [["102.0000", "98.0000", "100.0000"]] * 40,
                    },
                }
            )
    return rows


@pytest.fixture
def study_request(tmp_path):
    pool, excluded, calibration = (
        tmp_path / name for name in ("pool.json", "excluded.json", "calibration.json")
    )
    cal.write(pool, {"rows": pool_rows()})
    cal.write(excluded, {"rows": []})
    cal.write(calibration, {"panel_qualified": True})
    path = tmp_path / "request.json"
    cal.write(
        path,
        {
            "schema_version": study.SCHEMA,
            "maximum_calls": 128,
            "learning": {**asdict(hr.revision_policy()), "raw_threshold": 0.5},
            "selection": {"layout": study.LAYOUT},
            "implementation_bindings": {},
            "source": {
                "pool_path": str(pool),
                "pool_sha256": cal.sha(pool),
                "exclusions": [{"path": str(excluded), "sha256": cal.sha(excluded)}],
            },
            "calibration": {
                "summary_path": str(calibration),
                "summary_sha256": cal.sha(calibration),
            },
            "output_root": str(tmp_path / "study"),
            "runtime": {
                "reference_deadline": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                "total_deadline": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
                "cpu_cores": 2,
                "rss_bytes": 2**31,
                "tmp_bytes": 2**30,
                "output_bytes": 2**26,
            },
        },
    )
    study.prepare(path)
    return path


class FakeGuard:
    def __init__(self, runtime, output, sandbox):
        import threading

        self.lock, self.done, self.stopped = threading.Lock(), threading.Event(), threading.Event()
        self.reason, self.processes, self.peak_rss, self.affinity = "", [], 0, [0, 1]
        self.thread = threading.Thread(target=lambda: None)

    def stop(self, reason):
        self.reason = reason
        self.stopped.set()


def fake_process(command, **kwargs):
    prompt = command[-1]
    packet = json.loads(prompt.split("Packet: ")[1])
    reader = "reader_a" if "reader=reader_a" in prompt else "reader_b"
    label = "TRUE" if packet["case_id"].endswith("positive") else "FALSE"
    cites = [
        {
            "offset": i,
            "field": "H",
            "value": packet["HLC40"][i - 1][0],
            "reason": "synthetic contract evidence",
        }
        for i in (1, 21)
    ]
    opinion = {
        "case_id": packet["case_id"],
        "reader": reader,
        "label": label,
        "clauses": {
            c: {
                "label": label,
                "support": cites,
                "counterevidence": cites,
                "rationale": "synthetic path",
            }
            for c in cal.CLAUSES
        },
    }
    cal.write(Path(command[command.index("-o") + 1]), opinion)
    events = [
        {"type": "thread.started", "thread_id": "synthetic"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(opinion)}},
        {"type": "turn.completed", "usage": {}},
    ]

    class Process:
        returncode, pid = 0, 123
        stdout, stderr = io.BytesIO("\n".join(map(json.dumps, events)).encode()), io.BytesIO(b"")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    return Process()


def references(study_request, monkeypatch):
    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    monkeypatch.setattr(cal.subprocess, "Popen", fake_process)
    root = study.root_for(study_request)
    for role in study.ROLES:
        receipt = study.collect_role(study_request, role)
        assert receipt["calls"] == receipt["valid"] == 2 * study.CAPS[role]
        raw = root / f"reference-{role}/references.sealed.json"
        qa = root / f"semantic-qa-{role}.json"
        cal.write(
            qa,
            {
                "request_sha256": cal.sha(root / f"request-{role}.json"),
                "selected_sha256": cal.sha(root / f"selection-{role}/selected.private.json"),
                "opinions_sha256": cal.sha(raw),
                "reviewer": "synthetic independent test",
                "inspected_opinions": [
                    {"case_id": r["case_id"], "reader": r["reader"]}
                    for r in study.read(raw)["opinions"]
                ],
                "excluded": [],
            },
        )
        assert study.review_role(study_request, role, qa)["support_pass"]


def test_selection_isolated_august_roles_and_boundary_challenge(study_request):
    rows = study.selected_rows(study_request)
    assert len(rows) == len({r["identity"]["company_id"] for r in rows}) == 64
    assert {
        r["identity"]["anchor"] for r in rows if r["identity"]["role"] in ("guard", "update")
    } == {"2020-08-31"}
    assert sum(r["sampling_proxy"] == "boundary" for r in rows) == 20
    with pytest.raises(ValueError, match="FRESH_OUTPUT"):
        study.prepare(study_request)


def test_source_change_and_shortage_cannot_select_replacements(study_request):
    c = study.read(study_request)
    pool = Path(c["source"]["pool_path"])
    pool.write_text(pool.read_text() + " ")
    assert cal.sha(pool) != c["source"]["pool_sha256"]
    with pytest.raises(ValueError, match="FILE_BINDING"):
        study.bound(pool, c["source"]["pool_sha256"])
    with pytest.raises(ValueError, match="CAPACITY_STOP"):
        study.select_rows(pool_rows(), {r["identity"]["company_id"] for r in pool_rows()})


def test_missing_prior_gate_stops_before_next_role_attempt(study_request, monkeypatch):
    monkeypatch.setattr(cal.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(FileNotFoundError):
        study.collect_role(study_request, "evaluation")
    assert not (study.root_for(study_request) / "attempt-evaluation.json").exists()


def test_fit_seals_predictions_before_any_evaluation_label_read(study_request, monkeypatch):
    references(study_request, monkeypatch)
    original = study.resolved_role
    opened = []

    def read_role(path, role):
        if role == "evaluation":
            assert (study.root_for(path) / "fit/manifest.json").is_file()
        opened.append(role)
        return original(path, role)

    monkeypatch.setattr(study, "resolved_role", read_role)
    fitted = study.fit(study_request)
    assert fitted["fit_runs"] == 1 and "evaluation" not in opened
    compared = study.compare(study_request)
    assert "evaluation" in opened and compared["subjects"] == 64
    assert compared["evaluation_definite"] == 24
    assert sum(compared["metrics"]["v0"].values()) == 24
    assert compared["probability"] is None
    with pytest.raises(FileExistsError):
        study.fit(study_request)
    with pytest.raises(FileExistsError):
        study.compare(study_request)


def test_tampered_predictions_rejected_before_evaluation_unblind(study_request, monkeypatch):
    references(study_request, monkeypatch)
    study.fit(study_request)
    p = study.root_for(study_request) / "fit/predictions.sealed.json"
    p.write_text(p.read_text() + " ")
    monkeypatch.setattr(study, "resolved_role", lambda *a: pytest.fail("must not read labels"))
    with pytest.raises(ValueError, match="COMMITMENT_TAMPERED"):
        study.compare(study_request)
    assert not (p.parent / "evaluation-unblind.attempt.json").exists()


def test_confusion_uses_frozen_half_threshold_and_unknown_is_external():
    rows = [
        {"label": "TRUE", "v0": 0.5},
        {"label": "TRUE", "v0": 0.49},
        {"label": "FALSE", "v0": 0.8},
        {"label": "FALSE", "v0": 0.1},
    ]
    assert study.confusion(rows, "v0") == {"TP": 1, "TN": 1, "FP": 1, "FN": 1}
    assert study.confusion([{"label": "TRUE", "v1": None}], "v1") is None


def test_prior_gate_receipt_is_bound_into_role_contract(study_request, monkeypatch):
    references(study_request, monkeypatch)
    root = study.root_for(study_request)
    prior = root / "support-guard.safe.json"
    prior.write_text(prior.read_text() + " ")
    with pytest.raises(ValueError, match="FILE_BINDING"):
        study.reference_payload(root / "request-evaluation.json")


def test_cold_cannot_consume_attempt_before_primary_comparison(study_request):
    with pytest.raises(FileNotFoundError):
        study.cold(study_request)
    assert not (study.root_for(study_request) / "cold").exists()


def test_whole_job_output_budget_includes_previous_roles(tmp_path, monkeypatch):
    import threading

    root = tmp_path / "job"
    current = root / "reference-train"
    current.mkdir(parents=True)
    (root / "previous-role.json").write_bytes(b"x" * 20)
    guard = object.__new__(cal.ResourceGuard)
    guard.runtime = {"rss_bytes": 1000, "output_bytes": 10, "tmp_bytes": 1000}
    guard.output, guard.budget_root = current, root
    guard.sandbox_budget_root = tmp_path / "sandboxes"
    guard.lock, guard.stopped = threading.Lock(), threading.Event()
    guard.reason, guard.processes, guard.peak_rss = "", [], 0

    class TwoPasses:
        calls = 0

        def wait(self, interval):
            self.calls += 1
            return self.calls > 1

    guard.done = TwoPasses()
    monkeypatch.setattr(cal, "process_tree_rss", lambda pid: 0)
    guard.monitor()
    assert guard.reason == "R9_CALIBRATION_OUTPUT_LIMIT"


def test_learning_worker_timeout_is_receipted_without_second_attempt(study_request, monkeypatch):
    import subprocess

    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    killed = []
    monkeypatch.setattr(study.os, "killpg", lambda *args: killed.append(args))

    class Worker:
        stdout, stderr = io.BytesIO(b"{}"), io.BytesIO(b"")
        returncode, pid, waits = None, 456, 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                assert timeout <= 600
                raise subprocess.TimeoutExpired("synthetic", timeout)
            self.returncode = -9

    worker = Worker()
    monkeypatch.setattr(study.subprocess, "Popen", lambda *a, **k: worker)
    with pytest.raises(ValueError, match="WORKER_FAILED"):
        study.run_limited(study_request, "fit")
    receipt = study.read(study.root_for(study_request) / "runtime-fit/receipt.safe.json")
    assert receipt["timed_out"] and receipt["exit_code"] == -9 and len(killed) == 1
    with pytest.raises(FileExistsError):
        study.run_limited(study_request, "fit")
