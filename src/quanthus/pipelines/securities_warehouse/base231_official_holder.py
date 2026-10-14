"""Complete official holder-table rows bound to exact provider ranking hints.

Hints cannot establish transaction identity, source precedence, a missing row or
an undisclosed percentage. Native quantities/percentages come from the actual
complete official table; all original bodies stay in the bounded domain.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .financial_reporting import FinancialCompanyIdentity
from .ownership_capital import _stable_holder_key, ownership_capital_schemas

CONTRACT_ID = "p10-base231-official-complete-holder-matrix-reconciled-hints-v1"
_NATURE = "国有法人|境外法人|境内非国有法人|境内自然人|境外自然人|其他"
_WRAPPED_NATURE = (
    _NATURE + "|境内非国有法|境内非国有|境内非国|有法人|法人|国有法|"
    "境内自然|境外自然|境外法|境内法|自然人|然人|人"
)
_ANCHOR = re.compile(
    r"^(.*?)\s+(" + _WRAPPED_NATURE + r")\s+([0-9]+(?:\.[0-9]+)?)%\s+"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _validate_hints(hints: list[dict[str, Any]]) -> None:
    if (
        len(hints) != 10
        or any(
            type(r.get("HOLDER_RANK")) is not int
            or not isinstance(r.get("HOLDER_NAME"), str)
            or not r["HOLDER_NAME"]
            for r in hints
        )
        or sorted(r["HOLDER_RANK"] for r in hints) != list(range(1, 11))
        or len({_norm(str(r.get("HOLDER_NAME", ""))) for r in hints}) != 10
    ):
        raise BoundedError("OFFICIAL_HOLDER_EXACT_TEN_HINT_RANKS_REQUIRED")


def parse_top_matrix(
    body: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(body, str) or not 0 < len(body.encode()) <= 16 * 1024**2:
        raise BoundedError("OFFICIAL_HOLDER_BODY_BOUND")
    _validate_hints(hints)
    lines = body.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.search(r"前(?:10|十)名股东持股情况", _norm(line)) and "表" not in _norm(line)
    ]
    if len(starts) != 1:
        raise BoundedError("OFFICIAL_HOLDER_UNIQUE_COMPLETE_TOP_MATRIX_REQUIRED")
    start = starts[0]
    before = _norm("\n".join(lines[max(0, start - 15) : start + 6]))
    units = re.findall(r"单位[:：](万股|股|元|万元)", before)
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if any(
                t in _norm(lines[i])
                for t in ["上述股东关联关系", "前10名无限售", "前十名无限售", "上述股东涉及委托"]
            )
        ),
        len(lines),
    )
    block = lines[start + 1 : end]
    # Some official matrices put quantity before a bare percentage atom. Only
    # their explicit ordered header grants that unit; provider ratios do not.
    first_numeric = next(
        (i for i, line in enumerate(block) if re.search(_WRAPPED_NATURE, line)),
        len(block),
    )
    header = _norm("\n".join(block[:first_numeric]))
    quantity_first = bool(re.search(r"持股数量.*?比例.*?\(%\)", header))

    def column(label: str) -> int | None:
        positions = {
            sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in line[:pos])
            for line in block[:first_numeric]
            if (pos := line.find(label)) >= 0
        }
        return next(iter(positions)) if len(positions) == 1 else None

    header_columns = [column(label) for label in ["报告期内增减", "期末持股数", "比例", "股东性质"]]
    nature_last = bool(
        "(%)" in header
        and all(value is not None for value in header_columns)
        and header_columns
        == sorted(header_columns, key=lambda value: -1 if value is None else value)
        and len(set(header_columns)) == 4
    )
    last_nature_pattern = re.compile(
        r"^(.*?)\s+[+-]?[0-9][0-9,]*(?:\.[0-9]+)?\s+"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)(%?)\s+"
        r"[0-9][0-9,]*(?:\.[0-9]+)?\s+(?:无|质押|冻结|不适用|标记)\s+"
        r"[0-9][0-9,]*(?:\.[0-9]+)?\s+(" + _NATURE + r")\s*$"
    )
    anchor_pattern = (
        re.compile(
            r"^(.*?)\s+(" + _WRAPPED_NATURE + r")\s+"
            r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
            r"([0-9]+(?:\.[0-9]+)?)(%?)\s+"
        )
        if quantity_first
        else _ANCHOR
    )
    parts: list[str] = []
    anchors: list[dict[str, Any]] = []
    offset = 0
    for line in block:
        match = last_nature_pattern.match(line) if nature_last else anchor_pattern.match(line)
        if match:
            fragment = _norm(match[1].strip())
            anchors.append(
                dict(
                    start=offset,
                    end=offset + len(fragment),
                    nature=(
                        match[5]
                        if nature_last
                        else match[2]
                        if match[2] in _NATURE.split("|")
                        else None
                    ),
                    pct=match[3] if nature_last else match[4] if quantity_first else match[3],
                    qty=match[2] if nature_last else match[3] if quantity_first else match[4],
                    raw_pct=(
                        match[3] + match[4]
                        if nature_last
                        else (match[4] + match[5])
                        if quantity_first
                        else match[3] + "%"
                    ),
                )
            )
            parts.append(fragment)
            offset += len(fragment)
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        # These are this source layout's column headers/right-hand cells and
        # page furniture. Every left-column name byte must match the full chain.
        if (
            not stripped
            or indent > 8
            or stripped.isdecimal()
            or any(
                k in stripped
                for k in [
                    "股东名称",
                    "股东性质",
                    "持股比例",
                    "持股数量",
                    "条件的",
                    "股份数量",
                    "质押",
                    "冻结",
                    "股份状态",
                ]
            )
        ):
            continue
        fragment = _norm(re.split(r"\s{2,}", stripped)[0])
        parts.append(fragment)
        offset += len(fragment)
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    wanted = [_norm(r["HOLDER_NAME"]) for r in ordered]
    if "".join(parts) != "".join(wanted):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN")
    position = 0
    spans = []
    for name in wanted:
        spans.append((position, position + len(name)))
        position += len(name)
    rows = []
    ranks = []
    for anchor in anchors:
        if anchor["start"] == anchor["end"]:
            matching = [i for i, (a, b) in enumerate(spans) if a < anchor["start"] <= b]
        else:
            matching = [
                i for i, (a, b) in enumerate(spans) if anchor["start"] < b and anchor["end"] > a
            ]
        if len(matching) != 1:
            raise BoundedError("OFFICIAL_HOLDER_NUMERIC_ROW_NAME_BINDING_UNPROVEN")
        index = matching[0]
        quantity, percentage = Decimal(anchor["qty"].replace(",", "")), Decimal(anchor["pct"])
        if (
            not quantity.is_finite()
            or quantity < 0
            or not 0 <= percentage <= 100
            or quantity != Decimal(str(ordered[index]["HOLD_NUM"]))
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT")
        ranks.append(index + 1)
        rows.append(
            dict(
                rank=index + 1,
                name=ordered[index]["HOLDER_NAME"],
                category=anchor["nature"],
                quantity=quantity,
                percentage=percentage,
                raw_quantity=anchor["qty"],
                raw_percentage=anchor["raw_pct"],
            )
        )
    if ranks != list(range(1, 11)):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    return rows, dict(
        contract_id=CONTRACT_ID,
        rows=10,
        whole_block_sha256=digest("\n".join(block)),
        complete_name_chain_sha256=digest("".join(parts)),
        native_atoms_sha256=digest(rows),
        explicit_share_unit="SHARE",
        rank_authority="EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER",
        percentage_authority="ORIGINAL_OFFICIAL_PERCENT_TOKEN_NOT_PROVIDER_EXTRA_DIGITS",
        source_priority_or_transaction_equivalence_granted=False,
    )


def parse_floating_matrix(
    body: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Only the explicit single-share-class, repeated-quantity source layout.

    Its official table has no percentage column. Provider ratios cannot supply
    a missing denominator or silently turn this into a total-holder matrix.
    """
    if not isinstance(body, str) or not 0 < len(body.encode()) <= 16 * 1024**2:
        raise BoundedError("OFFICIAL_HOLDER_BODY_BOUND")
    _validate_hints(hints)
    lines = body.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.search(r"前(?:10|十)名无限售条件股东持股情况", _norm(line))
    ]
    if len(starts) != 1:
        raise BoundedError("OFFICIAL_FLOATING_UNIQUE_COMPLETE_MATRIX_REQUIRED")
    start = starts[0]
    units = re.findall(
        r"单位[:：](万股|股|元|万元)", _norm("\n".join(lines[max(0, start - 200) : start + 6]))
    )
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if any(
                t in _norm(lines[i])
                for t in [
                    "上述股东",
                    "前10名无限售条",
                    "前十名无限售条",
                    "持股5%以上股东",
                    "前10名股东参与",
                ]
            )
        ),
        len(lines),
    )
    block = lines[start + 1 : end]
    anchor_pattern = re.compile(
        r"^(.*?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s+人民币普通股\s+"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*$"
    )
    parts: list[str] = []
    anchors: list[dict[str, Any]] = []
    offset = 0
    for line in block:
        match = anchor_pattern.match(line)
        if match:
            fragment = _norm(match[1].strip())
            anchors.append(
                dict(start=offset, end=offset + len(fragment), qty=match[2], repeated=match[3])
            )
            parts.append(fragment)
            offset += len(fragment)
            continue
        stripped = line.strip()
        if (
            not stripped
            or len(line) - len(line.lstrip()) > 8
            or stripped.isdecimal()
            or any(
                k in stripped
                for k in ["股东名称", "持有无限售", "股份种类", "报告全文", "年度报告", "季度报告"]
            )
        ):
            continue
        fragment = _norm(re.split(r"\s{2,}", stripped)[0])
        parts.append(fragment)
        offset += len(fragment)
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    wanted = [_norm(r["HOLDER_NAME"]) for r in ordered]
    if "".join(parts) != "".join(wanted):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN")
    spans = []
    offset = 0
    for name in wanted:
        spans.append((offset, offset + len(name)))
        offset += len(name)
    rows = []
    for anchor in anchors:
        matching = [
            i
            for i, (a, b) in enumerate(spans)
            if (
                a < anchor["start"] <= b
                if anchor["start"] == anchor["end"]
                else anchor["start"] < b and anchor["end"] > a
            )
        ]
        if len(matching) != 1:
            raise BoundedError("OFFICIAL_HOLDER_NUMERIC_ROW_NAME_BINDING_UNPROVEN")
        index = matching[0]
        quantity = Decimal(anchor["qty"].replace(",", ""))
        if (
            quantity != Decimal(anchor["repeated"].replace(",", ""))
            or quantity != Decimal(str(ordered[index]["HOLD_NUM"]))
            or quantity < 0
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT")
        rows.append(
            dict(
                rank=index + 1,
                name=ordered[index]["HOLDER_NAME"],
                category=None,
                share_nature="人民币普通股",
                quantity=quantity,
                percentage=None,
                raw_quantity=anchor["qty"],
                raw_percentage=None,
            )
        )
    if [r["rank"] for r in rows] != list(range(1, 11)):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    return rows, dict(
        contract_id=CONTRACT_ID,
        rows=10,
        whole_block_sha256=digest("\n".join(block)),
        complete_name_chain_sha256=digest("".join(parts)),
        native_atoms_sha256=digest(rows),
        explicit_share_unit="SHARE",
        rank_authority="EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER",
        percentage_authority="NOT_DISCLOSED_IN_OFFICIAL_FLOATING_TABLE",
        source_priority_or_transaction_equivalence_granted=False,
    )


def project_top_matrix(
    body: str,
    hints: list[dict[str, Any]],
    *,
    parent: FinancialCompanyIdentity,
    business_date: date,
    announcement_id: str,
    official_sha256: str,
    hint_sha256: str,
    source_known_at: datetime,
    observed_at: datetime,
    original_index: dict[str, Any] | None = None,
    company_org_bindings: list[dict[str, Any]] | None = None,
    holding_type: str = "TOP_SHAREHOLDERS",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    clock, capture = instant(observed_at), instant(source_known_at)
    effective = datetime.combine(business_date, time(), ZoneInfo("Asia/Shanghai"))
    if (
        business_date > date(2026, 9, 30)
        or capture > clock
        or parent.knowledge_from > clock
        or not parent.effective_from <= instant(effective)
        or parent.effective_to is not None
        and instant(effective) >= parent.effective_to
        or not re.fullmatch(r"[0-9]+", announcement_id)
        or any(
            not re.fullmatch(r"sha256:[0-9a-f]{64}", sha) for sha in [official_sha256, hint_sha256]
        )
    ):
        raise BoundedError("OFFICIAL_HOLDER_DATED_PARENT_OR_SOURCE_CLOCK_UNPROVEN")
    header = _norm(body[:4000])
    reported_codes = set(re.findall(r"(?:证券|股票|A股)代码[:：]?([0-9]{6})", header))
    year = str(business_date.year)
    quarter_titles = {
        3: [year + "年第一季度报告", year + "年一季度报告"],
        6: [year + "年半年度报告"],
        9: [year + "年第三季度报告", year + "年三季度报告"],
        12: [year + "年年度报告", year + "年度报告"],
    }
    from .listed_company import _stable_id

    original_index = original_index or {}
    org_bindings = [
        r
        for r in (company_org_bindings or [])
        if r.get("company_id") == parent.company_id
        and r.get("identifier_namespace") == "CNINFO_ORG_ID"
        and r.get("identifier_authority") == "CNINFO"
        and r.get("identifier_value") == original_index.get("orgId")
        and r["effective_from"] <= instant(effective)
        and (r["effective_to"] is None or instant(effective) < r["effective_to"])
        and r["knowledge_from"] <= clock
        and (r["knowledge_to"] is None or clock < r["knowledge_to"])
    ]
    index_binds_issuer = (
        original_index.get("secCode") == parent.stock_code
        and str(original_index.get("announcementId")) == announcement_id
        and bool(original_index.get("orgId"))
        and (
            _stable_id("company", "CNINFO_ORG_ID", original_index["orgId"]) == parent.company_id
            or bool(org_bindings)
        )
    )
    if (
        (reported_codes and reported_codes != {parent.stock_code})
        or (not reported_codes and not index_binds_issuer)
        or not any(title in header for title in quarter_titles.get(business_date.month, []))
    ):
        raise BoundedError("OFFICIAL_HOLDER_REPORT_HEADER_CODE_PERIOD_UNPROVEN")
    if holding_type not in {"TOP_SHAREHOLDERS", "TOP_FLOATING_SHAREHOLDERS"}:
        raise BoundedError("OFFICIAL_HOLDER_TABLE_KIND_UNREVIEWED")
    atoms, proof = (
        parse_top_matrix(body, hints)
        if holding_type == "TOP_SHAREHOLDERS"
        else parse_floating_matrix(body, hints)
    )
    common = dict(
        record_source="cninfo.official_regular_report." + holding_type.lower(),
        source_revision=CONTRACT_ID,
        source_artifact_sha256=official_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        observation_start=instant(effective),
        observation_end=None,
        company_id=parent.company_id,
        security_id=parent.security_id,
        shareholder_id=None,
        holding_type=holding_type,
        table_kind=holding_type,
        holding_quantity_unit="SHARE",
        holding_percentage_unit="PERCENT" if holding_type == "TOP_SHAREHOLDERS" else None,
        parser_version=CONTRACT_ID,
    )
    rows = [
        dict(
            **common,
            source_record_id=announcement_id + "#" + holding_type + "&row=" + str(a["rank"]),
            share_nature=a.get("share_nature"),
            shareholder_source_name=a["name"],
            holder_match_key=_stable_holder_key(a["name"]),
            holder_rank=a["rank"],
            holder_category=a["category"],
            holding_quantity=a["quantity"],
            holding_percentage=a["percentage"],
            raw_holding_quantity=a["raw_quantity"],
            raw_holding_percentage=a["raw_percentage"],
        )
        for a in atoms
    ]
    typed = pa.Table.from_pylist(
        rows, schema=ownership_capital_schemas()["dwd_shareholding_observation"]
    )
    if typed.to_pylist() != rows:
        raise BoundedError("OFFICIAL_HOLDER_NATIVE_NUMERIC_OR_CLOCK_CHANGED")
    result = typed.to_pylist()
    return result, dict(
        proof,
        input_pdf_sha256=official_sha256,
        hint_body_sha256=hint_sha256,
        original_announcement_id=announcement_id,
        business_date=business_date,
        source_captured_at=capture,
        new_knowledge_at=clock,
        company_id=parent.company_id,
        security_id=parent.security_id,
        native_rows_sha256=digest(result),
        issuer_org_binding_sha256=digest(org_bindings),
        original_provider_acquisition=None,
        historical_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
        absent_holder_is_exit=False,
        old_values_overwritten=False,
    )
