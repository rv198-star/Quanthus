"""P10 supplementary-source units and first-observed time contracts (#587)."""

from __future__ import annotations

import calendar
import hashlib
import importlib
import importlib.metadata
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA = "p10-supplement-observation-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def instant(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("SOURCE_AWARE_CLOCK_REQUIRED")
    return stamp.astimezone(timezone.utc)


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def encode(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValueError("SOURCE_EXACT_DECIMAL_REQUIRED")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("SOURCE_EXACT_DECIMAL_REQUIRED") from exc
    if not result.is_finite():
        raise ValueError("SOURCE_FINITE_VALUE_REQUIRED")
    return result


def policy(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (
        value.get("kind") != "p10-supplement-policy-v1"
        or value.get("schema") != SCHEMA
        or not re.fullmatch(r"[a-f0-9]{40}", value["library"]["revision"])
        or not re.fullmatch(r"[a-f0-9]{64}", value["library"]["wheel_sha256"])
        or value["watch"]["automatic_business_admission"] is not False
    ):
        raise ValueError("SOURCE_POLICY_INVALID")
    if not 2 <= value["watch"]["days"] <= 30 or value["watch"]["interval_seconds"] < 3600:
        raise ValueError("SOURCE_WATCH_BUDGET_INVALID")
    if len({x["key"] for x in value["sources"]}) != len(value["sources"]):
        raise ValueError("SOURCE_DUPLICATE_CAPABILITY")
    return value


def library_contracts(config: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    astock = importlib.import_module("astock")
    bonds, disclosures, funds, lpr, macro, news, quotes, rss = (
        importlib.import_module("astock." + name)
        for name in ("bonds", "disclosures", "funds", "lpr", "macro", "news", "quotes", "rss")
    )

    if importlib.metadata.version("astock-sources") != config["library"]["version"]:
        raise ValueError("SOURCE_LIBRARY_VERSION_MISMATCH")
    for name, dependency in config["runtime_dependencies"].items():
        if importlib.metadata.version(name) != dependency["version"]:
            raise ValueError("SOURCE_RUNTIME_VERSION_MISMATCH")
    if astock.__file__ is None:
        raise ValueError("SOURCE_LIBRARY_FILE_REQUIRED")
    root = Path(astock.__file__).parent
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.py")}
    if actual != config["library"]["module_sha256"]:
        raise ValueError("SOURCE_LIBRARY_CONTENT_MISMATCH")
    contracts = {}
    for request in astock.capabilities():
        capability = request.capability
        parser: Any
        if capability.startswith("macro."):
            series = capability.split(".")[1]
            parser = partial(macro.parse, series=series)
        elif capability.startswith("news.roll."):
            channel = int(capability.split(".")[2])
            parser = partial(news.parse, channel=channel)
        else:
            parser = {
                "lpr.history": lpr.parse,
                "news.rss": rss.parse,
                "treasury.curve": bonds.parse,
                "disclosure.index": disclosures.parse,
                "quote.snapshot": quotes.parse,
                "fund.nav": funds.parse,
            }[capability]
        contracts[request.provider + ":" + capability] = (request, parser)
    if set(contracts) != {x["key"] for x in config["sources"]}:
        raise ValueError("SOURCE_CAPABILITY_SET_MISMATCH")
    return contracts


def period(series: str, text: str, marker: str) -> tuple[str, str]:
    if series == "gdp":
        match = re.fullmatch(r"([0-9]{4})年第1(?:-([1-4]))?季度", text)
        if match is None:
            raise ValueError("SOURCE_CUMULATIVE_QUARTER_REQUIRED")
        year, quarter = int(match[1]), int(match[2] or "1")
        month = quarter * 3
        start = date(year, 1, 1)
    else:
        match = re.fullmatch(r"([0-9]{4})年([0-9]{2})月份", text)
        if match is None:
            raise ValueError("SOURCE_MONTH_PERIOD_REQUIRED")
        year, month = map(int, match.groups())
        start = date(year, month, 1)
    if marker != date(year, month, 1).isoformat() + " 00:00:00":
        raise ValueError("SOURCE_PERIOD_MARKER_CONFLICT")
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start.isoformat(), end.isoformat()


def metric(value: Any, mapping: dict[str, Any]) -> dict[str, str]:
    native = number(value)
    return {
        "native_value": str(native),
        "native_unit": mapping["native_unit"],
        "value": str(native * Decimal(mapping["multiplier"])),
        "unit": mapping["unit"],
        "basis": mapping["basis"],
    }


def normalize(result: Any, spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    raw = result.observation
    acquired = instant(raw.acquired_at.isoformat())
    if raw.request.provider + ":" + raw.request.capability != spec["key"]:
        raise ValueError("SOURCE_NORMALIZATION_IDENTITY_MISMATCH")
    flags: set[str] = set()
    rows: list[dict[str, Any]] = []
    clocks: list[datetime] = []
    dates: list[date] = []
    status = result.status.value
    if status == "SUCCESS":
        for item in result.records:
            row: dict[str, Any] = {
                "knowledge_from": acquired.isoformat(),
                "knowledge_basis": "FIRST_OBSERVED_RESPONSE",
                "publication_at": None,
                "source_clock": None,
                "period_start": None,
                "period_end": None,
            }
            kind = spec["kind"]
            if kind == "macro":
                start, end = period(item.series, item.period_text, item.report_date_text)
                row.update(
                    record_key=item.report_date_text,
                    period_start=start,
                    period_end=end,
                    source_report_date_text=item.report_date_text,
                    source_period_text=item.period_text,
                    metrics={k: metric(v, spec["fields"][k]) for k, v in item.source_fields},
                )
            elif kind == "lpr":
                row.update(
                    record_key=item.effective_date.isoformat(),
                    effective_date=item.effective_date.isoformat(),
                    metrics={
                        "one_year": metric(item.one_year_percent, spec["fields"]["rate"]),
                        "five_year_plus": metric(item.five_year_percent, spec["fields"]["rate"]),
                    },
                )
            elif kind == "curve":
                row.update(
                    record_key=f"{item.curve_id}:{item.source_date}:{item.maturity_native}",
                    source_date=item.source_date.isoformat(),
                    curve_id=item.curve_id,
                    curve_name=item.curve_name,
                    metrics={
                        "maturity": metric(item.maturity_native, spec["fields"]["maturity"]),
                        "yield": metric(item.yield_native, spec["fields"]["yield"]),
                    },
                )
            elif kind == "fund":
                if item.fund_code not in config["fund_universe"]:
                    raise ValueError("SOURCE_FUND_SCOPE_MISMATCH")
                flags.add("FUND_IDENTITY_REQUEST_BOUND_NO_RESPONSE_ECHO")
                row.update(
                    record_key=f"{item.fund_code}:{item.valuation_date}",
                    fund_code=item.fund_code,
                    valuation_date=item.valuation_date.isoformat(),
                    identity_basis=item.identity_basis,
                    subscription_status=item.subscription_status,
                    redemption_status=item.redemption_status,
                    dividend_text=item.dividend_text,
                    metrics={
                        "unit_nav": metric(item.unit_nav_native, spec["fields"]["nav"]),
                        "accumulated_nav": metric(
                            item.accumulated_nav_native, spec["fields"]["accumulated_nav"]
                        ),
                        "daily_change": metric(
                            item.daily_change_percent, spec["fields"]["daily_change"]
                        ),
                    },
                )
            elif kind == "quote":
                if item.security != config["security_universe"][0]:
                    raise ValueError("SOURCE_SECURITY_SCOPE_MISMATCH")
                row.update(
                    record_key=item.security,
                    security=item.security,
                    source_name=item.name,
                    price_basis="SOURCE_SNAPSHOT_NO_ADJUSTMENT_INFERENCE",
                    source_clock_text=item.source_clock_text,
                    metrics={
                        k: metric(getattr(item, k + "_native"), spec["fields"]["price"])
                        for k in ("last", "open", "high", "low")
                    },
                )
                if item.source_clock is not None:
                    row["source_clock"] = instant(item.source_clock.isoformat()).isoformat()
                    row["source_clock_kind"] = "VENDOR_QUOTE_CLOCK_ASIA_SHANGHAI"
                else:
                    flags.add("SOURCE_QUOTE_CLOCK_UNAVAILABLE")
            elif kind == "disclosure":
                clock = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                    milliseconds=item.source_timestamp_ms
                )
                row.update(
                    record_key=item.announcement_id,
                    security_code=item.security_code,
                    organization_id=item.organization_id,
                    title=item.title,
                    source_timestamp_ms=item.source_timestamp_ms,
                    source_clock=clock.isoformat(),
                    source_clock_kind="VENDOR_INDEX_TIMESTAMP_NOT_PROVEN_PUBLICATION",
                    source_display_date=clock.astimezone(SHANGHAI).date().isoformat(),
                    attachment_path=item.attachment_path,
                )
            elif kind in {"rss", "news"}:
                row.update(record_key=item.native_id or item.url, title=item.title, url=item.url)
                stamp = item.published_at if kind == "rss" else item.source_created_at
                row["source_clock_text"] = (
                    item.published_text if kind == "rss" else item.source_ctime_text
                )
                if stamp is not None:
                    row["source_clock"] = instant(stamp.isoformat()).isoformat()
                    row["source_clock_kind"] = "PUBLISHER_TIME_CLAIM"
                else:
                    flags.add("SOURCE_PUBLICATION_CLOCK_UNAVAILABLE")
            else:
                raise ValueError("SOURCE_UNSUPPORTED_NORMALIZATION")
            for field in ("period_end", "source_date", "effective_date", "valuation_date"):
                if row.get(field):
                    dates.append(date.fromisoformat(row[field]))
            if row["source_clock"] is not None:
                clock = instant(row["source_clock"])
                clocks.append(clock)
                if clock > acquired + timedelta(seconds=config["quality"]["future_clock_seconds"]):
                    flags.add("SOURCE_CLOCK_IN_FUTURE")
            rows.append(row)
    if len(rows) != len(result.records):
        raise ValueError("SOURCE_NORMALIZATION_ROW_LOSS")
    latest = max(clocks) if clocks else None
    age = (acquired - latest).total_seconds() if latest else None
    maximum_age = spec.get("max_latest_age_seconds")
    if maximum_age is not None:
        if age is None:
            flags.add("CURRENT_NEWS_CLOCK_UNAVAILABLE")
        elif age > maximum_age:
            flags.add("STALE_FOR_CURRENT_NEWS")
    latest_date = max(dates) if dates else None
    date_age = (acquired.astimezone(SHANGHAI).date() - latest_date).days if latest_date else None
    if date_age is not None and date_age < 0:
        flags.add("SOURCE_DATE_IN_FUTURE")
    if spec["purpose"] == "archive_news_metadata":
        flags.add("ARCHIVE_ONLY_NOT_CURRENT_POLICY")
    quality_ok = status == "SUCCESS" and not flags.intersection(
        {
            "SOURCE_CLOCK_IN_FUTURE",
            "STALE_FOR_CURRENT_NEWS",
            "CURRENT_NEWS_CLOCK_UNAVAILABLE",
            "SOURCE_DATE_IN_FUTURE",
        }
    )
    return {
        "schema": SCHEMA,
        "key": spec["key"],
        "verified_at": acquired.isoformat(),
        "status": status,
        "reason": result.reason,
        "quality_ok": quality_ok,
        "quality_flags": sorted(flags),
        "purpose": spec["purpose"],
        "unit_contract": "VERIFIED_PER_FIELD" if spec.get("fields") else "METADATA_NO_NUMERIC_UNIT",
        "source_latest_clock": latest.isoformat() if latest else None,
        "source_latest_age_seconds": age,
        "source_latest_date": latest_date.isoformat() if latest_date else None,
        "source_latest_date_age_days": date_age,
        "source_row_count": len(result.records),
        "records": rows,
        "fallback_action": "NONE" if quality_ok else "ISOLATE_OPTIONAL_SOURCE",
        "historical_pit_admission": False,
        "business_admission": False,
    }
