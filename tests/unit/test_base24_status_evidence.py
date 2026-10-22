"""Existing original responses can repair dated evidence without invented clocks."""

import copy
from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse import base24_status_evidence as subject


def raw():
    return {
        "stock_code": "002607",
        "exchange": "SZSE",
        "bs_code": "sz.002607",
        "window": {"start": "2026-07-01", "end": "2026-07-25"},
        "request": {
            "code": "sz.002607",
            "fields": "date,code,isST,tradestatus",
            "start_date": "2026-07-01",
            "end_date": "2026-07-25",
            "frequency": "d",
            "adjustflag": "3",
            "fetched_at": "2026-07-24T18:18:12.348937+00:00",
        },
        "error_code": "0",
        "error_msg": "success",
        "n_rows": 1,
        "rows": [{"date": "2026-07-08", "code": "sz.002607", "isST": "0", "tradestatus": "1"}],
    }


def test_original_capture_is_preserved_and_not_backdated_to_market_day():
    data = raw()
    before = copy.deepcopy(data)
    result = subject.raw_observations(data, {("002607", "2026-07-08")}, "sha256:" + "1" * 64)
    assert data == before
    assert len(result) == 1
    assert result[0]["acquired_at"] == data["request"]["fetched_at"]
    assert result[0]["market_date"] == "2026-07-08"
    assert result[0]["status_value"] == "NORMAL"
    assert result[0]["reason"] == "PROVIDER_TRADING"
    assert result[0]["source_row"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["request"].pop("fetched_at"),
        lambda d: d["request"].update(fetched_at="2026-07-24T18:18:12"),
        lambda d: d.update(error_code="1"),
        lambda d: d.update(n_rows=2),
        lambda d: d["request"].update(code="sh.002607"),
        lambda d: d["request"].update(fields="date,code,close,isST,tradestatus"),
        lambda d: d["rows"][0].update(code="sz.002608"),
        lambda d: d["rows"][0].update(isST="unknown"),
        lambda d: d["rows"].append(copy.deepcopy(d["rows"][0])),
        lambda d: d["rows"][0].update(date="2026-08-01"),
    ],
)
def test_unqualified_response_cannot_create_dated_evidence(mutation):
    data = raw()
    mutation(data)
    with pytest.raises(subject.StatusEvidenceError):
        subject.raw_observations(data, {("002607", "2026-07-08")}, "sha256:" + "1" * 64)


def test_no_current_state_carry_forward_or_future_capture_admission():
    rows = subject.raw_observations(raw(), {("002607", "2026-07-08")}, "sha256:" + "1" * 64)
    requests = [
        {"stock_code": "002607", "market_date": "2026-07-08"},
        {"stock_code": "002607", "market_date": "2026-07-09"},
    ]
    held = subject.select_status(requests, rows, "2026-07-24T18:00:00+00:00")
    assert all(not r["qualified"] for r in held)
    selected = subject.select_status(requests, rows, "2026-07-25T00:00:00+00:00")
    assert selected[0]["qualified"] and not selected[1]["qualified"]


def test_same_time_disagreement_stays_held_and_older_observations_retained():
    rows = subject.raw_observations(raw(), {("002607", "2026-07-08")}, "sha256:" + "1" * 64)
    conflict = {**rows[0], "status_value": "ST", "source_sha256": "sha256:" + "2" * 64}
    request = [{"stock_code": "002607", "market_date": "2026-07-08"}]
    assert not subject.select_status(request, rows + [conflict], "2026-08-01T00:00:00Z")[0][
        "qualified"
    ]
    older = {**conflict, "acquired_at": "2026-07-23T18:00:00+00:00"}
    selected = subject.select_status(request, rows + [older], "2026-08-01T00:00:00Z")[0]
    assert selected["qualified"] and selected["status_value"] == "NORMAL"
    assert selected["observations"] == 2


def test_normalized_hotfix_alias_and_capture_checks():
    row = dict(
        stock_code="002607",
        security_id="002607",
        market_date=date(2026, 7, 8),
        date="2026-07-08",
        isST="0",
        tradestatus="1",
        acquired_at="2026-07-25T00:00:00Z",
        source="baostock.query_history_k_data_plus",
    )
    assert subject.hotfix_observation(row, "sha256:" + "1" * 64, 0)["status_value"] == "NORMAL"
    for bad in [
        {**row, "date": "2026-07-09"},
        {**row, "acquired_at": None},
        {**row, "security_id": "002608"},
    ]:
        with pytest.raises(subject.StatusEvidenceError):
            subject.hotfix_observation(bad, "sha256:" + "1" * 64, 0)


def fixture_source(tmp_path):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    root = tmp_path / "ods"
    root.mkdir()
    (root / "original.json").write_text(json.dumps(raw()))
    member = dict(
        path="original.json",
        sha256=file_sha(root / "original.json"),
        size_bytes=(root / "original.json").stat().st_size,
    )
    (root / "manifest.json").write_text(json.dumps({"content": {"files": [member]}}))
    return (
        root,
        file_sha(root / "manifest.json"),
        [dict(original_member=member, format="baostock-status-response")],
    )


def publish(tmp_path):
    root, sha, sources = fixture_source(tmp_path)
    out = tmp_path / "output"
    result = subject.build(
        root,
        sha,
        sources,
        [dict(stock_code="002607", market_date="2026-07-08")],
        "2026-08-01T00:00:00Z",
        out,
        "1" * 40,
    )
    return root, out, result


def test_cold_verification_needs_only_moved_product(tmp_path):
    import shutil

    root, out, result = publish(tmp_path)
    moved = tmp_path / "moved"
    shutil.copytree(out, moved)
    shutil.rmtree(out)
    shutil.rmtree(root)
    assert subject.verify(moved, result["manifest_sha256"])["dated_status_qualified"] == 1


def test_repacked_forged_qualification_is_recomputed(tmp_path):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    _, out, _ = publish(tmp_path)
    rows = json.loads((out / "dated-status.json").read_text())
    rows[0]["status_value"] = "ST"
    (out / "dated-status.json").write_text(json.dumps(rows))
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"]["dated-status.json"] = file_sha(out / "dated-status.json")
    (out / "manifest.json").write_text(json.dumps(manifest))
    sha = file_sha(out / "manifest.json")
    (out / "READY").write_text(sha + "\n")
    with pytest.raises(subject.StatusEvidenceError, match="RECOMPUTED_SEMANTICS"):
        subject.verify(out, sha)


def test_failed_source_has_no_ready_and_sealed_output_cannot_be_replaced(tmp_path):
    root, out, result = publish(tmp_path)
    _, sha, sources = root, subject.file_sha(root / "manifest.json"), []
    with pytest.raises(subject.StatusEvidenceError, match="OUTPUT_EXISTS"):
        subject.build(root, sha, sources, [], "2026-08-01T00:00:00Z", out, "1" * 40)
    assert subject.verify(out, result["manifest_sha256"])["requests"] == 1
    source = next((out / "sources").iterdir())
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(subject.StatusEvidenceError, match="MEMBER_HASH"):
        subject.verify(out, result["manifest_sha256"])


@pytest.mark.parametrize("malice", ["path", "digest", "unknown", "format"])
def test_bad_source_selection_is_rejected_before_writes(tmp_path, malice):
    root, sha, sources = fixture_source(tmp_path)
    if malice == "path":
        sources[0]["original_member"]["path"] = "../escape.json"
    elif malice == "digest":
        sources[0]["original_member"]["sha256"] = "sha256:../../escape"
    elif malice == "format":
        sources[0]["format"] = "other-provider"
    else:
        sources[0]["original_member"]["size_bytes"] += 1
    out = tmp_path / "output"
    with pytest.raises(subject.StatusEvidenceError):
        subject.build(root, sha, sources, [], "2026-08-01T00:00:00Z", out, "1" * 40)
    assert not out.exists()
