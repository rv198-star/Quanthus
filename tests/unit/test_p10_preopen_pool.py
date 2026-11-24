import json
from datetime import timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus import p10_preopen_pool as module
from quanthus.p10_preopen_samples import SLOTS, URL, SamplingError, encoded, instant, sha


def synthetic_references(tmp_path, monkeypatch, *, duplicate=False, conflict=False):
    base, core = tmp_path / "base", tmp_path / "core"
    base.mkdir()
    core.mkdir()
    stamp = instant("2026-11-24T18:00:00+08:00")
    clocks = {
        "knowledge_from": stamp,
        "knowledge_to": None,
        "effective_from": instant("2020-01-01T00:00:00Z"),
        "effective_to": None,
    }
    listings, identifiers, venues, states = [], [], [], []
    for code, venue, board in [("002517", "XSHE", "SZ_main"), ("600000", "XSHG", "SSE_main")]:
        listings.append(
            {
                **clocks,
                "listing_id": "l" + code,
                "security_id": "s" + code,
                "venue_id": venue,
                "listing_code": code,
                "board_code": board,
            }
        )
        identifiers.append(
            {
                **clocks,
                "security_id": "s" + code,
                "identifier_namespace": "CN_STOCK_CODE",
                "identifier_value": code,
            }
        )
        venues.append({**clocks, "venue_id": venue, "mic_code": venue})
        states.append(
            {
                "listing_id": "l" + code,
                "reason_code": "NORMAL",
                "knowledge_from": stamp,
                "knowledge_to": None,
                "observation_start": instant("2026-11-24T00:00:00+08:00"),
                "observation_end": instant("2026-11-25T00:00:00+08:00"),
            }
        )

    def manifest(root, tables, **header):
        files = []
        for table, rows in tables.items():
            path = root / table / "part.parquet"
            path.parent.mkdir()
            pq.write_table(pa.Table.from_pylist(rows), path)
            body = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": "sha256:" + sha(body),
                    "size_bytes": len(body),
                }
            )
        body = encoded({**header, "files": files})
        (root / "manifest.json").write_bytes(body)
        return sha(body)

    base_sha = manifest(
        base,
        {
            "dwd_listing": listings,
            "dwd_security_identifier": identifiers,
            "dwd_trading_venue": venues,
        },
    )
    increments = {}
    if duplicate:
        newer = {**listings[0], "knowledge_from": stamp + timedelta(minutes=1)}
        if conflict:
            newer["venue_id"] = "XSHG"
        increments["dwd_listing"] = [newer]
    core_sha = manifest(
        core, {**increments, "dwd_tradability_state": states}, market_date="2026-11-24"
    )
    fallback = tmp_path / "fallback.json"
    fallback.write_bytes(b"synthetic fallback")
    monkeypatch.setattr(module, "BASE_SHA", base_sha)
    monkeypatch.setattr(module, "FALLBACK_SHA", sha(fallback.read_bytes()))
    cal = tmp_path / "calendar.json"
    cal.write_bytes(
        encoded(
            {"venues": {v: {"sessions": ["2026-11-24", "2026-11-25"]} for v in ["SSE", "SZSE"]}}
        )
    )
    dates = tmp_path / "DATES.json"
    dates.write_bytes(
        encoded(
            {
                "dates": {
                    "2026-11-24": {
                        "market_date": "2026-11-24",
                        "status": "QUALIFIED_1D_NATIVE5M",
                        "core": str(core),
                        "core_manifest_sha256": core_sha,
                    }
                }
            }
        )
    )
    return {
        "sample_dates": ["2026-11-25", "2026-11-26"],
        "slots": list(SLOTS),
        "endpoint": URL,
        "max_securities": 18,
        "max_requests_per_day": 6,
        "calendar_descriptor": str(cal),
        "calendar_sha256": sha(cal.read_bytes()),
        "fallback": str(fallback),
        "dates": str(dates),
        "base": str(base),
    }


def test_reference_projection_uses_base_and_daily_with_exact_evidence(tmp_path, monkeypatch):
    plan = synthetic_references(tmp_path, monkeypatch)
    pool = module.prepare(plan, "2026-11-25", instant("2026-11-25T09:00:00+08:00"))
    assert [s["security_code"] for s in pool["securities"]] == ["002517", "600000"]
    assert pool["price_rows_read"] is False
    assert all(len(s["reference_evidence"]) == 9 for s in pool["securities"])
    assert "EX_DIVIDEND_EVENT_NOT_IN_SELECTED_REFERENCE" in pool["coverage_gaps"]


def test_domain_identical_increment_keeps_both_provenance_clocks(tmp_path, monkeypatch):
    plan = synthetic_references(tmp_path, monkeypatch, duplicate=True)
    pool = module.prepare(plan, "2026-11-25", instant("2026-11-25T09:00:00+08:00"))
    assert len(pool["securities"]) == 2
    assert len(pool["securities"][0]["reference_evidence"]) == 10


def test_conflicting_holding_identity_blocks_instead_of_prioritizing_source(tmp_path, monkeypatch):
    plan = synthetic_references(tmp_path, monkeypatch, duplicate=True, conflict=True)
    with pytest.raises(SamplingError, match="HOLDING_IDENTITY_UNAVAILABLE"):
        module.prepare(plan, "2026-11-25", instant("2026-11-25T09:00:00+08:00"))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "T_REFERENCE_NOT_READY"),
        ("date", "T_REFERENCE_DATE_OR_STATUS"),
        ("status", "T_REFERENCE_DATE_OR_STATUS"),
        ("sha", "REFERENCE_MANIFEST_IDENTITY"),
    ],
)
def test_unready_or_mismatched_daily_is_never_substituted(tmp_path, monkeypatch, change, reason):
    plan = synthetic_references(tmp_path, monkeypatch)
    path = tmp_path / "DATES.json"
    dates = json.loads(path.read_bytes())
    entry = dates["dates"]["2026-11-24"]
    if change == "missing":
        dates["dates"].clear()
    elif change == "date":
        entry["market_date"] = "2026-11-23"
    elif change == "status":
        entry["status"] = "FAILED"
    else:
        entry["core_manifest_sha256"] = "0" * 64
    path.write_bytes(encoded(dates))
    with pytest.raises(SamplingError, match=reason):
        module.prepare(plan, "2026-11-25", instant("2026-11-25T09:00:00+08:00"))


def test_selector_is_deterministic_bounded_and_excludes_validation_universe():
    rows = [
        {"security_code": code, "board": board, "reason_code": reason}
        for code, board, reason in [
            ("002517", "SZ_main", "NORMAL"),
            ("600000", "SSE_main", "NORMAL"),
            ("300001", "ChiNext", "NORMAL"),
            ("300002", "ChiNext", "NORMAL"),
            ("688001", "STAR", "NORMAL"),
            ("688002", "STAR", "NORMAL"),
            ("920001", "BSE_main", "NORMAL"),
            ("920002", "BSE_main", "NORMAL"),
            ("000001", "SZ_main", "ST_TRADABLE"),
            ("600001", "SSE_main", "ST_TRADABLE"),
            ("000002", "SZ_main", "SOURCE_NOT_TRADABLE"),
            ("000003", "SZ_main", "SOURCE_NOT_TRADABLE"),
            ("300308", "ChiNext", "NORMAL"),
        ]
    ]
    selected, gaps = module.choose(rows)
    assert module.choose(list(reversed(rows))) == (selected, gaps)
    assert len(selected) <= 18
    assert "300308" not in {r["security_code"] for r in selected}
    assert {"002517", "600000"} <= {r["security_code"] for r in selected}


def test_unknown_knowledge_clock_is_not_active_reference():
    cutoff = instant("2026-11-25T09:00:00+08:00")
    assert not module._active({"knowledge_from": None}, cutoff, cutoff)
    assert not module._active({"knowledge_from": cutoff.replace(tzinfo=None)}, cutoff, cutoff)
    assert not module._active({"knowledge_from": cutoff, "effective_from": None}, cutoff, cutoff)
