"""September successor for the reviewed literal per-ten dividend grammar.

The August producer stays frozen. This copy changes only the declared cutoff;
the typed projector and exact unit/Decimal proof are reused. New observations
use real governance knowledge and never restore correction001 or provider PIT.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_dividend import (
    _DATE,
    _PLAN,
    MAX_BLOCKS,
    MAX_CONTENT_BYTES,
    _cells,
    materialize_dividend_record,
)
from .base22_financial import _clock
from .base22_precision import decimal_profile
from .base23_ownership_reconfirmation import RAW_FIELDS
from .base23_sources import THROUGH
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-base23-retained-literal-dividend-exact-v1"
QUALIFICATION = "COMPLETE_RETAINED_LITERAL_IMPLEMENTED_BLOCKS_NOT_PROVIDER_PIT"
TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")


def latest_literal_captures(
    observations: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Select actual latest capture within one provider/security literal identity."""
    if not 0 < len(observations) <= 12000:
        raise BoundedError("SEPTEMBER_DIVIDEND_CAPTURE_CENSUS_BOUND")
    latest: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for raw, binding in observations:
        code = raw.get("stock_code")
        if (
            not isinstance(code, str) or set(raw) != RAW_FIELDS
            or raw.get("source") != "tdx.get_company_info_content"
            or raw.get("source_revision") != "quanthus-fundamentals-provider-v1"
            or raw.get("category") != "分红扩股"
            or binding.get("complete_raw_row_sha256") != digest(raw)
        ):
            raise BoundedError("SEPTEMBER_DIVIDEND_CAPTURE_BINDING_INVALID")
        clock = _clock(raw["acquired_at"])
        if code in latest:
            previous, old_binding = latest[code]
            old = _clock(previous["acquired_at"])
            if clock == old and raw["content_sha256"] != previous["content_sha256"]:
                raise BoundedError("SEPTEMBER_DIVIDEND_EQUAL_CAPTURE_BODY_CONFLICT")
            if clock < old or (clock == old and digest(binding) >= digest(old_binding)):
                continue
        latest[code] = raw, binding
    return [latest[k] for k in sorted(latest)]


def parse_september_dividend_content(
    raw: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recognize only explicit implemented per-ten plans with an ex-dividend date.

    Every dated dividend block receives a disposition. Proposals, preferred-share
    notes, unsupported plans and conflicting/missing dates are quarantined, rather
    than silently omitted or inferred from a reporting year. Provider header dates
    and source package creation never become publication/acquisition instants.
    """
    content = raw.get("content")
    if raw.get("category") != "分红扩股" or not isinstance(content, str):
        raise BoundedError("F10_DIVIDEND_SOURCE_CATEGORY_OR_CONTENT_INVALID")
    data = content.encode("utf-8")
    if len(data) > MAX_CONTENT_BYTES:
        raise BoundedError("F10_DIVIDEND_CONTENT_BOUND_EXCEEDED")
    sha = hashlib.sha256(data).hexdigest()
    if sha != str(raw.get("content_sha256")).removeprefix("sha256:"):
        raise BoundedError("F10_DIVIDEND_CONTENT_HASH_MISMATCH")
    _clock(raw.get("acquired_at"))
    blocks: list[tuple[str, list[str]]] = []
    active = False
    coordinate: str | None = None
    lines: list[str] = []
    for line in content.splitlines():
        if line.strip() == "【分红】":
            if active:
                raise BoundedError("F10_DIVIDEND_MULTIPLE_SECTIONS_UNSUPPORTED")
            active = True
            continue
        if active and line.strip().startswith("【"):
            break
        if not active:
            continue
        cells = _cells(line)
        if not cells:
            continue
        if _DATE.fullmatch(cells[0]):
            if coordinate is not None:
                blocks.append((coordinate, lines))
            coordinate, lines = cells[0], []
        if coordinate is not None:
            lines.append(cells[1] if len(cells) > 1 else "")
    if coordinate is not None:
        blocks.append((coordinate, lines))
    if len(blocks) > MAX_BLOCKS:
        raise BoundedError("F10_DIVIDEND_BLOCK_BOUND_EXCEEDED")
    records = []
    evidence = []
    for ordinal, (coordinate, lines) in enumerate(blocks):
        text = "\n".join(lines).replace(" ", "").replace("\u3000", "")
        proof: dict[str, Any] = {
            "ordinal": ordinal,
            "reporting_coordinate": coordinate,
            "block_sha256": digest(lines),
        }
        reason = None
        if "优先股" in text:
            reason = "PREFERRED_SHARE_OR_MIXED_NOTE_IDENTITY_UNQUALIFIED"
        elif "进展说明:实施" not in text and "进展说明：实施" not in text:
            reason = "IMPLEMENTATION_NOT_ESTABLISHED"
        match = _PLAN.search(text)
        if reason is None and (match is None or not any(match.groups())):
            reason = "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"
        if reason is None and len(_PLAN.findall(text)) != 1:
            reason = "MULTIPLE_PLAN_TOKENS_UNQUALIFIED"
        dates: dict[str, date | None] = {}
        for label, target in [
            ("除权除息日", "ex_date"),
            ("预案公布日", "announce_date"),
            ("股权登记日", "record_date"),
            ("红利发放日", "payment_date"),
        ]:
            tokens = re.findall(label + r"[:：](\d{4}-\d{2}-\d{2})", text)
            values: set[date] = set()
            try:
                values = {date.fromisoformat(token) for token in tokens}
            except ValueError:
                reason = reason or "INVALID_SOURCE_DATE"
            if len(values) > 1:
                reason = reason or "CONFLICTING_SOURCE_DATES"
            dates[target] = next(iter(values)) if len(values) == 1 else None
        if dates["ex_date"] is None:
            reason = reason or "EX_DIVIDEND_DATE_UNAVAILABLE"
        if dates["ex_date"] is not None and dates["ex_date"] > THROUGH:
            reason = "EXCLUDED_AFTER_BUSINESS_CUTOFF"
        if reason:
            evidence.append({**proof, "disposition": reason})
            continue
        assert match is not None
        tokens_by_field = dict(
            zip(("bonus_shares", "transfer_shares", "cash_per_share"), match.groups(), strict=True)
        )
        mapped: dict[str, Decimal | None] = {}
        classes = {}
        with localcontext() as context:
            context.prec = 64
            for field, token in tokens_by_field.items():
                value = Decimal(token) / Decimal(10) if token is not None else None
                profile = decimal_profile(value, precision=38, scale=10)
                if value is not None and not profile["fits"]:
                    reason = "CANONICAL_DECIMAL_NOT_EXACTLY_REPRESENTABLE"
                mapped[field] = value
                classes[field] = {
                    "original_token_sha256": digest(token),
                    "source_denominator": 10,
                    "classification": "ABSENT"
                    if token is None
                    else "ZERO"
                    if value == 0
                    else "EXACT",
                    "precision_profile": profile,
                }
        if reason:
            evidence.append({**proof, "disposition": reason, "numeric": classes})
            continue
        present = [f for f, value in mapped.items() if value is not None and value != 0]
        typ = (
            "mixed"
            if len(present) > 1
            else {
                "bonus_shares": "bonus",
                "transfer_shares": "transfer",
                "cash_per_share": "cash_div",
            }.get(present[0] if present else "", "cash_div")
        )
        record = {
            "stock_code": raw["stock_code"],
            "action_type": typ,
            "progress": "实施",
            **dates,
            **mapped,
            "source_row_key": digest([raw["stock_code"], coordinate, dates["ex_date"]]),
            "original_block_sha256": proof["block_sha256"],
        }
        records.append(record)
        evidence.append(
            {
                **proof,
                "disposition": "EXACT_SCOPED_OBSERVATION",
                "numeric": classes,
                "parsed_record_sha256": digest(record),
            }
        )
    return records, {
        "contract_id": CONTRACT_ID,
        "raw_row_sha256": digest(raw),
        "content_sha256": "sha256:" + sha,
        "source_acquisition_instant": _clock(raw["acquired_at"]),
        "provider_acquisition": "UNAVAILABLE",
        "publication_instant": "UNAVAILABLE_DATE_PRECISION_ONLY",
        "dividend_section_present": active,
        "dated_blocks": len(blocks),
        "blocks": evidence,
        "no_dated_blocks_is_full_no_change_evidence": False,
        "old_correction001_restoration": False,
        "consumer_admission": "NOT_GRANTED",
    }



def dividend_observation(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime,
    vocabulary: dict[str, Any], schemas: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if (
        set(raw) != RAW_FIELDS or raw.get("category") != "分红扩股"
        or raw.get("source") != "tdx.get_company_info_content"
        or raw.get("source_revision") != "quanthus-fundamentals-provider-v1"
        or raw.get("security_id") != raw.get("stock_code")
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) or not revision
        or type(raw.get("source_start")) is not int or raw["source_start"] < 0
        or type(raw.get("source_length")) is not int or raw["source_length"] < 0
        or not isinstance(raw.get("source_filename"), str) or not raw["source_filename"]
    ):
        raise BoundedError("SEPTEMBER_DIVIDEND_COMPLETE_RAW_BINDING_INVALID")
    date.fromisoformat(raw["market_date"])
    captured, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if captured > observed:
        raise BoundedError("SEPTEMBER_DIVIDEND_CAPTURE_AFTER_GOVERNANCE")
    records, evidence = parse_september_dividend_content(raw)
    output: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    holds: list[dict[str, Any]] = []
    events: dict[str, dict[str, list[dict[str, Any]]]] = {}
    conflicts: set[str] = set()
    identical_repeats = 0
    for record in records:
        if record["ex_date"] > captured.astimezone(ZoneInfo("Asia/Shanghai")).date():
            holds.append({"parsed_record_sha256": digest(record),
                          "reason": "IMPLEMENTATION_DATE_AFTER_RETAINED_CAPTURE"})
            continue
        parent, reason = resolver.resolve_for_report(
            raw["stock_code"], period_end=record["ex_date"],
        )
        if parent is None:
            holds.append({"parsed_record_sha256": digest(record), "reason": reason})
            continue
        if parent.knowledge_from > observed:
            raise BoundedError("SEPTEMBER_DIVIDEND_PARENT_AFTER_GOVERNANCE")
        projected = materialize_dividend_record(
            record, acquired_at=raw["acquired_at"], source_sha256=source_sha256,
            revision=revision, parent=parent, vocabulary=vocabulary, schemas=schemas,
            observed_at=observed,
        )
        key = projected["dwd_corporate_action"][0]["corporate_action_id"]
        if key in events:
            if events[key] == projected:
                identical_repeats += 1
            else:
                conflicts.add(key)
                holds.append({"event_key_sha256": digest(key),
                              "reason": "SAME_LITERAL_EVENT_CONFLICT_NO_SOURCE_PRIORITY"})
        else:
            events[key] = projected
    for key in sorted(events):
        if key in conflicts:
            continue
        for table, rows in events[key].items():
            output[table].extend(rows)
    evidence.update({
        "source_member_sha256": source_sha256, "source_revision": revision,
        "original_raw_acquired_at": raw["acquired_at"], "governance_observed_at": observed,
        "held_typed_records": holds, "typed_rows": {t: len(r) for t, r in output.items()},
        "identical_repeated_event_blocks": identical_repeats,
        "conflicting_event_keys": len(conflicts),
        "typed_rows_sha256": digest(output), "provider_PIT_qualified": False,
        "other_F10_sections_admitted": False, "native_history_changed": False,
        "publication_time_inferred": False, "READY": False,
    })
    return output, evidence
