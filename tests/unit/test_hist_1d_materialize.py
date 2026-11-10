from __future__ import annotations

import gzip
import hashlib
import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.hist_1d_materialize import (
    capture_payload,
    daily_records,
    factor_records,
    official_names,
    subtype,
)


def test_status_subtypes_require_official_name_and_preserve_binary_unknown() -> None:
    changes = [(date(2005, 1, 4), "S*ST样本"), (date(2005, 1, 7), "ST样本")]
    assert subtype("1", date(2005, 1, 5), changes)[0] == "DELISTING_RISK"
    assert subtype("1", date(2005, 1, 7), changes)[0] == "ST"
    assert subtype("1", date(2005, 1, 5), [])[0] == "UNKNOWN"
    assert subtype("0", date(2005, 1, 5), [])[0] == "NORMAL"
    assert subtype("0", date(1995, 1, 5), [])[0] == "UNKNOWN"


def test_daily_decimal_units_suspension_and_acquisition_clock() -> None:
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "tradestatus",
        "isST",
    ]
    acquired = "2026-11-11T00:00:00+00:00"
    payload = {
        "error_code": "0",
        "acquisition_completed_at": acquired,
        "fields": fields,
        "rows": [
            ["2005-01-04", "sz.000001", "1.0000000001", "2", "1", "1.5", "123", "180.25", "1", "0"],
            ["2005-01-05", "sz.000001", "", "", "", "", "0", "0", "0", "1"],
        ],
    }
    task = {
        "listing_id": "l",
        "security_id": "s",
        "code": "sz.000001",
        "venue_id": "v",
        "start_date": "2005-01-01",
        "end_date": "2005-01-31",
    }
    sessions = {
        ("v", date(2005, 1, d)): (
            f"session-{d}",
            datetime(2005, 1, d, 1, 30, tzinfo=timezone.utc),
            datetime(2005, 1, d, 7, tzinfo=timezone.utc),
            d,
        )
        for d in [4, 5]
    }
    rows = daily_records(payload, "sha256:" + "a" * 64, task, sessions, [], None, None)
    assert len(rows["bar"]) == 1
    bar = rows["bar"][0]
    assert bar["open"] == Decimal("1.0000000001")
    assert bar["volume"] == Decimal(123) and bar["turnover"] == Decimal("180.25")
    assert bar["knowledge_from"].isoformat() == acquired
    assert rows["listing-status"][0]["status_value"] == "SUSPENDED"
    assert any(
        r["status_type"] == "PROVIDER_DAILY_STATE" and r["status_value"] == "ST"
        for r in rows["security-status"]
    )
    payload["rows"][0][1] = "sh.600601"
    with pytest.raises(ValueError, match="DAILY_SOURCE_IDENTITY_INVALID"):
        daily_records(payload, "sha256:" + "a" * 64, task, sessions, [], None, None)


def test_native_factor_uses_lexical_native_column_and_separate_knowledge_clock() -> None:
    payload = {
        "error_code": "0",
        "acquisition_completed_at": "2026-11-11T00:00:00+00:00",
        "fields": [
            "code",
            "dividOperateDate",
            "foreAdjustFactor",
            "backAdjustFactor",
            "adjustFactor",
        ],
        "rows": [["sh.600601", "2010-07-12", "0.878719", "5680.849855", "5680.849855"]],
    }
    rows = factor_records(
        payload, "sha256:" + "a" * 64, {"listing_id": "l", "security_id": "s", "code": "sh.600601"}
    )
    assert rows[0]["factor_value"] == Decimal("5680.849855")
    assert rows[0]["adjustment_direction"] == "PROVIDER_NATIVE"
    assert rows[0]["effective_from"].isoformat() == "2010-07-11T16:00:00+00:00"


def test_official_xlsx_accepts_inline_headers(tmp_path: Path) -> None:
    cells = ["变更日期", "证券代码", "证券简称", "变更前简称", "变更后简称"]
    data = ["2005-01-04", "000001", "样本", "样本", "*ST样本"]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for values in [cells, data]:
        xml += (
            "<row>"
            + "".join('<c t="inlineStr"><is><t>' + v + "</t></is></c>" for v in values)
            + "</row>"
        )
    xml += "</sheetData></worksheet>"
    path = tmp_path / "names.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        )
        z.writestr("xl/worksheets/sheet1.xml", xml)
    assert official_names(path) == {"000001": [(date(2005, 1, 4), "*ST样本")]}


def test_session_recovery_preserves_failed_bytes_and_resolves_new_lineage(tmp_path: Path) -> None:
    root = tmp_path / "raw/baostock/l"
    root.mkdir(parents=True)
    old = root / "daily.json.gz"
    old.write_bytes(gzip.compress(json.dumps({"error_code": "10001001", "rows": []}).encode()))
    old_sha = hashlib.sha256(old.read_bytes()).hexdigest()
    before = old.read_bytes()
    old.with_suffix(".gz.receipt.json").write_text(
        json.dumps({"plan_sha256": "plan", "sha256": old_sha, "error_code": "10001001"})
    )
    new = root / "daily.session-recovery-1.json.gz"
    new.write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "error_code": "0",
                    "rows": [["value"]],
                    "supersedes_failed_capture_sha256": old_sha,
                }
            ).encode()
        )
    )
    new_sha = hashlib.sha256(new.read_bytes()).hexdigest()
    new.with_suffix(".gz.receipt.json").write_text(
        json.dumps({"plan_sha256": "plan", "sha256": new_sha, "error_code": "0"})
    )
    payload, digest = capture_payload(old, "plan")
    assert payload["rows"] == [["value"]] and digest == "sha256:" + new_sha
    assert payload["_capture_relative_path"].endswith("daily.session-recovery-1.json.gz")
    assert old.read_bytes() == before
