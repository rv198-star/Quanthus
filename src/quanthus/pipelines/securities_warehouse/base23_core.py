"""Bounded retained September core observations, never a complete Base 2.3.

Keep the frozen August adapters untouched. One local calendar/listing dependency
serves every day. Conflicting revisions are held, not ordered by package time.
Actual acquisition strings stay in physical local raw; provider clocks remain
unknown. Known calendar rows cannot stand in for missing month-end observations.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_calendar import EXCHANGE_MIC
from .base22_daily_measures import _typed_proof
from .base22_outputs import decimal_observation, instant
from .base22_sources import SourceAssessmentError
from .base22_status import MAX_DAY_ROWS, resolve_status_listing
from .base23_sources import START, THROUGH
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base23-retained-core-observations-v2"
CAPTURE_FIELDS = {"acquired_at", "built_at"}
BAR = "daily_ohlcv_none_delta"
STATUS = "daily_status_observation_delta"
TABLES = {BAR: "dwd_security_bar", STATUS: "dwd_tradability_state"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _sha(value: str) -> None:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise BoundedError("SEPTEMBER_EXACT_SOURCE_IDENTITY_REQUIRED")


def _day(value: date) -> None:
    if type(value) is not date or not START <= value <= THROUGH:
        raise BoundedError("SEPTEMBER_BUSINESS_SCOPE_REQUIRED")


def revision_union(
    sources: list[tuple[str, str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Exact same observations may share output; contested atoms get no priority.

    Acquisition and archived build clocks are capture metadata. Every original
    atom stays in local raw and both clocks stay in the proof. Identical
    business observations select the earliest actual
    acquisition, never created_at/promoted/accepted-child inference. A differing
    atom holds that whole code/day in this family. Unknown acquisition fails.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sha, revision, rows in sources:
        _sha(sha)
        if not revision or not 0 < len(rows) <= MAX_DAY_ROWS:
            raise BoundedError("SEPTEMBER_RAW_DAY_BOUND_OR_REVISION_INVALID")
        seen = set()
        for raw in rows:
            key = raw["stock_code"], raw["date"]
            if key in seen:
                raise BoundedError("SEPTEMBER_RAW_DUPLICATE_CODE_DAY")
            seen.add(key)
            acquired = instant(raw.get("acquired_at"))
            grouped.setdefault(key, []).append(
                {"raw": raw, "sha256": sha, "revision": revision, "acquired": acquired}
            )
    selected, held = [], []
    overlap = 0
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda v: (v["acquired"], v["sha256"], v["revision"]))
        observations = [
            {
                "raw_row_sha256": digest(v["raw"]),
                "member_sha256": v["sha256"],
                "revision": v["revision"],
                "acquired_at": v["raw"]["acquired_at"],
                "archived_built_at": v["raw"].get("built_at"),
            }
            for v in values
        ]
        if (
            len(
                {
                    digest({k: v for k, v in x["raw"].items() if k not in CAPTURE_FIELDS})
                    for x in values
                }
            )
            != 1
        ):
            held.append(
                {
                    "stock_code": key[0],
                    "market_date": key[1],
                    "reason": "CONFLICTING_REVISIONS_NO_AUTHORITY_PRIORITY",
                    "observations": observations,
                    "disposition": "NO_OUTPUT",
                }
            )
        else:
            first = values[0]
            selected.append(
                {
                    "raw": first["raw"],
                    "sha256": first["sha256"],
                    "revision": first["revision"],
                    "observations": observations,
                }
            )
            overlap += len(values) - 1
    proof: dict[str, Any] = {
        "selected_keys": len(selected),
        "held_conflicting_keys": len(held),
        "identical_revision_overlap": overlap,
        "source_priority_inferred": False,
    }
    proof["identity"] = digest(proof)
    return selected, held, proof


def calendar_union(
    sources: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Union observed September keys only; complete month requires exactly 90."""
    selected: dict[tuple[str, date, str], tuple[str, dict[str, Any]]] = {}
    overlap = 0
    for sha, rows in sources:
        _sha(sha)
        seen = set()
        for raw in rows:
            day = raw["trade_date"]
            _day(day)
            key = raw["exchange"], day, raw["session"]
            if (
                key in seen
                or raw["exchange"] not in EXCHANGE_MIC
                or raw["session"] != "day"
                or type(raw["is_open"]) is not int
                or raw["is_open"] not in (0, 1)
                or raw["source"] != "baostock.query_trade_dates"
            ):
                raise BoundedError("SEPTEMBER_CALENDAR_RAW_KEY_FLAG_SOURCE_INVALID")
            seen.add(key)
            clock = instant(raw["built_at"])
            if key in selected:
                old_sha, old = selected[key]
                if old["is_open"] != raw["is_open"]:
                    raise BoundedError("SEPTEMBER_CALENDAR_CONFLICTING_FLAGS")
                overlap += 1
                if (clock, sha) >= (instant(old["built_at"]), old_sha):
                    continue
            selected[key] = sha, raw
    if not selected:
        raise BoundedError("SEPTEMBER_CALENDAR_NO_OBSERVED_KEYS")
    keys = sorted(selected)
    rows = [selected[k][1] for k in keys]
    proof = {
        "contract_id": CONTRACT_ID,
        "known_keys": len(rows),
        "missing_month_keys": 90 - len(rows),
        "same_flag_overlap": overlap,
        "selected_rows_sha256": digest(rows),
        "origins": [
            {
                "exchange": k[0],
                "trade_date": k[1],
                "session": k[2],
                "member_sha256": selected[k][0],
                "raw_row_sha256": digest(selected[k][1]),
            }
            for k in keys
        ],
        "provider_acquisition": None,
    }
    proof["identity"] = digest(proof)
    return rows, proof


def project_calendar(
    rows: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source_sha256: str,
    revision: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _sha(source_sha256)
    clock = instant(observed_at)
    by_mic = {v["mic_code"]: v for v in venues}
    if len(by_mic) != len(venues) or set(by_mic) != set(EXCHANGE_MIC.values()) or not revision:
        raise BoundedError("SEPTEMBER_CALENDAR_EXACT_THREE_VENUES_REQUIRED")
    out = []
    seen = set()
    for raw in rows:
        _day(raw["trade_date"])
        key = raw["exchange"], raw["trade_date"], raw["session"]
        if key in seen or raw["exchange"] not in EXCHANGE_MIC or raw["session"] != "day":
            raise BoundedError("SEPTEMBER_CALENDAR_KEY_INVALID")
        seen.add(key)
        if (
            type(raw["is_open"]) is not int
            or raw["is_open"] not in (0, 1)
            or raw["source"] != "baostock.query_trade_dates"
        ):
            raise BoundedError("SEPTEMBER_CALENDAR_FLAG_SOURCE_INVALID")
        venue = by_mic[EXCHANGE_MIC[raw["exchange"]]]
        if (
            instant(raw["built_at"]) >= clock
            or venue["knowledge_from"] > clock
            or venue["knowledge_to"] is not None
            and clock >= venue["knowledge_to"]
        ):
            raise BoundedError("SEPTEMBER_CALENDAR_PARENT_OR_RAW_CLOCK_INVALID")
        start = datetime.combine(raw["trade_date"], time(), SHANGHAI)
        out.append(
            {
                "record_source": "p10_shared_a_share_calendar",
                "source_record_id": (
                    f"exchange={raw['exchange']}&trade_date={raw['trade_date']}&session=day"
                ),
                "source_revision": revision,
                "source_artifact_sha256": source_sha256,
                "knowledge_from": clock,
                "knowledge_to": None,
                "observation_start": start,
                "observation_end": start + timedelta(days=1),
                "calendar_session_id": _stable_id(
                    "calendar_session", venue["venue_id"], str(raw["trade_date"]), "day"
                ),
                "venue_id": venue["venue_id"],
                "trade_date": raw["trade_date"],
                "session_type": "day",
                "is_open": bool(raw["is_open"]),
                "open_at": None,
                "close_at": None,
                "calendar_reason": None if raw["is_open"] else "SCHEDULED_CLOSED",
            }
        )
    if not out or len(out) > 90:
        raise BoundedError("SEPTEMBER_CALENDAR_NONEMPTY_BOUND_REQUIRED")
    out.sort(key=lambda r: (r["venue_id"], r["trade_date"]))
    proof = {
        "rows": len(out),
        "full_month_coverage": len(out) == 90,
        "provider_acquisition": None,
        "session_hours_inferred": False,
        "independent_exchange_original_responses": "NOT_ASSERTED",
        "source_evidence_model": "shared_a_share_calendar_mirrored_to_exchanges",
        "historical_PIT_admission": False,
        "output_rows_sha256": digest(out),
    }
    return out, proof


def project_day(
    raw_by_family: dict[str, list[dict[str, Any]]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    observed_at: datetime,
    sources: dict[str, tuple[str, str]],
    input_digest: str,
    schemas: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One registered day, exact typed atoms and independent family dispositions."""
    import pyarrow as pa  # type: ignore[import-untyped]

    _day(day)
    _sha(input_digest)
    if set(raw_by_family) != set(TABLES) or set(sources) != set(TABLES):
        raise BoundedError("SEPTEMBER_EXACT_CORE_FAMILIES_REQUIRED")
    if len(listings) > MAX_DAY_ROWS:
        raise BoundedError("SEPTEMBER_PARENT_BOUND_EXCEEDED")
    by_code: dict[str, list[dict[str, Any]]] = {}
    for listing in listings:
        by_code.setdefault(listing["listing_code"], []).append(listing)
    out: dict[str, list[dict[str, Any]]] = {v: [] for v in TABLES.values()}
    proofs, held = [], []
    clock = instant(observed_at)
    start = instant(datetime.combine(day, time(), SHANGHAI))
    for family, rows in raw_by_family.items():
        sha, revision = sources[family]
        _sha(sha)
        if not revision or len(rows) > MAX_DAY_ROWS:
            raise BoundedError("SEPTEMBER_DAY_BOUND_OR_REVISION_INVALID")
        seen = set()
        for raw in rows:
            code = raw.get("stock_code")
            if not isinstance(code, str) or code in seen:
                raise BoundedError("SEPTEMBER_RAW_DUPLICATE_OR_UNKNOWN_CODE_DAY")
            seen.add(code)
            try:
                if raw.get("date") != str(day) or raw.get("market_date") != str(day):
                    raise BoundedError("SEPTEMBER_RAW_DATE_DISAGREES")
                acquired = instant(raw.get("acquired_at"))
                fractional = re.search(r"[ T]\d\d:\d\d:\d\d[.,](\d+)", str(raw.get("acquired_at")))
                if fractional and any(c != "0" for c in fractional[1][6:]):
                    raise BoundedError("SEPTEMBER_RAW_CLOCK_PRECISION_UNSUPPORTED")
                if acquired > clock:
                    raise BoundedError("SEPTEMBER_RAW_ACQUISITION_AFTER_OBSERVATION")
                listing = resolve_status_listing(
                    raw, by_code.get(code, []), day=day, observed_at=clock
                )
                matches = [
                    c
                    for c in calendars
                    if c["trade_date"] == day
                    and c["venue_id"] == listing["venue_id"]
                    and c["session_type"] == "day"
                ]
                if len(matches) != 1:
                    raise BoundedError("SEPTEMBER_EXACT_CALENDAR_DATE_VENUE_NOT_UNIQUE")
                calendar = matches[0]
                if (
                    calendar["knowledge_from"] > clock
                    or calendar["knowledge_to"] is not None
                    and clock >= calendar["knowledge_to"]
                ):
                    raise BoundedError("SEPTEMBER_CALENDAR_KNOWLEDGE_UNAVAILABLE")
                common = {
                    "record_source": "base23_september/" + family,
                    "source_record_id": f"stock_code={code}&date={day}",
                    "source_revision": revision,
                    "source_artifact_sha256": sha,
                    "knowledge_from": clock,
                    "knowledge_to": None,
                    "observation_start": start,
                    "observation_end": start + timedelta(days=1),
                    "listing_id": listing["listing_id"],
                    "calendar_session_id": calendar["calendar_session_id"],
                }
                if family == BAR:
                    if (
                        raw.get("adj_mode") != "none"
                        or raw.get("adj_version") != "baostock.adjustflag=3.v1"
                        or raw.get("source") != "baostock.query_history_k_data_plus.adjustflag=3"
                    ):
                        raise BoundedError("SEPTEMBER_EXACT_UNADJUSTED_SOURCE_UNKNOWN")
                    row = {
                        **common,
                        "interval_size": "1d",
                        "price_basis": "UNADJUSTED",
                        "currency": listing["listing_currency"],
                        "trade_count": None,
                    }
                    pairs = []
                    for original, target in [
                        (f, f) for f in ["open", "high", "low", "close", "volume"]
                    ] + [("amount", "turnover")]:
                        row[target] = decimal_observation(
                            raw.get(original), nullable=original in {"volume", "amount"}
                        )
                        pairs.append((raw.get(original), row[target]))
                    typed, proof = _typed_proof(
                        [row], schemas[TABLES[family]], pairs, acquired, clock
                    )
                else:
                    if (
                        raw.get("tradestatus") not in {"0", "1"}
                        or raw.get("isST") not in {"0", "1"}
                        or raw.get("source") != "baostock.query_history_k_data_plus"
                        or type(calendar["is_open"]) is not bool
                    ):
                        raise BoundedError("SEPTEMBER_STATUS_SOURCE_FLAGS_UNKNOWN")
                    tradable = raw["tradestatus"] == "1"
                    if tradable and not calendar["is_open"]:
                        raise BoundedError("SEPTEMBER_STATUS_CALENDAR_CLOSED_CONFLICT")
                    row = {
                        **common,
                        "is_tradable": tradable,
                        "can_buy": tradable,
                        "can_sell": tradable,
                        "reason_code": "SOURCE_NOT_TRADABLE"
                        if not tradable
                        else "ST_TRADABLE"
                        if raw["isST"] == "1"
                        else "NORMAL",
                        "upper_price": None,
                        "lower_price": None,
                        "input_revision_digest": input_digest,
                    }
                    typed = pa.Table.from_pylist([row], schema=schemas[TABLES[family]]).to_pylist()
                    if typed != [row]:
                        raise BoundedError("SEPTEMBER_TYPED_STATUS_ATOM_CHANGED")
                    proof = {"provider_acquisition": None, "unknown_price_limits_preserved": True}
            except (BoundedError, SourceAssessmentError) as exc:
                held.append(
                    {
                        "family": family,
                        "stock_code": code,
                        "market_date": str(day),
                        "raw_row_sha256": digest(raw),
                        "reason": str(exc),
                        "disposition": "NO_OUTPUT",
                    }
                )
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                held.append(
                    {
                        "family": family,
                        "stock_code": code,
                        "market_date": str(day),
                        "raw_row_sha256": digest(raw),
                        "reason": "SEPTEMBER_SCHEMA_PRECISION_OR_TYPE_UNSUPPORTED",
                        "disposition": "NO_OUTPUT",
                    }
                )
            else:
                out[TABLES[family]].extend(typed)
                proofs.append(
                    {
                        **proof,
                        "family": family,
                        "raw_row_sha256": digest(raw),
                        "typed_row_sha256": digest(typed[0]),
                        "acquired_at": raw["acquired_at"],
                        "provider_acquisition": None,
                        "observed_at": clock.isoformat(),
                    }
                )
    return out, proofs, held


def project_revisions(
    sources: dict[str, list[tuple[str, str, list[dict[str, Any]]]]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    observed_at: datetime,
    input_digest: str,
    schemas: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Share one dependency; globally deduplicate each source family before writes."""
    _day(day)
    if set(sources) != set(TABLES) or any(not v for v in sources.values()):
        raise BoundedError("SEPTEMBER_EXACT_NONEMPTY_SOURCE_FAMILIES_REQUIRED")
    outputs: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES.values()}
    details: dict[str, Any] = {}
    for family, observations in sources.items():
        selected, contested, union_proof = revision_union(observations)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in selected:
            groups.setdefault((item["sha256"], item["revision"]), []).append(item["raw"])
        proofs, held = [], []
        for (sha, revision), raw in sorted(groups.items()):
            source_ids = {key: (value[0][0], value[0][1]) for key, value in sources.items()}
            source_ids[family] = sha, revision
            out, p, h = project_day(
                {key: raw if key == family else [] for key in TABLES},
                day=day,
                listings=listings,
                calendars=calendars,
                observed_at=observed_at,
                sources=source_ids,
                input_digest=input_digest,
                schemas=schemas,
            )
            outputs[TABLES[family]].extend(out[TABLES[family]])
            proofs.extend(p)
            held.extend(h)
        details[family] = {
            "revision_union": union_proof,
            "contested": contested,
            "held": held,
            "output_proofs": proofs,
            "retained_capture_observations": [
                {k: v for k, v in item.items() if k != "raw"} for item in selected
            ],
        }
    return outputs, details
