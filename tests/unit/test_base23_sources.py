"""September opt-in source gates never alter August or invent missing sessions."""

from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    SourceAssessmentError,
    assess_package,
)
from quanthus.pipelines.securities_warehouse.base23_sources import (
    CONTRACT_ID,
    DAILY_REQUIRED,
    EXPECTED_SESSIONS,
    assess_september_release,
    coverage_census,
)
from tests.unit.test_base22_real_sources import _fixture


def september_source(tmp_path, day):
    import json

    root = _fixture(tmp_path / "input")
    path = root / "release.json"
    value = json.loads(path.read_text())
    value["market_date"] = day
    path.write_text(json.dumps(value))
    (root / "READY").write_text(file_sha(path))
    return root


def calendar():
    item = {
        "trading_dates": EXPECTED_SESSIONS,
        "source_url": "https://official.invalid/synthetic-notice",
        "source_sha256": "sha256:" + "1" * 64,
        "observed_at": "2026-10-09T00:00:00+00:00",
    }
    return {
        "timezone": "Asia/Shanghai",
        "start": "2026-09-01",
        "through": "2026-09-30",
        "venues": {"SSE": item, "SZSE": dict(item)},
    }


def report(day, *, rows=1, status="PASS", revision="synthetic"):
    return {
        "base23_contract_id": CONTRACT_ID,
        "status": status,
        "identity": {"identity": revision, "schedule": "daily", "market_date": day},
        "files": [{"name": name, "rows": rows} for name in sorted(DAILY_REQUIRED)],
    }


def test_september_requires_opt_in_and_does_not_relax_august(tmp_path):
    root = september_source(tmp_path, "2026-09-30")
    frozen = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(SourceAssessmentError, match="SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE"):
        assess_package(root, manifest_name="release.json")
    result = assess_september_release(root, manifest_sha256=file_sha(root / "release.json"))
    assert result["status"] == "PASS"
    assert result["identity"]["market_date"] == "2026-09-30"
    assert frozen == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize("day", ["2026-08-31", "2026-10-01"])
def test_september_rejects_other_business_months(tmp_path, day):
    root = september_source(tmp_path, day)
    with pytest.raises(SourceAssessmentError, match="SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE"):
        assess_september_release(root, manifest_sha256=file_sha(root / "release.json"))


@pytest.mark.parametrize("window", [(date(2026, 9, 30), date(2026, 9, 1)), ("a", "b"), ()])
def test_invalid_explicit_window_rejects_before_source_read(tmp_path, window):
    with pytest.raises(SourceAssessmentError, match="INVALID_ODS_BUSINESS_WINDOW"):
        assess_package(tmp_path / "nonexistent", ods_business_window=window)


def test_known_23rd_source_does_not_claim_month_end_complete():
    result = coverage_census([report("2026-09-23")], calendar_evidence=calendar())
    assert result["status"] == "BLOCKED_REQUIRED_DAILY_SOURCE"
    assert set(result["missing_core_dates_by_venue"]["SSE"]) >= {
        "2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30"
    }
    assert result["complete_Base23"] is result["candidate_READY"] is False


@pytest.mark.parametrize("fault", ["zero", "missing", "duplicate", "bad_hash"])
def test_incomplete_or_conflicting_core_cannot_fill_calendar_day(fault):
    value = report("2026-09-24")
    if fault == "zero":
        value["files"][0]["rows"] = 0
        value["files"] = [{"name": n, "rows": 0} for n in sorted(DAILY_REQUIRED)]
    elif fault == "missing":
        value["files"] = [i for i in value["files"] if i["name"] != "stock_list"]
    elif fault == "duplicate":
        value["files"].append(dict(value["files"][0]))
        with pytest.raises(SourceAssessmentError, match="DUPLICATE_OR_UNKNOWN"):
            coverage_census([value], calendar_evidence=calendar())
        return
    else:
        value["status"] = "FAIL"
    result = coverage_census([value], calendar_evidence=calendar())
    assert "2026-09-24" in result["missing_core_dates_by_venue"]["SSE"]


def test_calendar_only_nontrading_release_and_multiple_revisions_stay_distinct():
    values = [report("2026-09-23", revision="v1"), report("2026-09-23", revision="v2")]
    weekend = report("2026-09-19")
    weekend["files"] = [{"name": "trading_calendar", "rows": 1}]
    values.append(weekend)
    result = coverage_census(values, calendar_evidence=calendar())
    assert result["observed_core_dates"] == ["2026-09-23"]
    assert result["core_revisions_by_date"]["2026-09-23"] == ["v1", "v2"]


def test_all_necessary_dates_is_not_33_domain_or_row_qualification():
    result = coverage_census(
        [report(day) for day in calendar()["venues"]["SSE"]["trading_dates"]],
        calendar_evidence=calendar(),
    )
    assert result["status"] == "NECESSARY_GATE_PASS_ROW_AND_33_DOMAIN_QUALIFICATION_NOT_RUN"
    assert result["complete_Base23"] is False
    assert result["BSE_coverage"] == "NOT_ESTABLISHED_BY_SSE_SZSE_CALENDAR"


def test_absent_authoritative_calendar_fails_closed():
    with pytest.raises(SourceAssessmentError, match="AUTHORITATIVE_CALENDAR_REQUIRED"):
        coverage_census([], calendar_evidence={})


def test_shrunk_calendar_cannot_hide_the_four_missing_sessions():
    value = calendar()
    value["venues"]["SSE"] = dict(value["venues"]["SSE"], trading_dates=["2026-09-23"])
    with pytest.raises(SourceAssessmentError, match="AUTHORITATIVE_CALENDAR_REQUIRED"):
        coverage_census([report("2026-09-23")], calendar_evidence=value)
