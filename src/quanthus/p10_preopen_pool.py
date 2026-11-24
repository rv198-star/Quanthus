"""Select a <=18 evidence-only sample from existing T references, never price rows."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanthus.p10_preopen_samples import DATES, EXCLUDED, SLOTS, URL, SamplingError, boundary, sha

BASE_SHA = "8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
FALLBACK_SHA = "4131dac0b0462203391826e5fb1c259c42218746668224981ed46fec7216d56c"


def choose(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_code = {r["security_code"]: r for r in rows if r["security_code"] not in EXCLUDED}
    if len(by_code) != len([r for r in rows if r["security_code"] not in EXCLUDED]):
        raise SamplingError("REFERENCE_CODE_AMBIGUOUS")
    if not {"002517", "600000"} <= by_code.keys():
        raise SamplingError("HOLDING_IDENTITY_UNAVAILABLE")
    selected = {
        code: {**by_code[code], "sample_categories": ["P12_FALLBACK_HOLDING"]}
        for code in ["002517", "600000"]
    }
    gaps = [
        "FORMAL_HALT_NOT_QUALIFIED_BY_NONTRADABLE_FLAG",
        "EX_DIVIDEND_EVENT_NOT_IN_SELECTED_REFERENCE",
        "NEW_IPO_EVENT_NOT_IN_SELECTED_REFERENCE",
    ]
    groups: list[tuple[str, int, Callable[[dict[str, Any]], bool]]] = [
        ("SSE_MAIN", 1, lambda r: r["board"] == "SSE_main" and r["reason_code"] == "NORMAL"),
        ("SZ_MAIN", 1, lambda r: r["board"] == "SZ_main" and r["reason_code"] == "NORMAL"),
        ("CHINEXT", 2, lambda r: r["board"] in {"ChiNext", "CHINEXT"}),
        ("STAR", 2, lambda r: r["board"] == "STAR"),
        ("BSE", 2, lambda r: r["board"] == "BSE_main"),
        (
            "MAIN_ST",
            2,
            lambda r: r["board"] in {"SSE_main", "SZ_main"} and r["reason_code"] == "ST_TRADABLE",
        ),
        ("T_SOURCE_NONTRADABLE", 2, lambda r: r["reason_code"] == "SOURCE_NOT_TRADABLE"),
    ]
    for label, count, predicate in groups:
        matches = [r for code, r in sorted(by_code.items()) if predicate(r)]
        if len(matches) < count:
            gaps.append(label + "_INSUFFICIENT")
        for row in matches[:count]:
            code = row["security_code"]
            selected.setdefault(code, {**row, "sample_categories": []})["sample_categories"].append(
                label
            )
    return [selected[code] for code in sorted(selected)], gaps


def _active(row: dict[str, Any], field_time: datetime, known_at: datetime) -> bool:
    for field in ["knowledge_from", *(["effective_from"] if "effective_from" in row else [])]:
        if not isinstance(row.get(field), datetime) or row[field].tzinfo is None:
            return False
    for start, end, point in [
        ("effective_from", "effective_to", field_time),
        ("knowledge_from", "knowledge_to", known_at),
    ]:
        if start in row and row[start] is not None and row[start] > point:
            return False
        if end in row and row[end] is not None and row[end] <= point:
            return False
    return True


def prepare(plan: dict[str, Any], sample_date: str, observed_at: datetime) -> dict[str, Any]:
    """Read only identity/venue/T-state columns from pinned manifests, at most 8MiB."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    reference_day = {"2026-11-25": "2026-11-24", "2026-11-26": "2026-11-25"}.get(sample_date)
    if reference_day is None:
        raise SamplingError("DATE_OUTSIDE_FINITE_PLAN")
    if (
        plan.get("sample_dates") != sorted(DATES)
        or plan.get("slots") != list(SLOTS)
        or plan.get("endpoint") != URL
        or plan.get("max_securities") != 18
        or plan.get("max_requests_per_day") != 6
    ):
        raise SamplingError("FINITE_PLAN_MISMATCH")
    calendar = Path(plan["calendar_descriptor"])
    calendar_bytes = calendar.read_bytes()
    if sha(calendar_bytes) != plan["calendar_sha256"]:
        raise SamplingError("CALENDAR_IDENTITY_MISMATCH")
    cal = json.loads(calendar_bytes)
    if any(
        sample_date not in cal["venues"][venue]["sessions"]
        or reference_day not in cal["venues"][venue]["sessions"]
        for venue in ["SSE", "SZSE"]
    ):
        raise SamplingError("CALENDAR_SESSION_UNAVAILABLE")
    fallback = Path(plan["fallback"])
    if sha(fallback.read_bytes()) != FALLBACK_SHA:
        raise SamplingError("FALLBACK_IDENTITY_MISMATCH")
    dates_file = Path(plan["dates"])
    dates_bytes = dates_file.read_bytes()
    dates = json.loads(dates_bytes)["dates"]
    entry = dates.get(reference_day)
    if not entry or not entry.get("core"):
        raise SamplingError("T_REFERENCE_NOT_READY")
    if entry.get("market_date") != reference_day or entry.get("status") not in {
        "QUALIFIED_1D_NATIVE5M",
        "CORE_READY_5M_HELD_SOURCE_QUALITY",
    }:
        raise SamplingError("T_REFERENCE_DATE_OR_STATUS")
    base = Path(plan["base"])
    core = Path(entry["core"])
    manifests = [(base, BASE_SHA), (core, entry["core_manifest_sha256"].removeprefix("sha256:"))]
    tables: dict[str, list[dict[str, Any]]] = {}
    evidence = [
        {"uri": str(path), "sha256": sha(body), "observed_at": observed_at.isoformat()}
        for path, body in [
            (calendar, calendar_bytes),
            (dates_file, dates_bytes),
            (fallback, fallback.read_bytes()),
        ]
    ]
    total_bytes = 0
    wanted: dict[str, list[str] | None] = {
        "dwd_listing": [
            "listing_id",
            "security_id",
            "venue_id",
            "listing_code",
            "board_code",
            "knowledge_from",
            "knowledge_to",
            "effective_from",
            "effective_to",
        ],
        "dwd_security_identifier": [
            "security_id",
            "identifier_namespace",
            "identifier_value",
            "knowledge_from",
            "knowledge_to",
            "effective_from",
            "effective_to",
        ],
        "dwd_trading_venue": None,
        "dwd_tradability_state": [
            "listing_id",
            "reason_code",
            "knowledge_from",
            "knowledge_to",
            "observation_start",
            "observation_end",
        ],
    }
    for root, expected in manifests:
        body = (root / "manifest.json").read_bytes()
        if sha(body) != expected:
            raise SamplingError("REFERENCE_MANIFEST_IDENTITY")
        manifest = json.loads(body)
        if root == core and manifest.get("market_date") != reference_day:
            raise SamplingError("T_MANIFEST_DATE_MISMATCH")
        evidence.append(
            {
                "uri": str(root / "manifest.json"),
                "sha256": expected,
                "observed_at": observed_at.isoformat(),
            }
        )
        files = manifest.get("files", manifest.get("content", {}).get("files", []))
        for member in files:
            path = Path(member["path"])
            table = next((name for name in wanted if name in path.parts), None)
            if table is None or (table == "dwd_tradability_state" and root == base):
                continue
            if path.is_absolute() or ".." in path.parts:
                raise SamplingError("REFERENCE_PATH_INVALID")
            if not isinstance(member["size_bytes"], int) or member["size_bytes"] < 0:
                raise SamplingError("REFERENCE_SIZE_INVALID")
            total_bytes += member["size_bytes"]
            if total_bytes > 8 * 1024 * 1024:
                raise SamplingError("REFERENCE_READ_BUDGET")
            target = root / path
            if not target.resolve().is_relative_to(root.resolve()):
                raise SamplingError("REFERENCE_PATH_INVALID")
            if target.stat().st_size != member["size_bytes"]:
                raise SamplingError("REFERENCE_MEMBER_IDENTITY")
            payload = target.read_bytes()
            if len(payload) != member["size_bytes"] or sha(payload) != member[
                "sha256"
            ].removeprefix("sha256:"):
                raise SamplingError("REFERENCE_MEMBER_IDENTITY")
            evidence.append(
                {
                    "uri": str(root / path),
                    "sha256": sha(payload),
                    "observed_at": observed_at.isoformat(),
                }
            )
            tables.setdefault(table, []).extend(
                pq.ParquetFile(io.BytesIO(payload))
                .read(
                    columns=wanted[table],
                )
                .to_pylist()
            )
    at = boundary(reference_day, "15:00:00")
    venues: dict[str, str] = {}
    for row in tables.get("dwd_trading_venue", []):
        if _active(row, at, observed_at):
            if row["venue_id"] in venues and venues[row["venue_id"]] != row["mic_code"]:
                raise SamplingError("VENUE_IDENTITY_AMBIGUOUS")
            venues[row["venue_id"]] = row["mic_code"]
    codes = {
        (r["security_id"], r["identifier_value"])
        for r in tables["dwd_security_identifier"]
        if r["identifier_namespace"] == "CN_STOCK_CODE" and _active(r, at, observed_at)
    }
    listings: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    identity_columns = ["security_id", "venue_id", "listing_code", "board_code"]
    for row in tables["dwd_listing"]:
        if _active(row, at, observed_at):
            key = row["listing_id"]
            if key in listings and any(listings[key][col] != row[col] for col in identity_columns):
                ambiguous.add(key)
            listings[key] = row
    candidates = []
    seen = set()
    for state in tables["dwd_tradability_state"]:
        listing = listings.get(state["listing_id"])
        if not listing or state["listing_id"] in ambiguous or not _active(state, at, observed_at):
            continue
        if state["observation_start"] != boundary(reference_day, "00:00:00") or state[
            "observation_end"
        ] != boundary(reference_day, "00:00:00") + timedelta(days=1):
            raise SamplingError("T_STATE_OBSERVATION_DATE_MISMATCH")
        code = listing["listing_code"]
        mic = venues.get(listing["venue_id"])
        if (listing["security_id"], code) not in codes or mic not in {"XSHG", "XSHE", "BJSE"}:
            continue
        if code in seen:
            raise SamplingError("T_STATE_AMBIGUOUS")
        seen.add(code)
        candidates.append(
            {
                "security_code": code,
                "tencent_symbol": {"XSHG": "sh", "XSHE": "sz", "BJSE": "bj"}[mic] + code,
                "listing_id": state["listing_id"],
                "mic_code": mic,
                "board": listing["board_code"],
                "reason_code": state["reason_code"],
                "t_state_observation_start": state["observation_start"].isoformat(),
                "t_state_observation_end": state["observation_end"].isoformat(),
                "t_state_knowledge_from": state["knowledge_from"].isoformat(),
                "reference_evidence": evidence,
            }
        )
    selected, gaps = choose(candidates)
    if ambiguous:
        gaps.append("AMBIGUOUS_LISTING_IDENTITIES_EXCLUDED:" + str(len(ambiguous)))
    gaps.append("BSE_CALENDAR_UNAVAILABLE_RAW_SAMPLING_ONLY")
    return {
        "sample_date": sample_date,
        "reference_trade_date": reference_day,
        "securities": selected,
        "fallback_sha256": FALLBACK_SHA,
        "coverage_gaps": gaps,
        "reference_bytes_read": total_bytes,
        "dates_sha256": sha(dates_bytes),
        "core_manifest_sha256": manifests[1][1],
        "calendar_sha256": plan["calendar_sha256"],
        "price_rows_read": False,
    }
