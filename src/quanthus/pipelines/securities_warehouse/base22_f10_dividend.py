"""Exact, bounded F10 dividend observations; never legacy correction restoration."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_precision import decimal_profile
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base22-f10-dividend-exact-v1"
SOURCE_FAMILY = "base22_f10/dividend_implemented"
MAX_CONTENT_BYTES = 1024 * 1024
MAX_BLOCKS = 512
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_PLAN = re.compile(
    rf"(?<![\d.])10(?:(?:送({_NUMBER}))?(?:转(?:增)?({_NUMBER}))?(?:派({_NUMBER})元)?)\(含税\)"
)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.split("｜")[1:-1]]


def parse_dividend_content(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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


def materialize_dividend_record(
    record: dict[str, Any],
    *,
    acquired_at: Any,
    source_sha256: str,
    revision: str,
    parent: Any,
    vocabulary: dict[str, Any],
    schemas: dict[str, Any],
    observed_at: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Project already exact per-share atoms, preserving real knowledge and clocks."""
    import pyarrow as pa  # type: ignore[import-untyped]

    knowledge = max(_clock(acquired_at), parent.knowledge_from)
    if observed_at is not None:
        knowledge = max(knowledge, _clock(observed_at))
    row = {**record, "built_at": knowledge}
    # The immutable old vocabulary maps per-ten shares. This input contract has
    # already converted exact per-ten tokens to per-share quantities once.
    local_vocabulary = {**vocabulary, "share_quantity_unit": "per_share"}
    with localcontext() as context:
        context.prec = 64
        out = materialize_corporate_action_rows(
            row,
            source_id=SOURCE_FAMILY,
            source_sha256=source_sha256,
            source_revision=revision,
            company_id=parent.company_id,
            security_id=parent.security_id,
            vocabulary=local_vocabulary,
            parent_knowledge_from=parent.knowledge_from,
        )
    for table, rows in out.items():
        for result in rows:
            result["announced_at"] = None
            if pa.Table.from_pylist([result], schema=schemas[table]).to_pylist()[0] != result:
                raise BoundedError("F10_DIVIDEND_TYPED_VALUE_CHANGED")
    expected = {
        vocabulary["term_types"][field]: value
        for field, value in (
            (f, record[f]) for f in ("cash_per_share", "bonus_shares", "transfer_shares")
        )
        if value is not None and value != 0
    }
    with localcontext() as context:
        context.prec = 64
        actual = {
            r["term_type"]: r["amount_per_share"]
            if r["amount_per_share"] is not None
            else r["ratio_numerator"] / r["ratio_denominator"]
            for r in out["dwd_corporate_action_term"]
        }
    if actual != expected:
        raise BoundedError("F10_DIVIDEND_CANONICAL_UNIT_OR_DECIMAL_CHANGED")
    return out
