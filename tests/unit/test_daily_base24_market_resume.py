from __future__ import annotations

import base64
import gzip
import json
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_market_resume as resume

DAY = date(2026, 10, 20)
START = "2026-10-20T11:00:00+00:00"
OBSERVED = "2026-10-20T19:00:01+08:00"
END = "2026-10-20T11:00:02+00:00"


@pytest.fixture(autouse=True)
def provider_contract(monkeypatch):
    # The worker imports this protocol from a separate published checkout, not
    # from the controller package. Stub that dependency; exercise the adapter.
    from decimal import Decimal

    global evidence
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: END

    def capture(*, request, raw, started_at, completed_at, status, root):
        envelope = dict(
            policy_id="p10-acquisition-prevention-v1",
            request=request,
            status=status,
            request_started_at=started_at,
            response_completed_at=completed_at,
            warehouse_observed_at=evidence.clock(),
            publication_at=None,
            provider_availability_at=None,
            raw_sha256=resume.sha(raw),
            raw_bytes=len(raw),
            raw_base64=base64.b64encode(raw).decode(),
        )
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        root.mkdir(parents=True, exist_ok=True)
        path = root / (resume.sha(encoded) + ".json.gz")
        path.write_bytes(gzip.compress(encoded, mtime=0))
        return {k: v for k, v in envelope.items() if k != "raw_base64"} | {
            "capture_file": path.name,
            "capture_sha256": resume.sha(path.read_bytes()),
        }

    def number(token, required=False):
        if token is None or not str(token).strip():
            if required:
                raise ValueError("required token")
            return None
        assert Decimal(token).is_finite()
        return token

    def suspended(row, *, code, provider_code, day, observed_at):
        if (
            row[:2] != [day, provider_code]
            or row[10] not in {"0", "1"}
            or row[11] not in {"0", "1"}
        ):
            raise ValueError("identity/status")
        if row[10] == "0" and not all(row[2:8]):
            return dict(security_id=code, tradestatus="0", acquired_at=observed_at)
        return None

    evidence.capture = capture
    evidence.verify_capture = lambda p: json.loads(gzip.decompress(p.read_bytes()))
    evidence.decimal_token = number
    evidence.suspended_status = suspended
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)


def fixture(tmp_path, monkeypatch, *, suspended=False):
    root = tmp_path / "source"
    rawroot = root / "original-responses"
    monkeypatch.setattr(evidence, "clock", lambda: END)
    request = dict(
        provider="baostock",
        api="query_history_k_data_plus",
        code="sh.600000",
        start=str(DAY),
        end=str(DAY),
        frequency="d",
        adjustflag="3",
        fields=resume.FIELDS,
    )
    row = [
        str(DAY),
        "sh.600000",
        "12.3400000000",
        "13",
        "12",
        "12.5",
        "100",
        "1250",
        "0",
        "0.1",
        "1",
        "0",
        "1",
        "2",
        "",
        "0",
    ]
    if suspended:
        row[2:8] = [""] * 6
        row[10] = "0"
    receipt = evidence.capture(
        request={**request, "representation": "lossless_sdk_strings"},
        raw=json.dumps(
            dict(fields=resume.FIELDS.split(","), rows=[row], error_code="0", error_msg="ok")
        ).encode(),
        started_at=START,
        completed_at=END,
        status="0",
        root=rawroot,
    )
    log = dict(
        market_date=str(DAY),
        requests=[
            dict(
                security_id="600000",
                request=request,
                response_error_code="0",
                response_error_msg="ok",
                response_rows=1,
                acquired_at=OBSERVED,
                raw_receipt=receipt,
            )
        ],
    )
    (root / "acquisition_log.json").write_text(json.dumps(log))
    (root / "source_universe.json").write_text(
        json.dumps(dict(market_date=str(DAY), codes=["600000", "600001"]))
    )
    files = {
        str(p.relative_to(root)): dict(sha256=resume.sha(p.read_bytes()), bytes=p.stat().st_size)
        for p in root.rglob("*")
        if p.is_file()
    }
    value = dict(
        kind="cn02_daily_market_seed_v1",
        market_date=str(DAY),
        root=str(root),
        files=files,
        collector_revision="f" * 40,
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(value))
    return inventory, value, log, row


def loaded(inventory):
    return resume.load_seed(inventory, resume.sha(inventory.read_bytes()), DAY)


def test_missing_only_original_decimal_clock_and_independent_evidence(tmp_path, monkeypatch):
    inv, value, log, row = fixture(tmp_path, monkeypatch)
    target = tmp_path / "target"
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(target))
    calls = []

    def original(codes, day, **kwargs):
        calls.append((codes, day, kwargs))
        return ([{"security_id": "600001"}], [{"security_id": "600001"}], [])

    seed = loaded(inv)
    bars, states, logs = resume.adapter(original, seed, "a" * 40)(
        ["600000", "600001"], DAY, deadline_at="unchanged"
    )
    assert calls == [(["600001"], DAY, {"deadline_at": "unchanged"})]
    assert bars[0]["open"] == row[2]
    assert bars[0]["acquired_at"] == bars[0]["built_at"] == OBSERVED
    assert bars[0]["_valuation_ps_ttm"] is None
    assert bars[0]["_valuation_pcf_ttm"] == "0"
    assert states[0]["acquired_at"] == OBSERVED
    assert logs[0]["raw_receipt"] == log["requests"][0]["raw_receipt"]
    assert logs[-1]["seeded_securities"] == logs[-1]["requested_missing_securities"] == 1
    for relative in value["files"]:
        p = Path(value["root"]) / relative
        assert resume.sha(p.read_bytes()) == value["files"][relative]["sha256"]
        if relative.startswith("original-responses/"):
            copied = target / p.name
            assert copied.read_bytes() == p.read_bytes()
            assert copied.stat().st_ino != p.stat().st_ino and copied.stat().st_nlink == 1
    for p in target.iterdir():
        evidence.verify_capture(p)


def test_suspension_without_prices_retains_status_only(tmp_path, monkeypatch):
    inv, _, _, _ = fixture(tmp_path, monkeypatch, suspended=True)
    seed = loaded(inv)
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(tmp_path / "target"))
    bars, states, _ = resume.adapter(lambda *a, **kw: ([], [], []), seed, "a" * 40)(
        ["600000", "600001"], DAY
    )
    assert bars == []
    assert states[0]["tradestatus"] == "0"
    assert states[0]["acquired_at"] == OBSERVED


@pytest.mark.parametrize("change", ["bytes", "inventory", "extra", "symlink", "day"])
def test_frozen_members_and_scope_fail_before_output(tmp_path, monkeypatch, change):
    inv, value, _, _ = fixture(tmp_path, monkeypatch)
    digest = resume.sha(inv.read_bytes())
    root = Path(value["root"])
    if change == "bytes":
        (root / "acquisition_log.json").write_bytes(b"changed")
    elif change == "inventory":
        inv.write_bytes(b"{}")
    elif change == "extra":
        (root / "original-responses/extra.json.gz").write_bytes(b"extra")
    elif change == "symlink":
        real = root / "acquisition_log.json"
        original = root / "saved.json"
        real.rename(original)
        real.symlink_to(original)
    with pytest.raises(ValueError):
        resume.load_seed(inv, digest, date(2026, 10, 19) if change == "day" else DAY)
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    "change", ["receipt", "duplicate", "unknown", "universe", "overlap", "clock", "scope"]
)
def test_log_closure_identity_and_overlap_fail_before_mutation(tmp_path, monkeypatch, change):
    inv, value, _, _ = fixture(tmp_path, monkeypatch)
    seed = loaded(inv)
    target = tmp_path / "target"
    log = seed["log"]["requests"][0]
    if change == "receipt":
        log["raw_receipt"]["capture_sha256"] = "0" * 64
    elif change == "duplicate":
        seed["log"]["requests"].append(dict(log))
    elif change == "unknown":
        log["security_id"] = "600002"
    elif change == "clock":
        log["acquired_at"] = "2026-10-19T11:00:00+00:00"
    elif change == "scope":
        log["request"]["frequency"] = "5"
    elif change == "overlap":
        target = Path(value["root"]) / "original-responses"
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(target))
    before = {p: p.read_bytes() for p in Path(value["root"]).rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        resume.adapter(lambda *a, **kw: pytest.fail("no source request allowed"), seed, "a" * 40)(
            ["600001", "600000"] if change == "universe" else ["600000", "600001"], DAY
        )
    assert before == {p: p.read_bytes() for p in Path(value["root"]).rglob("*") if p.is_file()}
    if change != "overlap":
        assert not target.exists()


def test_changed_input_between_validation_and_copy_never_calls_provider(tmp_path, monkeypatch):
    inv, value, _, _ = fixture(tmp_path, monkeypatch)
    seed = loaded(inv)
    (Path(value["root"]) / "acquisition_log.json").write_bytes(b"changed")
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(tmp_path / "target"))
    with pytest.raises(ValueError, match="CHANGED_BEFORE_COPY"):
        resume.adapter(lambda *a, **kw: pytest.fail("no source request allowed"), seed, "a" * 40)(
            ["600000", "600001"], DAY
        )


def test_collector_second_precision_is_retained_without_inventing_subseconds(tmp_path, monkeypatch):
    inv, _, _, _ = fixture(tmp_path, monkeypatch)
    seed = loaded(inv)
    log = seed["log"]["requests"][0]
    envelope = seed["captures"][log["raw_receipt"]["capture_file"]]
    envelope["request_started_at"] = "2026-10-20T11:00:01.100000+00:00"
    bar, _ = resume.rows_for(log, envelope, DAY)
    assert bar["acquired_at"] == OBSERVED
