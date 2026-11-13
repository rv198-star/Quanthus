from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.r7_decision_review import (
    digest,
    encoded,
    load_request,
    project_rows,
    query,
    read_package,
    render_html,
)
from quanthus.r7_native import file_sha, materialize

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def native_input(tmp_path: Path) -> tuple[Path, dict[str, object], Path]:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pytest.importorskip("duckdb")
    known = datetime(2026, 10, 31, tzinfo=timezone.utc)
    common = {"knowledge_from": known, "knowledge_to": None}
    days = []
    day = date(2020, 1, 2)
    while day <= date(2020, 4, 30):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    tables: dict[str, object] = {}
    tables["dwd_listing"] = [
        {
            **common,
            "listing_code": code,
            "security_id": "sec" + code,
            "listing_id": "list" + code,
            "venue_id": "venue" + str(i % 2),
            "effective_from": datetime(2018, 1, 1, tzinfo=timezone.utc),
            "effective_to": None,
        }
        for i, code in enumerate(("000001", "300750", "600519"))
    ]
    tables["dwd_security"] = [
        {
            **common,
            "security_id": "sec" + code,
            "effective_from": datetime(2018, 1, 1, tzinfo=timezone.utc),
            "effective_to": None,
        }
        for code in ("000001", "300750", "600519")
    ]
    tables["dwd_trading_calendar"] = [
        {
            **common,
            "venue_id": venue,
            "trade_date": day,
            "calendar_session_id": venue + str(day),
            "is_open": True,
            "close_at": datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc),
        }
        for venue in ("venue0", "venue1")
        for day in days
    ]
    bars, states = [], []
    for i, code in enumerate(("000001", "300750", "600519")):
        for n, day in enumerate(days):
            price = 100 + n / 10
            identity = {
                **common,
                "listing_id": "list" + code,
                "calendar_session_id": "venue" + str(i % 2) + str(day),
            }
            bars.append(
                {
                    **identity,
                    "interval_size": "1d",
                    "price_basis": "UNADJUSTED",
                    "currency": "CNY",
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price,
                    "volume": 1000.0,
                    "turnover": 100000.0,
                    "source_record_id": code + str(day),
                    "source_artifact_sha256": "sha256:" + "a" * 64,
                    "observation_start": datetime(
                        day.year, day.month, day.day, 1, tzinfo=timezone.utc
                    ),
                    "observation_end": datetime(
                        day.year, day.month, day.day, 7, tzinfo=timezone.utc
                    ),
                }
            )
            states.append(
                {
                    **identity,
                    "is_tradable": True,
                    "can_buy": True,
                    "can_sell": True,
                    "upper_price": price + 10,
                    "lower_price": price - 10,
                    "source_artifact_sha256": "sha256:" + "b" * 64,
                    "observation_start": datetime(
                        day.year, day.month, day.day, 1, tzinfo=timezone.utc
                    ),
                    "observation_end": datetime(
                        day.year, day.month, day.day, 7, tzinfo=timezone.utc
                    ),
                }
            )
    tables["dwd_security_bar"] = bars
    tables["dwd_tradability_state"] = states
    timestamp = pa.timestamp("us", tz="UTC")
    schemas = {
        "dwd_corporate_action": pa.schema(
            [
                ("knowledge_from", timestamp),
                ("knowledge_to", timestamp),
                ("security_id", pa.string()),
                ("action_status", pa.string()),
                ("corporate_action_id", pa.string()),
                ("ex_date", pa.date32()),
            ]
        ),
        "dwd_quality_exception": pa.schema(
            [
                ("knowledge_from", timestamp),
                ("knowledge_to", timestamp),
                ("affected_effective_at", timestamp),
                ("resolution_state", pa.string()),
                ("affected_business_key_json", pa.string()),
                ("quality_exception_id", pa.string()),
            ]
        ),
    }
    source, project = tmp_path / "input", tmp_path / "project"
    source.mkdir()
    project.mkdir()
    members = []
    for table in sorted(set(tables) | set(schemas)):
        name = f"payload/{table}/all/part-000000.parquet"
        path = source / "DWD" / name
        path.parent.mkdir(parents=True)
        data = (
            pa.Table.from_pylist(tables[table])
            if table in tables
            else pa.Table.from_pylist([], schema=schemas[table])
        )
        pq.write_table(data, path)
        members.append({"path": name, "sha256": file_sha(path), "size_bytes": path.stat().st_size})
    closure = project / "closure.json"
    closure.write_bytes(encoded({"members": members}))
    request = load_request(ROOT)
    request["closure"] = {
        "path": "closure.json",
        "sha256": file_sha(closure),
        "exact_members": len(members),
        "exact_bytes": sum(item["size_bytes"] for item in members),
    }
    return source, request, project


def test_real_shape_native_adapter_and_unchanged_experts(
    native_input: tuple[Path, dict[str, object], Path], tmp_path: Path
) -> None:
    source, request, project = native_input
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    data = materialize(source, project, request, scratch)  # type: ignore[arg-type]
    result = project_rows(data, ROOT, request)  # type: ignore[arg-type]
    assert result["summary"]["row_count"] == 66
    assert result["summary"]["three_operator_available_rows"] == 66
    assert result["summary"]["trend_states"] == {"stable": 66}
    for row in result["rows"]:
        assert row["decision"]["action"]["proposed_action"] == "trial_entry"
        assert row["decision"]["action"]["final_action"] == "no_action"
        assert row["baseline_digest"] == row["after_sidecar_digest"]
        assert len(row["expert_context"]["feature_facts"]) == 2
        assert row["execution"]["fills"] == []
        assert Decimal(row["operators"]["volume"]["ratio20"]) == Decimal("1")
        assert row["clock"]["actual_source_cutoff"].startswith("2026-")
        assert row["clock"]["simulated_t_close"].startswith("2020-")
    assert project_rows(data, ROOT, request) == result  # type: ignore[arg-type]
    packaged = {**result, "manifest_sha256": "sha256:" + "e" * 64}
    selected = query(packaged, security="000001", day="2020-04-09", limit=1)
    assert (selected["matched"], selected["returned"], selected["truncated"]) == (1, 1, False)
    assert query(packaged, security="000001", limit=1)["truncated"] is True
    view = render_html(selected)
    assert "000001" in view and "</script>" in view
    assert "http://" not in view and "https://" not in view


def test_action_hold_preserves_denominator_and_blocks_derived_coordinates(
    native_input: tuple[Path, dict[str, object], Path], tmp_path: Path
) -> None:
    source, request, project = native_input
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    data = materialize(source, project, request, scratch)  # type: ignore[arg-type]
    data["holds"]["000001"] = ["unqualified_action"]
    result = project_rows(data, ROOT, request)  # type: ignore[arg-type]
    assert result["summary"]["row_count"] == 66
    assert result["summary"]["three_operator_available_rows"] == 44
    held = [r for r in result["rows"] if r["identity"]["code"] == "000001"]
    assert all(r["operators"]["trend"]["status"] == "ABSTAIN" for r in held)
    assert all(r["operators"]["structure"]["status"] == "X" for r in held)
    assert all(r["decision"]["action"]["final_action"] == "no_action" for r in held)


def test_all_unavailable_is_partial(
    native_input: tuple[Path, dict[str, object], Path], tmp_path: Path
) -> None:
    source, request, project = native_input
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    data = materialize(source, project, request, scratch)  # type: ignore[arg-type]
    for code in data["holds"]:
        data["holds"][code] = ["unqualified_input"]
    result = project_rows(data, ROOT, request)  # type: ignore[arg-type]
    assert result["summary"]["status"] == "partial"
    assert result["summary"]["three_operator_available_rows"] == 0


def test_authenticated_cold_package_and_corruption(tmp_path: Path) -> None:
    result = {"rows": [], "summary": {"row_count": 0}}
    package = tmp_path / "package"
    package.mkdir()
    (package / "rows.jsonl").write_bytes(b"")
    (package / "summary.json").write_bytes(encoded(result["summary"]))
    manifest = {
        "schema_version": "quanthus.r7-decision-review.v1",
        "semantic_digest": digest(result),
        "files": {},
    }
    for name in ("rows.jsonl", "summary.json"):
        manifest["files"][name] = {
            "sha256": file_sha(package / name),  # type: ignore[index]
            "bytes": (package / name).stat().st_size,
        }
    (package / "manifest.json").write_bytes(encoded(manifest))
    trusted = file_sha(package / "manifest.json")
    (package / "READY").write_bytes(encoded({"manifest_sha256": trusted}))
    assert read_package(package, trusted)["rows"] == []
    (package / "summary.json").write_text("{}")
    with pytest.raises(ValueError, match="PAYLOAD"):
        read_package(package, trusted)


def test_output_escaping_and_fixed_safety_request() -> None:
    request = load_request(ROOT)
    assert request["decisions"]["entry_evidence_ready"] is False
    result = {
        "rows": [],
        "matched": 0,
        "returned": 0,
        "manifest_sha256": '<script>alert("bad")</script>',
        "summary": {"row_count": 0, "three_operator_available_rows": 0},
    }
    view = render_html(copy.deepcopy(result))
    assert '<script>alert("bad")</script>' not in view
    assert "&lt;script&gt;" in view


@pytest.mark.parametrize(
    "defect", ["currency", "future_bar", "infinite_volume", "session_reuse", "security_missing"]
)
def test_invalid_native_history_and_identity_fail_closed(
    native_input: tuple[Path, dict[str, object], Path], tmp_path: Path, defect: str
) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    source, request, project = native_input
    table = {"session_reuse": "dwd_trading_calendar", "security_missing": "dwd_security"}.get(
        defect, "dwd_security_bar"
    )
    path = source / "DWD" / f"payload/{table}/all/part-000000.parquet"
    native = pq.read_table(path)
    rows = native.to_pylist()
    if defect == "security_missing":
        rows = rows[:1]
    elif defect == "session_reuse":
        affected = [
            r
            for r in rows
            if r["venue_id"] == "venue0" and r["trade_date"] in [date(2020, 4, 1), date(2020, 4, 2)]
        ]
        affected[1]["calendar_session_id"] = affected[0]["calendar_session_id"]
    else:
        row = next(
            r
            for r in rows
            if r["listing_id"] == "list000001"
            and r["observation_start"].date() == date(2020, 3, 25)
        )
        if defect == "currency":
            row["currency"] = "USD"
            for name in ["open", "high", "low", "close"]:
                row[name] *= 2
        elif defect == "future_bar":
            row["observation_end"] += timedelta(days=1)
        else:
            row["volume"] = float("inf")
    pq.write_table(pa.Table.from_pylist(rows, schema=native.schema), path)
    closure = json.loads((project / "closure.json").read_bytes())
    for item in closure["members"]:
        if item["path"] == path.relative_to(source / "DWD").as_posix():
            item.update(sha256=file_sha(path), size_bytes=path.stat().st_size)
    (project / "closure.json").write_bytes(encoded(closure))
    request["closure"]["sha256"] = file_sha(project / "closure.json")  # type: ignore[index]
    request["closure"]["exact_bytes"] = sum(  # type: ignore[index]
        item["size_bytes"] for item in closure["members"]
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    if defect in {"session_reuse", "security_missing"}:
        with pytest.raises(
            ValueError, match="R7_SESSION_ID_DUPLICATE|R7_SECURITY_METADATA_COVERAGE"
        ):
            materialize(source, project, request, scratch)  # type: ignore[arg-type]
    else:
        data = materialize(source, project, request, scratch)  # type: ignore[arg-type]
        result = project_rows(data, ROOT, request)  # type: ignore[arg-type]
        assert result["summary"]["row_count"] == 66
        row = next(
            r
            for r in result["rows"]
            if r["identity"]["code"] == "000001" and r["identity"]["date"] == "2020-04-01"
        )
        assert row["operators"]["trend"]["status"] == "ABSTAIN"
        assert row["operators"]["structure"]["status"] == "X"
        assert row["unavailable_reasons"]
