"""Explicit per-ten share labels omitted by the frozen literal dividend grammar.

Only complete standalone plans with literal share units are added. Cash plans
still require yuan and gross-tax evidence; cash-free share plans do not invent
tax or zero cash. Original source bytes, acquisition and old outputs stay intact.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_dividend import _cells, materialize_dividend_record
from .base22_financial import _clock
from .base22_precision import decimal_profile
from .base23_dividend import TABLES, parse_september_dividend_content
from .base23_ownership_reconfirmation import RAW_FIELDS
from .base23_sources import THROUGH
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-base23-explicit-share-unit-dividend-additions-v1"
_NUM = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_SHARES = rf"10(?:送({_NUM})股)?(?:转(?:增)?({_NUM})股)?"
_CASH = re.compile(_SHARES + rf"派({_NUM})元\(含税\)")
_ONLY_SHARES = re.compile(_SHARES)


def parse_unit_additions(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep every earlier disposition; only resolve the exact share-label class."""
    if (
        set(raw) != RAW_FIELDS or raw.get("source") != "tdx.get_company_info_content"
        or raw.get("source_revision") != "quanthus-fundamentals-provider-v1"
        or raw.get("security_id") != raw.get("stock_code")
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
    ):
        raise BoundedError("DIVIDEND_UNIT_COMPLETE_ORIGINAL_BINDING_REQUIRED")
    _, prior = parse_september_dividend_content(raw)
    blocks: list[tuple[str, list[str]]] = []
    active = False
    coordinate: str | None = None
    lines: list[str] = []
    for line in raw["content"].splitlines():
        if line.strip() == "【分红】":
            active = True
            continue
        if active and line.strip().startswith("【"):
            break
        if not active:
            continue
        cells = _cells(line)
        if not cells:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            if coordinate is not None:
                blocks.append((coordinate, lines))
            coordinate, lines = cells[0], []
        if coordinate is not None:
            lines.append(cells[1] if len(cells) > 1 else "")
    if coordinate is not None:
        blocks.append((coordinate, lines))
    if len(blocks) != len(prior["blocks"]):
        raise BoundedError("DIVIDEND_UNIT_ORIGINAL_BLOCK_CENSUS_CHANGED")
    records = []
    evidence = []
    for old, (coordinate, lines) in zip(prior["blocks"], blocks, strict=True):
        proof = {"ordinal": old["ordinal"], "block_sha256": digest(lines),
                 "prior_disposition": old["disposition"]}
        if old["disposition"] != "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED":
            evidence.append({**proof, "disposition": "UNCHANGED_PRIOR_DISPOSITION"})
            continue
        compact = [re.sub(r"[\s\u3000]+", "", x).replace("（", "(").replace("）", ")")
                   for x in lines]
        plans = [(line, _CASH.fullmatch(line) or _ONLY_SHARES.fullmatch(line))
                 for line in compact]
        matched = [(line, match) for line, match in plans if match and any(match.groups())]
        if len(matched) != 1:
            evidence.append({**proof, "disposition": "EXPLICIT_COMPLETE_SHARE_UNIT_PLAN_REQUIRED"})
            continue
        plan, match = matched[0]
        assert match is not None
        # A second standard plan or a cash clause outside a share-only plan is a conflict.
        if any(re.search(r"(?<![\d.])10(?:送|转|派)", line) and line != plan for line in compact):
            evidence.append({**proof, "disposition": "ADDITIONAL_PLAN_CLAIM_UNQUALIFIED"})
            continue
        text = "\n".join(compact)
        if match.re is _ONLY_SHARES and any("派" in line for line in compact):
            evidence.append({**proof, "disposition": "SHARE_ONLY_WITH_CASH_CLAIM_UNQUALIFIED"})
            continue
        dates: dict[str, date | None] = {}
        reason = None
        for label, field in [("除权除息日", "ex_date"), ("预案公布日", "announce_date"),
                             ("股权登记日", "record_date"), ("红利发放日", "payment_date")]:
            tokens = re.findall(label + r"[:：](\d{4}-\d{2}-\d{2})", text)
            try:
                values = {date.fromisoformat(x) for x in tokens}
            except ValueError:
                values = set()
                reason = "INVALID_ORIGINAL_DATE"
            if len(values) > 1:
                reason = reason or "CONFLICTING_ORIGINAL_DATES"
            dates[field] = next(iter(values)) if len(values) == 1 else None
        if dates["ex_date"] is None or dates["ex_date"] > THROUGH:
            reason = reason or "EX_DATE_UNAVAILABLE_OR_AFTER_CUTOFF"
        quantities = (*match.groups(), None) if match.re is _ONLY_SHARES else match.groups()
        values_by_field: dict[str, Decimal | None] = {}
        with localcontext() as context:
            context.prec = 64
            for field, token in zip(("bonus_shares", "transfer_shares", "cash_per_share"),
                                    quantities, strict=True):
                value = None if token is None else Decimal(token) / Decimal(10)
                if value is not None and not decimal_profile(value, precision=38, scale=10)["fits"]:
                    reason = reason or "CANONICAL_DECIMAL_NOT_EXACTLY_REPRESENTABLE"
                values_by_field[field] = value
        if reason:
            evidence.append({**proof, "disposition": reason})
            continue
        present = [k for k, v in values_by_field.items() if v is not None and v != 0]
        typ = ("mixed" if len(present) > 1 else {"bonus_shares": "bonus",
               "transfer_shares": "transfer", "cash_per_share": "cash_div"}.get(
                   present[0] if present else "", "cash_div"))
        record = {"stock_code": raw["stock_code"], "action_type": typ, "progress": "实施",
                  **dates, **values_by_field,
                  "source_row_key": digest([raw["stock_code"], coordinate, dates["ex_date"]]),
                  "original_block_sha256": proof["block_sha256"]}
        records.append(record)
        evidence.append({**proof, "disposition": "EXPLICIT_SHARE_UNIT_QUALIFIED",
                         "source_denominator": 10, "share_unit": "股",
                         "cash_basis": "EXPLICIT_YUAN_GROSS" if match.re is _CASH else None,
                         "original_plan_literal_sha256": digest(plan),
                         "parsed_record_sha256": digest(record)})
    return records, {"contract_id": CONTRACT_ID, "original_raw_row_sha256": digest(raw),
                     "original_content_sha256": raw["content_sha256"], "blocks": evidence,
                     "prior_contract_id": prior["contract_id"], "source_bytes_changed": False,
                     "provider_PIT_qualified": False, "tax_for_absent_cash_inferred": False}


def materialize_unit_additions(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime,
    vocabulary: dict[str, Any], schemas: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    captured, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if (captured > observed or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
            or not revision):
        raise BoundedError("DIVIDEND_UNIT_SOURCE_IDENTITY_OR_OBSERVATION_INVALID")
    records, proof = parse_unit_additions(raw)
    events: dict[str, dict[str, list[dict[str, Any]]]] = {}
    conflicting: set[str] = set()
    holds: list[dict[str, Any]] = []
    repeated = 0
    for record in records:
        if record["ex_date"] > captured.astimezone(ZoneInfo("Asia/Shanghai")).date():
            holds.append({"record_sha256": digest(record), "reason": "AFTER_ORIGINAL_CAPTURE"})
            continue
        parent, reason = resolver.resolve_for_report(
            raw["stock_code"], period_end=record["ex_date"])
        if parent is None:
            holds.append({"record_sha256": digest(record), "reason": reason})
            continue
        if parent.knowledge_from > observed:
            raise BoundedError("DIVIDEND_UNIT_PARENT_AFTER_OBSERVATION")
        rows = materialize_dividend_record(
            record, acquired_at=raw["acquired_at"], source_sha256=source_sha256,
            revision=revision, parent=parent, vocabulary=vocabulary, schemas=schemas,
            observed_at=observed,
        )
        key = rows[TABLES[0]][0]["corporate_action_id"]
        if key in events:
            if rows == events[key]:
                repeated += 1
            else:
                conflicting.add(key)
        else:
            events[key] = rows
    output: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    for key in sorted(set(events) - conflicting):
        for table, table_rows in events[key].items():
            output[table].extend(table_rows)
    proof.update(source_member_sha256=source_sha256, source_revision=revision,
                 original_acquired_at=raw["acquired_at"], observed_at=observed,
                 typed_row_counts={k: len(v) for k, v in output.items()},
                 typed_rows_sha256=digest(output), holds=holds,
                 conflicting_event_keys=len(conflicting), identical_repeats=repeated,
                 consumer_admission="NOT_GRANTED", sealed_base_changed=False)
    return output, proof
