"""Exact new-listing identities and unadjusted daily source observations.

Official listing bodies establish only the named listing date. Actual new
provider strings remain local evidence; they neither recover historical PIT nor
admit a consumer. The frozen August adapters and price policy are unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_precision import decimal_profile
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-official-new-listing-daily-observations-v1"
FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount", "tradestatus", "isST")
THROUGH = date(2026, 9, 30)
TZ = ZoneInfo("Asia/Shanghai")


def official_listing_date(body: str, *, code: str, venue: str) -> date:
    text = re.sub(r"\s+", "", body)
    exchange = {"SSE": "上海证券交易所", "SZSE": "深圳证券交易所"}.get(venue)
    dates = {
        date(int(y), int(m), int(d))
        for y, m, d in re.findall(
            r"(?:上市时间|上市日期)[：:](\d{4})年(\d{1,2})月(\d{1,2})日", text
        )
    }
    if (
        not re.fullmatch(r"\d{6}", code)
        or code not in text
        or exchange is None
        or exchange not in text
        or len(dates) != 1
    ):
        raise BoundedError("NEW_LISTING_OFFICIAL_DATE_CODE_VENUE_NOT_PROVEN")
    result = next(iter(dates))
    if not date(2026, 7, 26) <= result <= THROUGH:
        raise BoundedError("NEW_LISTING_OFFICIAL_DATE_OUTSIDE_SCOPE")
    return result


def project_identity(
    *,
    basic: dict[str, Any],
    issuer: dict[str, Any],
    notice: dict[str, Any],
    body: str,
    venue: dict[str, Any],
    observed_at: datetime,
    input_identity: str,
) -> dict[str, list[dict[str, Any]]]:
    """Bind the original native ID scheme to a verified official IPO body."""
    clock = instant(observed_at)
    code = issuer.get("code", "")
    exchange = {"XSHG": "SSE", "XSHE": "SZSE"}.get(venue.get("mic_code", ""))
    if exchange is None:
        raise BoundedError("NEW_LISTING_VENUE_UNQUALIFIED")
    provider_code = ("sh." if exchange == "SSE" else "sz.") + code
    if (
        basic.get("status") != "COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE"
        or basic.get("fields") != ["code", "code_name", "ipoDate", "outDate", "type", "status"]
        or basic.get("request", {}).get("code") != provider_code
        or len(basic.get("rows", [])) != 1
        or issuer.get("category") != "A股"
        or not issuer.get("orgId")
        or notice.get("code") != code
        or notice.get("orgId") != issuer["orgId"]
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", input_identity)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", notice.get("pdf_sha256", ""))
    ):
        raise BoundedError("NEW_LISTING_OFFICIAL_ISSUER_SOURCE_CLOSURE_INVALID")
    cells = basic["rows"][0]
    if (
        len(cells) != 6
        or cells[0] != provider_code
        or cells[4:] != ["1", "1"]
        or cells[3] != ""
        or cells[1] != issuer.get("zwjc")
    ):
        raise BoundedError("NEW_LISTING_PROVIDER_OFFICIAL_IDENTITY_CONFLICT")
    day = official_listing_date(body, code=code, venue=exchange)
    if (
        str(day) != cells[2]
        or instant(basic["completed_at"]) > clock
        or instant(notice["pdf_received_at"]) > clock
        or instant(notice["issuer_map_received_at"]) > clock
        or instant(venue["knowledge_from"]) > clock
        or venue.get("knowledge_to") is not None
        and clock >= instant(venue["knowledge_to"])
    ):
        raise BoundedError("NEW_LISTING_DATED_IDENTITY_OR_KNOWLEDGE_CONFLICT")
    compact = re.sub(r"\s+", "", body)
    if "人民币普通股" not in compact:
        raise BoundedError("NEW_LISTING_INSTRUMENT_CURRENCY_UNPROVEN")
    board = (
        "STAR"
        if exchange == "SSE" and "科创板" in compact
        else "ChiNext"
        if exchange == "SZSE" and "创业板" in compact
        else "SSE_main"
        if exchange == "SSE" and "主板" in compact
        else "SZ_main"
        if exchange == "SZSE" and "主板" in compact
        else None
    )
    if board is None:
        raise BoundedError("NEW_LISTING_OFFICIAL_BOARD_UNPROVEN")
    start = instant(datetime.combine(day, time(), TZ))
    company = _stable_id("company", "CNINFO_ORG_ID", issuer["orgId"])
    security = _stable_id("security", "ECONOMIC_SECURITY", exchange + ":" + code)
    listing = _stable_id("listing", security, venue["venue_id"], code, start.isoformat())
    common = dict(
        record_source="cninfo.official_ipo+baostock.basic",
        source_record_id=notice["announcement_id"],
        source_revision=CONTRACT_ID + ":" + input_identity,
        source_artifact_sha256=notice["pdf_sha256"],
        knowledge_from=clock,
        knowledge_to=None,
        effective_from=start,
        effective_to=None,
    )
    lineage = {**common, "security_id": security, "venue_id": venue["venue_id"]}
    return {
        "dwd_listed_company": [
            {
                **common,
                "company_id": company,
                "display_name": cells[1],
                "company_type": "provider_scoped_listed_company",
                "resolution_scope": "CNINFO_ORG_ID:PRIMARY",
            }
        ],
        "dwd_company_identifier": [
            {
                **common,
                "company_id": company,
                "identifier_namespace": "CNINFO_ORG_ID",
                "identifier_value": issuer["orgId"],
                "identifier_authority": "CNINFO",
            }
        ],
        "dwd_security": [
            {
                **common,
                "security_id": security,
                "company_id": company,
                "instrument_type": "common_equity",
                "instrument_currency": "CNY",
            }
        ],
        "dwd_security_identifier": [
            {**lineage, "identifier_namespace": namespace, "identifier_value": value}
            for namespace, value in [
                ("CN_STOCK_CODE:" + exchange, code),
                ("BAOSTOCK_SECURITY_ID", code),
                ("BAOSTOCK_CODE", provider_code),
            ]
        ],
        "dwd_listing": [
            {
                **lineage,
                "listing_id": listing,
                "listing_code": code,
                "board_code": board,
                "listing_currency": "CNY",
                "lot_size": None,
            }
        ],
    }


def project_daily(
    response: dict[str, Any],
    *,
    listing: dict[str, Any],
    calendars: list[dict[str, Any]],
    source_sha256: str,
    observed_at: datetime,
    input_identity: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Require exact post-IPO session closure; never fabricate a missing bar."""
    clock = instant(observed_at)
    request = response.get("request", {})
    code = listing["listing_code"]
    provider_code = request.get("code", "")
    if (
        response.get("status") != "COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE"
        or response.get("fields") != list(FIELDS)
        or request.get("fields") != ",".join(FIELDS)
        or not re.fullmatch(r"(?:sh|sz)\." + code, provider_code)
        or request.get("frequency") != "d"
        or request.get("adjustflag") != "3"
        or request.get("end_date") != str(THROUGH)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", input_identity)
        or instant(response["completed_at"]) > clock
    ):
        raise BoundedError("NEW_LISTING_DAILY_SOURCE_OR_CLOCK_INVALID")
    start = date.fromisoformat(request["start_date"])
    if (
        start != instant(listing["effective_from"]).astimezone(TZ).date()
        or not date(2026, 7, 26) <= start <= THROUGH
        or instant(listing["knowledge_from"]) > clock
        or listing.get("knowledge_to") is not None
        and clock >= instant(listing["knowledge_to"])
    ):
        raise BoundedError("NEW_LISTING_DAILY_LISTING_WINDOW_INVALID")
    calendar = {}
    for row in calendars:
        if (
            row["venue_id"] == listing["venue_id"]
            # The sealed native history uses REGULAR_DAY; August/September
            # projections use day. Both denote the daily session, not intraday
            # windows. Duplicate dates across these representations still fail.
            and row["session_type"] in {"day", "REGULAR_DAY"}
            and start <= row["trade_date"] <= THROUGH
            and row["is_open"] is True
            and instant(row["knowledge_from"]) <= clock
            and (row.get("knowledge_to") is None or clock < instant(row["knowledge_to"]))
        ):
            if row["trade_date"] in calendar:
                raise BoundedError("NEW_LISTING_CALENDAR_SESSION_NOT_UNIQUE")
            calendar[row["trade_date"]] = row
    rows = response.get("rows", [])
    if not rows or len(rows) > 100:
        raise BoundedError("NEW_LISTING_DAILY_ROWS_MISSING_OR_BOUND")
    bars, states, days = [], [], set()
    counts: Counter[str] = Counter()
    for cells in rows:
        if (
            not isinstance(cells, list)
            or len(cells) != len(FIELDS)
            or any(not isinstance(c, str) for c in cells)
            or cells[1] != provider_code
            or cells[8] not in {"0", "1"}
            or cells[9] not in {"0", "1"}
        ):
            raise BoundedError("NEW_LISTING_DAILY_ROW_CODE_OR_FLAGS_INVALID")
        day = date.fromisoformat(cells[0])
        if day not in calendar or day in days:
            raise BoundedError("NEW_LISTING_DAILY_DATE_CALENDAR_CLOSURE_INVALID")
        days.add(day)
        values: dict[str, Decimal | None] = {}
        for field, token in zip(
            ["open", "high", "low", "close", "volume", "turnover"], cells[2:8], strict=True
        ):
            if token == "" and field in {"volume", "turnover"}:
                values[field] = None
                counts["NULL"] += 1
                continue
            try:
                value = Decimal(token)
            except InvalidOperation as exc:
                raise BoundedError("NEW_LISTING_DAILY_DECIMAL_INVALID") from exc
            if not value.is_finite() or not decimal_profile(value, precision=38, scale=10)["fits"]:
                raise BoundedError("NEW_LISTING_DAILY_EXACT_DECIMAL_UNREPRESENTABLE")
            values[field] = value
            counts["ZERO" if value == 0 else "FINITE_EXACT"] += 1
        beginning = instant(datetime.combine(day, time(), TZ))
        if listing.get("effective_to") is not None and beginning >= instant(
            listing["effective_to"]
        ):
            raise BoundedError("NEW_LISTING_DAILY_LISTING_WINDOW_INVALID")
        common = dict(
            record_source="baostock.query_history_k_data_plus.adjustflag=3",
            source_record_id=f"code={provider_code}&date={day}",
            source_revision=CONTRACT_ID + ":" + input_identity,
            source_artifact_sha256=source_sha256,
            knowledge_from=clock,
            knowledge_to=None,
            observation_start=beginning,
            observation_end=beginning + timedelta(days=1),
            listing_id=listing["listing_id"],
            calendar_session_id=calendar[day]["calendar_session_id"],
        )
        bars.append(
            {
                **common,
                "interval_size": "1d",
                "price_basis": "UNADJUSTED",
                "currency": listing["listing_currency"],
                **values,
                "trade_count": None,
            }
        )
        tradable = cells[8] == "1"
        states.append(
            {
                **common,
                "is_tradable": tradable,
                "can_buy": tradable,
                "can_sell": tradable,
                "reason_code": "SOURCE_NOT_TRADABLE"
                if not tradable
                else "ST_TRADABLE"
                if cells[9] == "1"
                else "NORMAL",
                "upper_price": None,
                "lower_price": None,
                "input_revision_digest": input_identity,
            }
        )
        if not tradable:
            counts["NONTRADING_OBSERVATION"] += 1
    if days != set(calendar):
        raise BoundedError("NEW_LISTING_DAILY_EXACT_SESSION_SET_INCOMPLETE")
    output = {"dwd_security_bar": bars, "dwd_tradability_state": states}
    proof = dict(
        contract_id=CONTRACT_ID,
        source_sha256=source_sha256,
        input_identity=input_identity,
        raw_response_sha256=digest(response),
        listing_sha256=digest(listing),
        expected_sessions=len(calendar),
        actual_sessions=len(days),
        outputs_sha256=digest(output),
        numeric_classifications=dict(counts),
        original_response_received_at=response["completed_at"],
        new_knowledge=clock,
        provider_acquisition=None,
        raw_lexical_tokens_preserved_in_input=True,
        historical_PIT_admission=False,
        consumer_admission="NOT_GRANTED",
        price_limits_inferred=False,
    )
    return output, proof


def replay_scope(root: Any, *, sealed: bool = True) -> dict[str, Any]:
    """Rebuild this seven-table patch from only its local immutable evidence."""
    import json
    import subprocess
    from pathlib import Path

    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha, load_contract, schemas
    from .base22_sources import _tree, physical_path

    root = physical_path(Path(root))
    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("business_through") != str(THROUGH)
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("historical_PIT_admission") is not False
        or plan.get("evaluator_sha256") != file_sha(Path(__file__))
        or not 0 < len(plan.get("selection", [])) <= 23
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("NEW_LISTING_LOCAL_SCOPE_INVALID")
    members = plan["members"]
    markers = {"INPUTS.json", "REPLAY.json", "READY"} if sealed else {"INPUTS.json"}
    if _tree(root) != set(members) | markers:
        raise BoundedError("NEW_LISTING_LOCAL_PHYSICAL_SET_CHANGED")
    if sealed and json.loads((root / "READY").read_bytes()) != {
        "inputs_sha256": file_sha(root / "INPUTS.json"),
        "replay_sha256": file_sha(root / "REPLAY.json"),
    }:
        raise BoundedError("NEW_LISTING_LOCAL_SEAL_CHANGED")

    def local(name: str) -> Path:
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or name not in members:
            raise BoundedError("NEW_LISTING_LOCAL_REFERENCE_INVALID")
        path = physical_path(root / rel)
        item = members[name]
        if (
            path.stat().st_nlink != 1
            or path.stat().st_size != item["bytes"]
            or file_sha(path) != item["sha256"]
        ):
            raise BoundedError("NEW_LISTING_LOCAL_BYTES_CHANGED")
        return path

    for name in members:
        local(name)
    _, model, extension = load_contract(root / "contract")
    row_schemas = schemas(model, extension)
    calendars = pq.ParquetFile(local(plan["calendar"])).read().to_pylist()
    venues = pq.ParquetFile(local(plan["venues"])).read().to_pylist()
    by_mic = {v["mic_code"]: v for v in venues}
    issuer_rows = json.loads(local(plan["issuer_map"]).read_bytes())["stockList"]
    issuer_by_code = {r["code"]: r for r in issuer_rows}
    observed = instant(plan["observed_at"])
    output: dict[str, list[dict[str, Any]]] = {}
    proofs = []
    codes = set()
    for spec in plan["selection"]:
        code = spec["code"]
        if code in codes:
            raise BoundedError("NEW_LISTING_SCOPE_DUPLICATE_SECURITY")
        codes.add(code)
        closure = {
            "code": code,
            "observed_at": plan["observed_at"],
            "evaluator_sha256": plan["evaluator_sha256"],
            "parent_manifest_sha256": plan["parent_manifest_sha256"],
            "sources": {
                k: members[spec[k]]["sha256"] for k in ["basic", "daily", "official_index", "pdf"]
            },
            "parents": {
                k: members[plan[k]]["sha256"] for k in ["calendar", "venues", "issuer_map"]
            },
        }
        if spec["input_identity"] != digest(closure):
            raise BoundedError("NEW_LISTING_SEMANTIC_INPUT_CLOSURE_CHANGED")
        basic = json.loads(local(spec["basic"]).read_bytes())
        daily = json.loads(local(spec["daily"]).read_bytes())
        original_index = json.loads(local(spec["official_index"]).read_bytes())
        matching = [
            r
            for r in original_index.get("announcements", [])
            if str(r.get("announcementId")) == spec["notice"]["announcement_id"]
        ]
        if (
            len(matching) != 1
            or matching[0].get("orgId") != spec["notice"]["orgId"]
            or matching[0].get("secCode") != code
            or matching[0].get("adjunctUrl") != spec["notice"]["adjunctUrl"]
            or file_sha(local(spec["pdf"])) != spec["notice"]["pdf_sha256"]
        ):
            raise BoundedError("NEW_LISTING_OFFICIAL_INDEX_BODY_BINDING_CHANGED")
        result = subprocess.run(
            ["pdftotext", "-layout", str(local(spec["pdf"])), "-"],
            capture_output=True,
            timeout=30,
            check=True,
        )
        body = result.stdout.decode("utf-8")
        identity = project_identity(
            basic=basic,
            issuer=issuer_by_code[code],
            notice=spec["notice"],
            body=body,
            venue=by_mic[spec["mic"]],
            observed_at=observed,
            input_identity=spec["input_identity"],
        )
        rows, proof = project_daily(
            daily,
            listing=identity["dwd_listing"][0],
            calendars=calendars,
            source_sha256=file_sha(local(spec["daily"])),
            observed_at=observed,
            input_identity=spec["input_identity"],
        )
        for table, values in {**identity, **rows}.items():
            typed = pa.Table.from_pylist(values, schema=row_schemas[table])
            if typed.to_pylist() != values or any(
                not f.nullable and typed.column(f.name).null_count for f in row_schemas[table]
            ):
                raise BoundedError("NEW_LISTING_NATIVE_EXACT_SERIALIZATION_CHANGED")
            output.setdefault(table, []).extend(typed.to_pylist())
        proofs.append(proof)
    if set(output) != set(plan["outputs"]):
        raise BoundedError("NEW_LISTING_OUTPUT_TABLE_SET_CHANGED")
    definitions = {row["name"]: row for row in model["tables"]}
    for table, values in output.items():
        keys = [tuple(row[field] for field in definitions[table]["row_key"]) for row in values]
        if len(set(keys)) != len(keys):
            raise BoundedError("NEW_LISTING_GLOBAL_NATIVE_KEY_NOT_UNIQUE")
        pf = pq.ParquetFile(local(plan["outputs"][table]))
        if (
            not pf.schema_arrow.equals(row_schemas[table], check_metadata=False)
            or pf.read().to_pylist() != values
        ):
            raise BoundedError("NEW_LISTING_NATIVE_OUTPUT_REPLAY_CHANGED")
    report = dict(
        status="PASS_LOCAL_RAW_OFFICIAL_AND_NATIVE_REPLAY",
        contract_id=CONTRACT_ID,
        input_identity=plan["identity"],
        securities=len(codes),
        rows={t: len(v) for t, v in output.items()},
        semantic_sha256=digest(output),
        proof_sha256=digest(proofs),
        historical_PIT_admission=False,
        consumer_admission="NOT_GRANTED",
        complete_Base=False,
    )
    if sealed and json.loads((root / "REPLAY.json").read_bytes()) != report:
        raise BoundedError("NEW_LISTING_SEALED_REPLAY_CHANGED")
    return report
