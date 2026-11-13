import hashlib
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus import r9_calibration as cal


def opinion(packet, reader="reader_a", label="TRUE"):
    def citation(offset):
        return {
            "offset": offset,
            "field": "H",
            "value": packet["HLC40"][offset - 1][0],
            "reason": "observed envelope",
        }

    clauses = {}
    for name in cal.CLAUSES:
        clauses[name] = {
            "label": label,
            "support": [citation(1), citation(21)],
            "counterevidence": [citation(1), citation(21)],
            "rationale": "full path",
        }
    return {"case_id": packet["case_id"], "reader": reader, "clauses": clauses, "label": label}


@pytest.fixture
def task_request(tmp_path):
    rows = []
    anchors = ["2020-03-31", "2020-04-30", "2020-07-31", "2020-08-31"]
    for anchor in anchors:
        for i in range(3):
            key = f"opaque-{anchor}-{i}"
            rows.append(
                {
                    "identity": {"company_id": key, "anchor": anchor, "sample_id": key},
                    "available": True,
                    "packet": {
                        "case_id": key,
                        "available": True,
                        "HLC40": [["101.0000", "99.0000", "100.0000"]] * 40,
                    },
                    "primary_facts": {
                        k: {"value": v}
                        for k, v in {
                            "directional_efficiency": "0.1",
                            "range_width": str(i + 1),
                            "width_change": "0.1" if i == 2 else "-0.3",
                        }.items()
                    },
                }
            )
    pool, exclude = tmp_path / "pool.json", tmp_path / "exclude.json"
    cal.write(pool, {"rows": rows})
    cal.write(exclude, {"rows": []})
    task_request = tmp_path / "task_request.json"
    cal.write(
        task_request,
        {
            "source": {
                "pool_path": str(pool),
                "pool_sha256": cal.sha(pool),
                "exclude_path": str(exclude),
                "exclude_sha256": cal.sha(exclude),
            },
            "selection": {"anchors": anchors},
            "reference": {
                "provider_id": "magpie",
                "models": list(cal.MODELS),
                "maximum_calls": 24,
                "retries": 0,
                "concurrency": 2,
            },
            "runtime": {
                "calibration_deadline": (
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat(),
                "attempt_marker": str(tmp_path / "attempt.json"),
                "cpu_cores": 2,
                "rss_bytes": 2**31,
                "tmp_bytes": 2**30,
                "output_bytes": 2**26,
            },
        },
    )
    return task_request


def packet():
    return {"case_id": "opaque", "HLC40": [["101.0000", "99.0000", "100.0000"]] * 40}


@pytest.mark.parametrize(
    "labels,expected",
    [
        (["TRUE"] * 4, "TRUE"),
        (["TRUE", "UNKNOWN", "TRUE", "TRUE"], "UNKNOWN"),
        (["UNKNOWN", "FALSE", "TRUE", "TRUE"], "FALSE"),
    ],
)
def test_three_valued_and(labels, expected):
    assert cal.conjunction(labels) == expected


@pytest.mark.parametrize(
    "change", ["label_type", "old_window", "contraction_window", "price", "identity"]
)
def test_invalid_citation_or_label_is_accounted_value_error(change):
    p = packet()
    value = opinion(p)
    if change == "label_type":
        value["clauses"]["materially_narrow"]["label"] = []
    elif change == "old_window":
        value["clauses"]["materially_narrow"]["support"] = value["clauses"]["materially_narrow"][
            "support"
        ][:1]
    elif change == "contraction_window":
        value["clauses"]["material_contraction"]["support"] = value["clauses"][
            "material_contraction"
        ]["support"][1:]
    elif change == "price":
        value["clauses"]["low_directional_efficiency"]["support"][0]["value"] = "101"
    else:
        value["case_id"] = "other"
    with pytest.raises(ValueError):
        cal.validate(value, p, "reader_a")


@pytest.mark.parametrize("event", [[], None, {"type": "item.completed", "item": []}])
def test_malformed_trace_cannot_escape_as_attribute_error(event):
    with pytest.raises(ValueError):
        cal.filtered_trace(json.dumps(event), cal.MODELS[0])


def test_selection_tampering_fails_before_process_or_marker(task_request, tmp_path):
    cal.prepare(task_request, tmp_path / "selected")
    selected = tmp_path / "selected/selected.private.json"
    payload = json.loads(selected.read_text())
    payload["rows"][0]["source_row"]["packet"]["case_id"] = "tampered"
    cal.write(selected, payload)
    with pytest.raises(ValueError, match="PROTOCOL_BINDING"):
        cal.collect(task_request, selected, tmp_path / "output", tmp_path / "sandbox")
    assert not (tmp_path / "attempt.json").exists()


def test_source_mutation_and_identity_shortage_stop_before_prepare(task_request, tmp_path):
    c = json.loads(task_request.read_text())
    pool = Path(c["source"]["pool_path"])
    payload = json.loads(pool.read_text())
    payload["rows"][0]["available"] = False
    cal.write(pool, payload)
    with pytest.raises(ValueError, match="SOURCE_BINDING"):
        cal.prepare(task_request, tmp_path / "selected")
    c["source"]["pool_sha256"] = cal.sha(pool)
    cal.write(task_request, c)
    with pytest.raises(ValueError, match="QUOTA_SHORTAGE"):
        cal.prepare(task_request, tmp_path / "selected")


class FakeGuard:
    def __init__(self, runtime, output, sandbox):
        self.lock, self.done, self.stopped = threading.Lock(), threading.Event(), threading.Event()
        self.reason, self.processes, self.peak_rss, self.affinity = "", [], 0, [0, 1]
        self.thread = threading.Thread(target=lambda: None)

    def stop(self, reason):
        self.reason = reason
        self.stopped.set()


def fake_process(command, **kwargs):
    prompt = command[-1]
    p = json.loads(prompt.split("Packet: ")[1])
    reader = "reader_a" if "reader=reader_a" in prompt else "reader_b"
    value = opinion(p, reader, "FALSE" if p["case_id"].endswith("-2") else "TRUE")
    output = Path(command[command.index("-o") + 1])
    cal.write(output, value)
    events = [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
        {"type": "turn.completed", "usage": {}},
    ]

    class Process:
        returncode, pid = 0, 123
        stdout = io.BytesIO("\n".join(map(json.dumps, events)).encode())
        stderr = io.BytesIO(b"")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    return Process()


def test_24_calls_single_attempt_and_qa_exclusion_only(task_request, tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        return fake_process(command, **kwargs)

    monkeypatch.setattr(cal.subprocess, "Popen", launch)
    cal.prepare(task_request, tmp_path / "selected")
    selected = tmp_path / "selected/selected.private.json"
    result = cal.collect(task_request, selected, tmp_path / "output", tmp_path / "sandbox")
    assert result["calls"] == result["valid"] == len(calls) == 24
    with pytest.raises(FileExistsError):
        cal.collect(task_request, selected, tmp_path / "other", tmp_path / "other-sandbox")
    assert len(calls) == 24
    references = tmp_path / "output/references.sealed.json"
    sealed = json.loads(references.read_text())
    qa = tmp_path / "qa.json"
    cal.write(
        qa,
        {
            "request_sha256": cal.sha(task_request),
            "selected_sha256": cal.sha(selected),
            "opinions_sha256": cal.sha(references),
            "reviewer": "independent-test-role",
            "inspected_opinions": [
                {"case_id": r["case_id"], "reader": r["reader"]} for r in sealed["opinions"]
            ],
            "excluded": [
                {
                    "case_id": sealed["opinions"][0]["case_id"],
                    "reader": "reader_a",
                    "reason": "path misread",
                }
            ],
        },
    )
    summary = cal.summarize(task_request, selected, references, qa, tmp_path / "summary")
    assert summary["counts"] == {"TRUE": 7, "FALSE": 4, "UNKNOWN": 1}
    assert summary["panel_qualified"]
    changed = json.loads(qa.read_text())
    changed["opinions_sha256"] = hashlib.sha256(b"other").hexdigest()
    cal.write(qa, changed)
    with pytest.raises(ValueError, match="QA_BINDING"):
        cal.summarize(task_request, selected, references, qa, tmp_path / "other-summary")


def test_expired_request_never_creates_attempt(task_request, tmp_path):
    c = json.loads(task_request.read_text())
    c["runtime"]["calibration_deadline"] = "2020-01-01T00:00:00Z"
    cal.write(task_request, c)
    cal.prepare(task_request, tmp_path / "selected")
    with pytest.raises(ValueError, match="EXPIRED"):
        cal.collect(
            task_request,
            tmp_path / "selected/selected.private.json",
            tmp_path / "output",
            tmp_path / "sandbox",
        )
    assert not (tmp_path / "attempt.json").exists()


def test_empty_success_exit_stops_the_queue_without_retry(task_request, tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    calls = []

    class EmptyProcess:
        returncode, pid = 0, 123

        def __init__(self):
            self.stdout, self.stderr = io.BytesIO(b""), io.BytesIO(b"stdin notice")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    def launch(*args, **kwargs):
        calls.append(1)
        return EmptyProcess()

    monkeypatch.setattr(cal.subprocess, "Popen", launch)
    cal.prepare(task_request, tmp_path / "selected")
    result = cal.collect(
        task_request,
        tmp_path / "selected/selected.private.json",
        tmp_path / "out",
        tmp_path / "sandbox",
    )
    assert 1 <= len(calls) <= 2
    assert result["calls"] == len(calls) and result["valid"] == 0
    assert result["resource_monitor"]["stop_reason"] == "R9_CALIBRATION_TRACE_FAILURE"
    sealed = json.loads((tmp_path / "out/references.sealed.json").read_text())
    assert len(sealed["opinions"]) == 24
    assert sum(r["status"] == "NOT_RUN" for r in sealed["opinions"]) >= 22


def test_stream_limit_applies_only_to_captured_bytes(tmp_path):
    guard = FakeGuard({}, tmp_path, tmp_path)
    sink = io.BytesIO()
    cal.capture_stream(io.BytesIO(b"x" * 300000), sink, guard)
    assert len(sink.getvalue()) == 262144
    assert guard.reason == "R9_CALIBRATION_STREAM_LIMIT"


def test_synthetic_channel_probe_is_exactly_two_calls(task_request, tmp_path, monkeypatch):
    c = json.loads(task_request.read_text())
    c["schema_version"] = cal.PROBE_SCHEMA
    c["source"] = {
        "grade": "synthetic channel only",
        "packet_sha256": hashlib.sha256(cal.canonical(cal.channel_fixture())).hexdigest(),
    }
    c["reference"]["maximum_calls"] = 2
    cal.write(task_request, c)
    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        return fake_process(command, **kwargs)

    monkeypatch.setattr(cal.subprocess, "Popen", launch)
    selected = tmp_path / "selected"
    assert cal.prepare(task_request, selected)["cases"] == 1
    result = cal.collect(
        task_request, selected / "selected.private.json", tmp_path / "out", tmp_path / "sandbox"
    )
    assert result["calls"] == result["valid"] == len(calls) == 2
    assert json.loads((tmp_path / "attempt.json").read_text())["maximum_calls"] == 2
    assert {call[call.index("--model") + 1] for call in calls} == set(cal.MODELS)
    for call in calls:
        assert "--ephemeral" in call
        assert 'model_provider="magpie"' in call
        assert "model_providers.magpie.request_max_retries=0" in call
        assert "model_providers.magpie.stream_max_retries=0" in call
    assert result["fit_runs"] == result["update_runs"] == 0


def test_timeout_drains_streams_and_stops_following_slots(task_request, tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "ResourceGuard", FakeGuard)
    monkeypatch.setattr(cal.os, "killpg", lambda *args: None)
    processes = []

    class SlowStream(io.BytesIO):
        def read(self, n):
            time.sleep(0.01)
            return super().read(n)

    class TimedProcess:
        pid = 123

        def __init__(self):
            self.returncode = None
            self.stdout, self.stderr = SlowStream(b"tail"), SlowStream(b"error tail")

        def wait(self, timeout):
            if self.returncode is None:
                self.returncode = -9
                raise cal.subprocess.TimeoutExpired("fake-cli", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

    def launch(*args, **kwargs):
        p = TimedProcess()
        processes.append(p)
        return p

    monkeypatch.setattr(cal.subprocess, "Popen", launch)
    cal.prepare(task_request, tmp_path / "selected")
    result = cal.collect(
        task_request,
        tmp_path / "selected/selected.private.json",
        tmp_path / "out",
        tmp_path / "sandbox",
    )
    assert 1 <= result["calls"] <= 2
    assert result["valid"] == 0
    assert all(p.stdout.closed and p.stderr.closed for p in processes)
    rows = json.loads((tmp_path / "out/references.sealed.json").read_text())["opinions"]
    attempted = [r for r in rows if r["call_started"]]
    assert all(r["error"] == "R9_CALIBRATION_TIMEOUT" for r in attempted)
    assert all(r["process_exit_code"] == -9 and r["termination_signal"] == 9 for r in attempted)
