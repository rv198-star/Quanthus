"""Bounded one-day source supplement, never a mutation of a sealed Base/P6 input."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base24_execution_evidence import _require
from .base24_status_evidence import _instant

CONTRACT = "p10-one-day-unadjusted-observation.v1"
FIELDS = "date,code,open,high,low,close,volume,amount,isST,tradestatus"


def project_observation(
    raw: dict[str, Any],
    receipt: dict[str, Any],
    listing: dict[str, Any],
    calendar: dict[str, Any],
    *,
    source_sha256: str,
    expected_code: str,
    expected_day: date,
    repair_at: datetime,
) -> dict[str, Any]:
    """Reuse the native daily observation/state convention with exact Decimal values.

    Acquisition is the new observed retrieval, never provider publication. Price
    limits remain null here and must be resolved by the separate frozen policy.
    """
    query = raw["query"]
    _require(
        query == receipt["query"]
        and query["code"] == "sz." + expected_code
        and query["start_date"] == query["end_date"] == expected_day.isoformat()
        and query["frequency"] == "d"
        and query["adjustflag"] == "3"
        and query["fields"] == FIELDS,
        "EXACT_SINGLE_DAY_SCOPE",
    )
    _require(
        raw["error_code"] == receipt["query_error_code"] == "0"
        and receipt["error"] is None
        and receipt["body_sha256"] == source_sha256
        and receipt["rows"] == len(raw["rows"]) == 1,
        "RAW_OBSERVATION_UNAVAILABLE",
    )
    row = raw["rows"][0]
    _require(row["code"] == query["code"] and row["date"] == expected_day.isoformat(), "RAW_KEY")
    acquired = _instant(receipt["completed_at"])
    _require(_instant(receipt["started_at"]) <= acquired <= repair_at, "ACTUAL_ACQUISITION")
    _require(
        listing["listing_code"] == expected_code
        and listing["venue_id"] == calendar["venue_id"]
        and calendar["trade_date"] == expected_day
        and calendar["is_open"] is True
        and calendar["session_type"] == "day",
        "LISTING_CALENDAR",
    )
    start, end = calendar["observation_start"], calendar["observation_end"]
    _require(
        start.astimezone(ZoneInfo("Asia/Shanghai")).date() == expected_day
        and start < end
        and listing["effective_from"] <= start
        and (listing["effective_to"] is None or start < listing["effective_to"]),
        "OBSERVATION_INTERVAL",
    )
    _require(
        all(
            x["knowledge_from"] is not None and x["knowledge_from"] <= repair_at
            for x in (listing, calendar)
        ),
        "IDENTITY_KNOWLEDGE",
    )
    values = {}
    for name in ("open", "high", "low", "close", "volume", "amount"):
        _require(isinstance(row[name], str), "DECIMAL_SOURCE_TYPE")
        try:
            value = Decimal(row[name])
            _require(value.is_finite() and value >= 0, "DECIMAL_SOURCE_VALUE")
            # Value/scale must survive the native decimal128(38,10) encoding.
            from decimal import localcontext

            with localcontext() as ctx:
                ctx.prec = 60
                _require(
                    value.quantize(Decimal("0.0000000001")) == value and value < Decimal(10) ** 28,
                    "DECIMAL_NATIVE_PRECISION",
                )
        except InvalidOperation as exc:
            raise ValueError("DECIMAL_SOURCE_VALUE") from exc
        values[name] = value
    _require(row["isST"] in {"0", "1"} and row["tradestatus"] in {"0", "1"}, "STATUS_UNKNOWN")
    trading = row["tradestatus"] == "1"
    if trading:
        _require(all(values[k] > 0 for k in values), "TRADING_NONPOSITIVE")
    _require(
        values["low"]
        <= min(values["open"], values["close"])
        <= max(values["open"], values["close"])
        <= values["high"],
        "OHLC_RANGE",
    )
    lineage = dict(
        record_source=CONTRACT,
        source_record_id=f"baostock:{query['code']}:{expected_day}:d:3",
        source_revision=CONTRACT,
        source_artifact_sha256=source_sha256,
        knowledge_from=acquired,
        knowledge_to=None,
        observation_start=start,
        observation_end=end,
        listing_id=listing["listing_id"],
        calendar_session_id=calendar["calendar_session_id"],
    )
    bar = dict(
        **lineage,
        interval_size="1d",
        price_basis="UNADJUSTED",
        currency=listing["listing_currency"],
        **{k: values[k] for k in ("open", "high", "low", "close", "volume")},
        turnover=values["amount"],
        trade_count=None,
    )
    state = dict(
        **lineage,
        is_tradable=trading,
        can_buy=trading,
        can_sell=trading,
        reason_code="SOURCE_NOT_TRADABLE"
        if not trading
        else "ST_TRADABLE"
        if row["isST"] == "1"
        else "NORMAL",
        upper_price=None,
        lower_price=None,
        input_revision_digest=digest([source_sha256, listing, calendar]),
    )
    return dict(
        contract_id=CONTRACT,
        bar=bar,
        state=state,
        dated_status=dict(
            stock_code=expected_code,
            market_date=expected_day,
            status_value="ST" if row["isST"] == "1" else "NORMAL",
            reason="PROVIDER_TRADING" if trading else "PROVIDER_NOT_TRADING",
            known=acquired,
            evidence=source_sha256,
        ),
        provider_acquired_at=None,
        repair_known_at=repair_at,
        consumer_admission_granted=False,
        historical_pit_granted=False,
    )


def verify_single_day(root: Any, expected_manifest: str) -> dict[str, Any]:
    """Rebuild the one-day rows from embedded originals in a disconnected process."""
    import json
    from pathlib import Path

    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import canonical, file_sha
    from .base22_sources import _tree, physical_path

    root = physical_path(Path(root))
    _require(file_sha(root / "manifest.json") == expected_manifest, "MANIFEST_HASH")
    manifest = json.loads((root / "manifest.json").read_bytes())
    expected = {
        "baostock-002569-20260803.private.json",
        "baostock-002569-20260803.receipt.json",
        "dwd_listing.parquet",
        "dwd_trading_calendar.parquet",
        "source-manifest.json",
        "dwd_security_bar.parquet",
        "dwd_tradability_state.parquet",
        "plan.json",
        "result.private.json",
    }
    _require(
        set(manifest["files"]) == expected
        and set(_tree(root)) == expected | {"manifest.json", "READY"},
        "EXACT_FILE_SET",
    )
    _require((root / "READY").read_text().strip() == expected_manifest, "READY_HASH")
    for name, sha in manifest["files"].items():
        _require(file_sha(root / name) == sha, "FILE_HASH")
    plan = json.loads((root / "plan.json").read_bytes())
    _require(
        plan["module_sha256"] == file_sha(Path(__file__))
        and plan["consumer_admission_granted"] is False
        and manifest["consumer_admission_granted"] is False
        and manifest["contract"] == CONTRACT,
        "ENGINE_OR_PURPOSE",
    )
    source = json.loads((root / "source-manifest.json").read_bytes())
    _require(
        file_sha(root / "source-manifest.json") == plan["source_manifest_sha256"], "SOURCE_IDENTITY"
    )
    for name in ("dwd_listing", "dwd_trading_calendar"):
        _require(
            file_sha(root / (name + ".parquet"))
            == source["files"]["native/" + name + ".parquet"]["sha256"],
            "SOURCE_MEMBERSHIP",
        )
    cutoff = _instant(plan["snapshot_cutoff"])
    day = date.fromisoformat(plan["expected_day"])
    start = datetime.combine(day, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    c = duckdb.connect()
    c.execute("SET memory_limit='512MB'")
    c.execute("SET threads=1")
    try:

        def rows(sql: str, args: list[Any]) -> list[dict[str, Any]]:
            cur = c.execute(sql, args)
            return [
                dict(zip([x[0] for x in cur.description], r, strict=True)) for r in cur.fetchall()
            ]

        ls = rows(
            """SELECT DISTINCT * FROM read_parquet(?) WHERE listing_code=?
          AND knowledge_from<=? AND coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>?
          AND effective_from<=? AND coalesce(effective_to,'infinity'::TIMESTAMPTZ)>?""",
            [
                str(root / "dwd_listing.parquet"),
                plan["expected_code"],
                cutoff,
                cutoff,
                start,
                start,
            ],
        )
        _require(len(ls) == 1, "LISTING_CARDINALITY")
        cal = rows(
            """SELECT DISTINCT * FROM read_parquet(?) WHERE venue_id=? AND trade_date=?
          AND session_type='day' AND knowledge_from<=?
          AND coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>?""",
            [str(root / "dwd_trading_calendar.parquet"), ls[0]["venue_id"], day, cutoff, cutoff],
        )
        _require(len(cal) == 1, "CALENDAR_CARDINALITY")
        raw = root / "baostock-002569-20260803.private.json"
        result = project_observation(
            json.loads(raw.read_bytes()),
            json.loads((root / "baostock-002569-20260803.receipt.json").read_bytes()),
            ls[0],
            cal[0],
            source_sha256=file_sha(raw),
            expected_code=plan["expected_code"],
            expected_day=day,
            repair_at=_instant(plan["repair_known_at"]),
        )
        _require(
            (root / "result.private.json").read_bytes() == canonical(result), "RECOMPUTED_SEMANTICS"
        )
        for name, key in (("dwd_security_bar", "bar"), ("dwd_tradability_state", "state")):
            table = pq.read_table(root / (name + ".parquet"))
            _require(
                table.to_pylist() == [result[key]] and set(table.column_names) == set(result[key]),
                "NATIVE_ROUNDTRIP",
            )
            for column in (
                ("open", "high", "low", "close", "volume", "turnover")
                if key == "bar"
                else ("upper_price", "lower_price")
            ):
                _require(
                    str(table.schema.field(column).type) == "decimal128(38, 10)",
                    "NATIVE_DECIMAL_SCHEMA",
                )
        return dict(
            manifest_sha256=expected_manifest,
            bar_rows=1,
            state_rows=1,
            semantic_reconstruction=True,
            consumer_admission_granted=False,
            historical_pit_granted=False,
        )
    finally:
        c.close()
