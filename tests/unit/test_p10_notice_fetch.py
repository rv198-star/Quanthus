"""Offline transport/accounting checks. No test calls a publisher."""
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.p10_notice_fetch import (
    FixedNoticeCollector,
    is_implementation_notice,
)


@pytest.fixture
def collector(tmp_path, monkeypatch):
    for name in ("requests", "raw", "evidence"):
        (tmp_path / name).mkdir()
    selection = {"page_size": 30, "targets": [
        {"code": f"{i:06d}", "org_id": f"org{i}", "exchange": "SSE",
         "metadata_window": ["2021-01-01", "2021-12-31"]} for i in range(1, 129)
    ]}
    request = {"source_hostnames_allowed": ["www.cninfo.com.cn", "static.cninfo.com.cn"]}
    authorization = {
        "effective_acquisition_deadline": (datetime.now(timezone.utc)
                                            + timedelta(hours=1)).isoformat(),
        "maximum_requests": 640, "maximum_metadata_requests": 544,
        "maximum_response_bytes": 128 * 2**20, "maximum_unique_official_bodies": 96,
        "maximum_new_output_bytes": 256 * 2**20, "minimum_free_bytes": 0,
    }
    for name, value in [("request", request), ("metadata-selection", selection)]:
        raw = json.dumps(value).encode()
        (tmp_path / f"{name}.frozen.json").write_bytes(raw)
        authorization["request_sha256" if name == "request" else "selection_sha256"] = (
            hashlib.sha256(raw).hexdigest()
        )
    (tmp_path / "authorization.json").write_text(json.dumps(authorization))
    monkeypatch.setattr("quanthus.pipelines.securities_warehouse.p10_notice_fetch.time.sleep",
                        lambda _: None)
    return FixedNoticeCollector(tmp_path)


class Response(io.BytesIO):
    status = 200


def test_success_counts_exact_request_and_actual_bytes(collector):
    collector.opener = SimpleNamespace(open=lambda *a, **k: Response(b'{"ok":true}'))
    raw, receipt = collector.fetch("metadata", "000001", page=1)
    assert raw == b'{"ok":true}'
    assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
    assert collector.accounting()[:2] == (1, len(raw))


def test_failure_is_charged_and_original_attempt_is_retained(collector):
    def fail(*args, **kwargs):
        raise TimeoutError("synthetic timeout")
    collector.opener = SimpleNamespace(open=fail)
    _, receipt = collector.fetch("metadata", "000001", page=1)
    assert receipt["status"] == "failed"
    assert collector.accounting()[:2] == (1, 0)
    assert len(list((collector.root / "requests").glob("*.json"))) == 1


def test_interrupted_attempt_blocks_further_calls(collector):
    collector.append({"event": "started", "attempt": 1, "kind": "metadata"})
    with pytest.raises(ValueError, match="INTERRUPTED"):
        collector.accounting()


@pytest.mark.parametrize("url", ["http://static.cninfo.com.cn/a", "https://example.com/a",
                                 "https://user@static.cninfo.com.cn/a"])
def test_unapproved_links_fail_before_any_request(collector, url):
    with pytest.raises(ValueError, match="URL_SCOPE"):
        collector.fetch("body", "000001", body_url=url, notice_id="test")
    assert collector.accounting()[0] == 0


def test_total_cap_is_checked_before_new_attempt(collector):
    collector.authorization["maximum_requests"] = 0
    with pytest.raises(ValueError, match="REQUEST_OR_BYTE_CAP"):
        collector.fetch("metadata", "000001", page=1)
    assert collector.accounting()[0] == 0


def test_scan_failure_is_not_capacity_pass(collector, monkeypatch):
    def fail(path, onerror):
        onerror(PermissionError("synthetic"))
        return []
    monkeypatch.setattr("quanthus.pipelines.securities_warehouse.p10_notice_fetch.os.walk", fail)
    with pytest.raises(PermissionError):
        collector.guard(0)


def test_original_deadline_is_not_restarted(collector):
    collector.deadline = 0
    with pytest.raises(ValueError, match="APPROVED_DEADLINE"):
        collector.fetch("metadata", "000001", page=1)
    assert collector.accounting()[0] == 0


def test_changed_frozen_selection_is_rejected(collector):
    path = collector.root / "metadata-selection.frozen.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="FROZEN_IDENTITY"):
        FixedNoticeCollector(collector.root)


def empty_metadata(collector):
    for target in collector.selection["targets"]:
        raw = json.dumps({"totalAnnouncement": 0, "announcements": [], "hasMore": False}).encode()
        path = collector.root / "raw" / (target["code"] + ".json")
        path.write_bytes(raw)
        result = dict(**target, complete=True, unique_count=0, receipts=[
            {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "page": 1}
        ])
        (collector.root / "evidence" / ("metadata-" + target["code"] + ".json")).write_text(
            json.dumps(result)
        )


def test_body_selection_replays_complete_empty_scope_without_inventing_action(collector):
    empty_metadata(collector)
    assert collector.prepare_bodies() == []
    assert collector.accounting()[0] == 0


def test_modified_raw_metadata_blocks_body_selection(collector):
    empty_metadata(collector)
    (collector.root / "raw" / "000001.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="METADATA_BYTES_CHANGED"):
        collector.prepare_bodies()


def test_incomplete_issuer_blocks_body_preparation(collector):
    empty_metadata(collector)
    path = collector.root / "evidence" / "metadata-000001.json"
    result = json.loads(path.read_text())
    result["complete"] = False
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="INCOMPLETE_METADATA"):
        collector.prepare_bodies()


@pytest.mark.parametrize("title", ["关于2020年年度权益分派实施的更正公告",
                                 "2020年年度权益分派实施公告", "2021年半年度权益分派公告"])
def test_implementation_and_its_correction_are_all_selected(title):
    assert is_implementation_notice(title)


def test_proposal_is_not_pretended_to_be_implementation():
    assert not is_implementation_notice("2020年年度利润分配预案公告")
