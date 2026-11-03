"""Replay an observed closing auction alongside unchanged BaoStock 5m originals.

The compound envelope is a processing representation, not a BaoStock response.
No daily-bar field is an input to reconstruction. Daily gates run separately.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

PROVIDER = "baostock+tencent-observed-closing-auction-5m"
CONTRACT = "p10-observed-closing-auction-supplement-v1"
PHASE = "continuous_and_closing_auction"


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def positive(value: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("AUCTION_EXACT_STRING_REQUIRED")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise ValueError("AUCTION_POSITIVE_VALUE_REQUIRED")
    return number


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("AUCTION_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def captured(entry: dict[str, Any], *, url: str) -> bytes:
    raw = base64.b64decode(entry["raw_base64"], validate=True)
    if entry["url"] != url or sha(raw) != entry["sha256"]:
        raise ValueError("AUCTION_CAPTURE_BINDING_INVALID")
    if instant(entry["started_at"]) > instant(entry["completed_at"]):
        raise ValueError("AUCTION_CAPTURE_CLOCK_INVALID")
    return raw


def js(raw: bytes, variable: str) -> list[Any]:
    text = raw.decode("utf-8").strip().rstrip(";")
    prefix = variable + "="
    if not text.startswith(prefix):
        raise ValueError("AUCTION_SYMBOL_IDENTITY_INVALID")
    result = json.loads(text[len(prefix) :])
    if not isinstance(result, list) or len(result) != 2:
        raise ValueError("AUCTION_JS_SHAPE_INVALID")
    return result


def observed(value: dict[str, Any], code: str, day: str) -> dict[str, Any]:
    if (
        value.get("provider") != PROVIDER
        or value.get("contract") != CONTRACT
        or value.get("stock_code") != code
        or value.get("market_date") != day
        or not re.fullmatch(r"6\d{5}", code)
    ):
        raise ValueError("AUCTION_SCOPE_INVALID")
    evidence = value["evidence"]
    close_at = instant(day + "T15:00:00+08:00")
    if any(instant(entry["started_at"]) < close_at for entry in evidence.values()):
        raise ValueError("AUCTION_PRE_CLOSE_CAPTURE_INVALID")
    prefix = "https://stock.gtimg.cn/data/index.php?appn=detail&c=sh" + code
    counts = js(
        captured(evidence["count"], url=prefix + "&action=datacount"), "v_detail_time_sh" + code
    )
    if counts[0] != int(day.replace("-", "")) or not isinstance(counts[1], str):
        raise ValueError("AUCTION_MARKET_DATE_INVALID")
    ranges = counts[1].split("|")
    page = len(ranges) - 1
    details = js(
        captured(evidence["ticks"], url=prefix + "&action=data&p=" + str(page)),
        "v_detail_data_sh" + code,
    )
    if details[0] != page or not isinstance(details[1], str):
        raise ValueError("AUCTION_PAGE_INVALID")
    ticks = [row.split("/") for row in details[1].split("|")]
    if any(len(row) != 7 for row in ticks):
        raise ValueError("AUCTION_TICK_SHAPE_INVALID")
    selected = [row for row in ticks if "15:00:00" <= row[1] <= "15:00:05"]
    if len(selected) != 1:
        raise ValueError("AUCTION_SINGLE_MATCH_REQUIRED")
    tick = selected[0]
    price = positive(tick[2])
    if code.startswith("688"):
        url = "https://ifzq.gtimg.cn/appstock/app/minute/query?code=sh" + code
        data = json.loads(captured(evidence["minute"], url=url))
        if data.get("code") != 0 or set(data["data"]) != {"sh" + code}:
            raise ValueError("AUCTION_MINUTE_SYMBOL_INVALID")
        series = data["data"]["sh" + code]["data"]
        if series["date"] != day.replace("-", ""):
            raise ValueError("AUCTION_MINUTE_DATE_INVALID")
        records = [row.split() for row in series["data"]]
        before = [row for row in records if row[0] == "1459"]
        after = [row for row in records if row[0] == "1500"]
        if len(before) != 1 or len(after) != 1 or len(before[0]) != 4 or len(after[0]) != 4:
            raise ValueError("AUCTION_CUMULATIVE_PAIR_INVALID")
        if positive(after[0][1]) != price:
            raise ValueError("AUCTION_INDEPENDENT_PRICE_DIFFERS")
        volume = positive(after[0][2]) - positive(before[0][2])
        amount = positive(after[0][3]) - positive(before[0][3])
        rule = "tencent_kcb_cumulative_shares_and_amount_1500_minus_1459"
    else:
        # Tencent detail volume for SSE main-board is in 100-share lots.
        volume = positive(tick[4]) * 100
        amount = positive(tick[5])
        rule = "tencent_sse_mainboard_tick_lots_times_100_and_reported_amount"
    if volume <= 0 or volume != volume.to_integral_value() or amount <= 0:
        raise ValueError("AUCTION_QUANTITY_INVALID")
    # Amount is the supplier's displayed aggregate, not inferred exchange precision.
    delta = Decimal(tick[3])
    if not delta.is_finite():
        raise ValueError("AUCTION_PRICE_DELTA_INVALID")
    return {
        "price": price,
        "prior_price": price - delta,
        "volume": volume,
        "amount": amount,
        "rule": rule,
        "tick_time": tick[1],
        "amount_precision": "supplier_reported",
    }


def replay(raw: bytes, code: str, day: str) -> dict[str, dict[str, Decimal]]:
    from .native5m_daily_source import native_m5

    value = json.loads(raw)
    auction = observed(value, code, day)
    original = base64.b64decode(value["base_raw_base64"], validate=True)
    if sha(original) != value["base_raw_sha256"] or (
        json.loads(original).get("provider") != "baostock-native-5m"
    ):
        raise ValueError("AUCTION_BASE_ORIGINAL_INVALID")
    bars = native_m5(original, code, day)
    terminal = day.replace("-", "") + "1500"
    if len(bars) != 48 or terminal not in bars:
        raise ValueError("AUCTION_COMPLETE_BASE_GRID_REQUIRED")
    final = bars[terminal]
    if final["close"] != auction["prior_price"]:
        raise ValueError("AUCTION_CONTINUOUS_PRIOR_PRICE_DIFFERS")
    bars[terminal] = {
        **final,
        "high": max(final["high"], auction["price"]),
        "low": min(final["low"], auction["price"]),
        "close": auction["price"],
        "volume_shares": final["volume_shares"] + auction["volume"],
        "amount": final["amount"] + auction["amount"],
    }
    return bars


def phase(raw: bytes, stamp: str) -> str:
    value = json.loads(raw)
    if value.get("provider") == PROVIDER and stamp.endswith("1500"):
        return PHASE
    return "continuous"


def verify_revision(root, manifest, statuses):
    """Bind every compound member to the preserved, held predecessor observation."""
    import gzip

    meta = manifest.get("closing_auction_revision")
    compound = {
        code for code, status in statuses.items() if status.get("source_composition") == PROVIDER
    }
    if meta is None:
        if compound:
            raise ValueError("AUCTION_REVISION_METADATA_MISSING")
        return
    if meta.get("contract") != CONTRACT or set(meta.get("codes", [])) != compound or not compound:
        raise ValueError("AUCTION_REVISION_CENSUS_INVALID")
    prior_raw = (root / "evidence/predecessor-native-manifest.json").read_bytes()
    prior = json.loads(prior_raw)
    prior_quality_raw = (root / "evidence/predecessor-quality-status.jsonl").read_bytes()
    declared_quality = [x for x in prior["files"] if x["path"] == "quality-status.jsonl"]
    if len(declared_quality) != 1 or declared_quality[0]["sha256"] != sha(prior_quality_raw):
        raise ValueError("AUCTION_PREDECESSOR_QUALITY_CHANGED")
    proof_raw = (root / "evidence/auction-repair.json").read_bytes()
    proof = json.loads(proof_raw)
    if sha(proof_raw) != meta["evidence_sha256"] or proof["predecessor_manifest_sha256"] != sha(
        prior_raw
    ):
        raise ValueError("AUCTION_PREDECESSOR_BINDING_INVALID")
    if meta.get("predecessor_manifest_sha256") != sha(prior_raw):
        raise ValueError("AUCTION_PREDECESSOR_ANCHOR_DIFFERS")
    capture_index = json.loads((root / "evidence/auction-capture-index.json").read_bytes())
    prior_statuses = {s["stock_code"]: s for s in map(json.loads, prior_quality_raw.splitlines())}
    repairs = {x["stock_code"]: x for x in proof["repairs"]}
    if set(repairs) != compound or len(repairs) != len(proof["repairs"]):
        raise ValueError("AUCTION_REPAIR_PROOF_CENSUS_INVALID")
    for code in compound:
        raw = gzip.decompress((root / "ods/raw" / (code + ".json.gz")).read_bytes())
        value = json.loads(raw)
        if (
            value.get("provider") != PROVIDER
            or prior_statuses[code]["quality_grade"] != "R3"
            or value["base_raw_sha256"] != prior_statuses[code]["raw_sha256"]
            or sha(raw) != repairs[code]["compound_raw_sha256"]
            or value["base_raw_sha256"] != repairs[code]["original_raw_sha256"]
            or statuses[code].get("auction_source_observed") is not True
        ):
            raise ValueError("AUCTION_ORIGINAL_LINEAGE_DIFFERS")
        names = {
            "count": "tick-count-" + code + ".raw",
            "ticks": "tick-last-" + code + ".raw",
            "minute": "tencent-minute-" + code + ".json",
        }
        for kind, entry in value["evidence"].items():
            indexed = capture_index["files"][names[kind]]
            if {k: entry[k] for k in indexed} != indexed:
                raise ValueError("AUCTION_CAPTURE_INDEX_DIFFERS")
        bound = max(instant(e["completed_at"]) for e in value["evidence"].values())
        if instant(statuses[code]["source_acquired_at_or_after"]) != bound:
            raise ValueError("AUCTION_KNOWLEDGE_BOUND_DIFFERS")
