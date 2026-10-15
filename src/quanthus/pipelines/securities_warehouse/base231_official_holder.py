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
_NATURE = "国有法人|境外法人|境内非国有法人|境内自然人|境外自然人|其他|未知"
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
    try:
        return _parse_top_matrix(body, hints, physical_name_cells=False)
    except BoundedError as error:
        if str(error) not in {
            "OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN",
            "OFFICIAL_HOLDER_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT",
            "OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN",
        }:
            raise
    # Preserve the already qualified source grammar. A separate explicit-column
    # family may resolve wrapped non-name cells, but never weakens full names,
    # complete ranks or original quantity reconciliation.
    try:
        return _parse_top_matrix(body, hints, physical_name_cells=True)
    except BoundedError as error:
        if str(error) not in {
            "OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN",
            "OFFICIAL_HOLDER_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT",
            "OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN",
        }:
            raise
    return _parse_top_matrix(body, hints, physical_name_cells=True, raw_name_cells=True)


def _parse_top_matrix(
    body: str,
    hints: list[dict[str, Any]],
    *,
    physical_name_cells: bool,
    raw_name_cells: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(body, str) or not 0 < len(body.encode()) <= 16 * 1024**2:
        raise BoundedError("OFFICIAL_HOLDER_BODY_BOUND")
    _validate_hints(hints)
    original_body = body
    if physical_name_cells:
        body = _join_explicit_lending_delta(body)
    wrapped_nature = _WRAPPED_NATURE + ("|国有|非国|有法" if body != original_body else "")
    if physical_name_cells:
        wrapped_nature += "|境内自|境外自"
    lines = body.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.search(r"前(?:10|十)名股东持股情况", _norm(line)) and "表" not in _norm(line)
    ]
    if len(starts) != 1:
        raise BoundedError("OFFICIAL_HOLDER_UNIQUE_COMPLETE_TOP_MATRIX_REQUIRED")
    start = starts[0]
    before = "\n".join(lines[max(0, start - 32) : start + 6])
    units = re.findall(r"单位\s*[:：]\s*(\S+)", before)
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if _norm(lines[i]).startswith("战略投资者或一般法人因")
            or (
                _norm(lines[i]).startswith("战略投资者或一般法人")
                and re.search(
                    r"战略投资者或一般法人因配售新股成为前(?:10|十)名股东",
                    _norm("".join(lines[i : i + 5])),
                )
            )
            or any(
                t in _norm(lines[i])
                for t in [
                    "上述股东关联关系",
                    "前10名无限售",
                    "前十名无限售",
                    "上述股东涉及委托",
                    "战略投资者或一般法人因配售新股成为前10名股东",
                    "战略投资者或一般法人因配售新股成为前十名股东",
                ]
            )
        ),
        len(lines),
    )
    block = lines[start + 1 : end]
    # Some official matrices put quantity before a bare percentage atom. Only
    # their explicit ordered header grants that unit; provider ratios do not.
    first_numeric = next(
        (i for i, line in enumerate(block) if re.search(wrapped_nature, line)),
        len(block),
    )
    header = _norm("\n".join(block[:first_numeric]))

    def column(label: str) -> int | None:
        positions = {
            sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in line[:pos])
            for line in block[:first_numeric]
            if (pos := line.find(label)) >= 0
        }
        if len(positions) == 1:
            return next(iter(positions))
        # The fallback family binds split header words to the same physical
        # column. Merely finding the words elsewhere does not grant a column.
        if physical_name_cells and not positions:
            pairs = {
                "报告期内增减": [("报告期", "内增减"), ("报告期内增", "减"), ("报告期内", "增减")],
                "股东性质": [("股东", "性质"), ("股东性", "质")],
                "期末持股数": [("期末持股", "数量")],
            }
            wrapped = set()
            for first, second in pairs.get(label, []):
                for n, line in enumerate(block[:first_numeric]):
                    for match in re.finditer(re.escape(first), line):
                        left = sum(
                            2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1
                            for c in line[: match.start()]
                        )
                        for following in block[n + 1 : min(n + 5, first_numeric)]:
                            for other in re.finditer(re.escape(second), following):
                                right = sum(
                                    2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1
                                    for c in following[: other.start()]
                                )
                                # A centered one-character second line need
                                # not begin at the first line's left edge.
                                centered = abs(left + len(first) - right - len(second)) <= 4
                                if abs(match.start() - other.start()) <= 2 or (
                                    abs(left - right) <= 4 and centered
                                ):
                                    wrapped.add(left)
            if len(wrapped) == 1:
                return next(iter(wrapped))
        return None

    header_columns = [column(label) for label in ["报告期内增减", "期末持股数", "比例", "股东性质"]]
    percentage_column = next(
        (value for label in ["持股比", "比例"] if (value := column(label)) is not None), None
    )
    quantity_column = next(
        (
            value
            for label in ["持股数量", "报告期末持股", "期末持股数", "报告期末持"]
            if (value := column(label)) is not None
        ),
        None,
    )
    split_percentage_first = (
        percentage_column is not None
        and quantity_column is not None
        and percentage_column < quantity_column
    )
    quantity_first = bool(
        "(%)" in header
        and percentage_column is not None
        and quantity_column is not None
        and quantity_column < percentage_column
    )
    split_numeric_pattern = re.compile(
        r"^(.*?)\s+([0-9]+(?:\.[0-9]+)?)%\s+"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
    )
    split_quantity_pattern = re.compile(
        r"^(.*?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
        r"([0-9]+(?:\.[0-9]+)?)(%?)\s+"
    )
    nature_last = bool(
        "(%)" in header
        and all(value is not None for value in header_columns)
        and header_columns
        == sorted(header_columns, key=lambda value: -1 if value is None else value)
        and len(set(header_columns)) == 4
    )
    nature_column = column("股东性质")
    name_fence = (
        header_columns[0]
        if nature_last
        else (
            nature_column
            if nature_column is not None
            and quantity_column is not None
            and nature_column < quantity_column
            else None
        )
    )
    if not physical_name_cells:
        name_fence = None

    def name_cell(line: str) -> str:
        if (lending_column or raw_name_cells) and nature_last and raw_name_fence is not None:
            if _norm(line[:raw_name_fence]) in {"(全称)", "股东名称"}:
                return line[:raw_name_fence].strip()
            remainder = _norm(line[raw_name_fence:])
            if remainder and remainder not in wrapped_nature.split("|"):
                return line.strip()
            return line[:raw_name_fence].strip()
        if name_fence is None:
            return re.split(r"\s{2,}", line.strip())[0]
        width = 0
        for i, char in enumerate(line):
            width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            if width > name_fence:
                return line[:i].strip()
        return line.strip()

    lending_column = bool(
        physical_name_cells
        and nature_last
        and (
            all(token in header for token in ("包含转", "融通借", "出股份"))
            or _vertical_lending_header(block[:first_numeric])
        )
    )
    raw_fences = {
        match.start() for line in block[:first_numeric] for match in re.finditer("报告期", line)
    }
    raw_name_fence = next(iter(raw_fences)) if len(raw_fences) == 1 else None
    lending_column = lending_column and raw_name_fence is not None
    last_nature_pattern = re.compile(
        r"^(.*?)\s+[+-]?[0-9][0-9,]*(?:\.[0-9]+)?\s+"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)(%?)\s+"
        r"[0-9][0-9,]*(?:\.[0-9]+)?\s+"
        + (r"[0-9][0-9,]*(?:\.[0-9]+)?\s+" if lending_column else "")
        + r"(?:无|质押|冻结|不适用|标记)"
        + (r"(?:\s+|$)" if physical_name_cells else r"\s+")
        + (
            r"(?:[0-9][0-9,]*(?:\.[0-9]+)?|/|未知)?"
            if physical_name_cells
            else r"[0-9][0-9,]*(?:\.[0-9]+)?"
        )
        + (
            r"(?:\s+(" + wrapped_nature + r"))?\s*$"
            if lending_column or physical_name_cells
            else r"\s+(" + _NATURE + r")\s*$"
        )
    )
    anchor_pattern = (
        re.compile(
            r"^(.*?)\s+(" + wrapped_nature + r")\s+"
            r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
            r"([0-9]+(?:\.[0-9]+)?)(%?)\s+"
        )
        if quantity_first
        else _ANCHOR
    )
    parts: list[str] = []
    anchors: list[dict[str, Any]] = []
    offset = 0
    for line_index, line in enumerate(block):
        match = last_nature_pattern.match(line) if nature_last else anchor_pattern.match(line)
        if match:
            fragment = _norm(match[1].strip())
            anchors.append(
                dict(
                    start=offset,
                    end=offset + len(fragment),
                    nature=(
                        (match[5] if match[5] in _NATURE.split("|") else None)
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
        # Some tables wrap the nature into another physical line. This family
        # requires an explicit percentage-before-quantity column order and a
        # native '%' token. Full official names, all ten rows and exact hint
        # quantities are still checked below; no category is reconstructed.
        split = (
            split_quantity_pattern.match(line)
            if quantity_first
            else split_numeric_pattern.match(line)
            if split_percentage_first
            else None
        )
        if split:
            fragment = _norm(split[1].strip())
            anchors.append(
                dict(
                    start=offset,
                    end=offset + len(fragment),
                    nature=None,
                    pct=split[3] if quantity_first else split[2],
                    qty=split[2] if quantity_first else split[3],
                    raw_pct=(split[3] + split[4]) if quantity_first else split[2] + "%",
                )
            )
            parts.append(fragment)
            offset += len(fragment)
            continue
        stripped = line.strip()
        left_cell = name_cell(line)
        indent = len(line) - len(line.lstrip())
        # These are this source layout's column headers/right-hand cells and
        # page furniture. Every left-column name byte must match the full chain.
        if (
            not stripped
            or indent > 8
            and (name_fence is None or indent >= name_fence)
            or stripped.isdecimal()
            or line_index < first_numeric
            and _norm(left_cell) in {"例", "况", "称", "质", "数量", "股东名", "股东性", "(全称)"}
            or re.fullmatch(r".+\d{4}年(?:第[一二三四]季度|年度|半年度)报告(?:全文)?", _norm(line))
            or any(
                k in left_cell
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
        fragment = _norm(left_cell)
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
    proof = dict(
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
    if body != original_body:
        proof["layout_normalization_contract"] = "adjacent-explicit-lending-delta-row-v1"
        proof["original_layout_body_sha256"] = digest(original_body)
    return rows, proof


def _vertical_lending_header(lines: list[str]) -> bool:
    columns = []
    for token in ["包含", "转融", "通借", "出股", "份的"]:
        found = [
            m.start() for line in lines if "持股情况" not in line for m in re.finditer(token, line)
        ]
        if len(found) != 1:
            return False
        columns.append(found[0])
    return len(set(columns)) == 1


def _join_explicit_lending_delta(body: str) -> str:
    """Join an explicit delta cell with its immediately following numeric row.

    No value or category is inferred. This family requires all lending-column
    labels, an actual signed delta, a complete quantity/percent/state row and
    the known nature fragments. Complete names, ranks and quantities still
    pass the ordinary full-matrix reconciliation afterwards.
    """
    lines = body.splitlines()
    starts = [i for i, s in enumerate(lines) if "前十名股东持股情况" in _norm(s)]
    if len(starts) != 1:
        return body
    start = starts[0]
    header = _norm("".join(lines[start : start + 14]))
    if "报告期内增" not in header or not (
        all(t in header for t in ["包含转", "融通借", "出股份"])
        or _vertical_lending_header(lines[start : start + 14])
    ):
        return body
    previous = re.compile(
        r"^(\S.*?)(\s{2,})([+-]?[0-9][0-9,]*(?:\.[0-9]+)?)(?:\s{2,}(国有|其他))?\s*$"
    )
    following = re.compile(
        r"^\s+[0-9][0-9,]*(?:\.[0-9]+)?\s+[0-9]+(?:\.[0-9]+)?\s+"
        r"[0-9][0-9,]*(?:\.[0-9]+)?\s+[0-9][0-9,]*(?:\.[0-9]+)?\s+"
        r"(?:无|质押|冻结|不适用|标记)\s+[0-9][0-9,]*(?:\.[0-9]+)?\s*$"
    )
    changed = False
    for i in range(start + 1, len(lines) - 1):
        if "前十名无限售" in _norm(lines[i]) or "上述股东关联关系" in _norm(lines[i]):
            break
        match = previous.fullmatch(lines[i])
        if match and following.fullmatch(lines[i + 1]):
            lines[i] = (
                match[1]
                + "  "
                + match[3]
                + "  "
                + lines[i + 1].strip()
                + ("  " + match[4] if match[4] else "")
            )
            lines[i + 1] = ""
            changed = True
    return "\n".join(lines) if changed else body


def _parse_floating_matrix(
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
    units = re.findall(r"单位\s*[:：]\s*(\S+)", "\n".join(lines[max(0, start - 200) : start + 6]))
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if _norm(lines[i]).startswith(("前10名无限售流通", "前十名无限售流通"))
            or any(
                t in _norm(lines[i])
                for t in [
                    "上述股东",
                    "前10名无限售条",
                    "前十名无限售条",
                    "持股5%以上股东",
                    "前10名股东参与",
                    "前10名无限售流通股股东之间",
                    "前十名无限售流通股股东之间",
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


def parse_floating_matrix(
    body: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        return _parse_floating_matrix(body, hints)
    except BoundedError as error:
        if str(error) not in {
            "OFFICIAL_FLOATING_UNIQUE_COMPLETE_MATRIX_REQUIRED",
            "OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN",
            "OFFICIAL_FLOATING_SPLIT_SHARE_CLASS_UNPROVEN",
        }:
            raise
    try:
        return _parse_split_floating_matrix(body, hints)
    except BoundedError as error:
        if str(error) not in {
            "OFFICIAL_FLOATING_UNIQUE_COMPLETE_MATRIX_REQUIRED",
            "OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED",
            "OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN",
            "OFFICIAL_FLOATING_SPLIT_SHARE_CLASS_UNPROVEN",
        }:
            raise
    return _parse_column_floating_matrix(body, hints)


def _parse_column_floating_matrix(
    body: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Explicit repeated-quantity columns with complete wrapped names.

    A leading # or * is retained as a source annotation only when the entire
    subsequent name chain and every native quantity reconcile exactly. Interior
    symbols, unknown text, missing ranks and additional rows are never removed.
    """
    _validate_hints(hints)
    if not 0 < len(body.encode()) <= 16 * 1024**2:
        raise BoundedError("OFFICIAL_HOLDER_BODY_BOUND")
    lines = body.split("\n")
    heading = (
        r"前(?:10|十)名无限售条件股东持股情况(?:[(]不含通过转融通出借股份(?:、高管锁定股)?[)])?"
    )
    starts = [i for i, line in enumerate(lines) if re.fullmatch(heading, _norm(line))]
    if len(starts) != 1:
        raise BoundedError("OFFICIAL_FLOATING_UNIQUE_COMPLETE_MATRIX_REQUIRED")
    start = starts[0]
    units = re.findall(r"单位\s*[:：]\s*(\S+)", "\n".join(lines[max(0, start - 200) : start + 2]))
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if _norm(lines[i]).startswith(
                (
                    "前10名无限售流通",
                    "前十名无限售流通",
                    "上述股东",
                    "前十名股东中回购专户",
                    "前10名股东中回购专户",
                )
            )
        ),
        None,
    )
    if end is None:
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    block = lines[start + 1 : end]
    anchor = re.compile(
        r"^(.*?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s+人民币普通股\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s*$"
    )
    first = next((i for i, line in enumerate(block) if anchor.fullmatch(line)), len(block))
    header = _norm("".join(block[:first]))
    split_quantity_columns = [
        match.start() for line in block[:first] for match in re.finditer("持有无限售条件", line)
    ]
    split_quantity_suffixes = [
        match.start() for line in block[:first] for match in re.finditer("流通股的数量", line)
    ]
    split_quantity_header = (
        len(split_quantity_columns) == len(split_quantity_suffixes) == 1
        and abs(split_quantity_columns[0] - split_quantity_suffixes[0]) <= 2
    )
    if not all(t in header for t in ["股东名称", "股份种类", "数量"]) or not (
        split_quantity_header
        or any(
            label in header
            for label in ["报告期末持有无限售条件股份数量", "持有无限售条件流通股的数量"]
        )
    ):
        raise BoundedError("OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED")
    headers = [
        i for i, line in enumerate(block[:first]) if re.search(r"(?:股份)?种类\s+数量", line)
    ]
    if len(headers) != 1:
        raise BoundedError("OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED")
    data_start = headers[0] + 1
    parts = []
    atoms: list[dict[str, Any]] = []
    offset = 0
    for i, line in enumerate(block[data_start:], data_start):
        match = anchor.fullmatch(line)
        if match:
            fragment = _norm(match[1])
            atoms.append(
                dict(start=offset, end=offset + len(fragment), qty=match[2], repeated=match[3])
            )
        else:
            fragment = _norm(line)
            if not fragment:
                continue
            if re.fullmatch(
                r".{1,80}[0-9]{4}年(?:半年度|年度|第[一二三四]季度)报告(?:全文)?", fragment
            ):
                continue
            if re.fullmatch(r"[0-9]{1,4}(?:/[0-9]{1,4})?", fragment) and any(
                "报告" in nearby for nearby in block[max(0, i - 3) : i + 4]
            ):
                continue
        parts.append(fragment)
        offset += len(fragment)
    chain = "".join(parts)
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    annotations = []
    spans = []
    pos = 0
    for i, hint in enumerate(ordered):
        name = _norm(hint["HOLDER_NAME"])
        begin = pos
        if chain[pos : pos + 1] in {"#", "*"} and chain[pos + 1 :].startswith(name):
            annotations.append(dict(rank=i + 1, marker=chain[pos]))
            pos += 1
        if not chain[pos:].startswith(name):
            raise BoundedError("OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN")
        pos += len(name)
        spans.append((begin, pos))
    if pos != len(chain) or len(atoms) != 10:
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    rows = []
    for atom in atoms:
        indices = [
            i
            for i, (a, b) in enumerate(spans)
            if (
                a < atom["start"] <= b
                if atom["start"] == atom["end"]
                else atom["start"] < b and atom["end"] > a
            )
        ]
        if len(indices) != 1:
            raise BoundedError("OFFICIAL_HOLDER_NUMERIC_ROW_NAME_BINDING_UNPROVEN")
        n = indices[0]
        quantity = Decimal(atom["qty"].replace(",", ""))
        if (
            quantity < 0
            or quantity != Decimal(atom["repeated"].replace(",", ""))
            or quantity != Decimal(str(ordered[n]["HOLD_NUM"]))
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT")
        rows.append(
            dict(
                rank=n + 1,
                name=ordered[n]["HOLDER_NAME"],
                category=None,
                share_nature="人民币普通股",
                quantity=quantity,
                percentage=None,
                raw_quantity=atom["qty"],
                raw_percentage=None,
            )
        )
    if [r["rank"] for r in rows] != list(range(1, 11)):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    return rows, dict(
        contract_id=CONTRACT_ID,
        layout_contract="explicit-column-wrapped-floating-matrix-v1",
        rows=10,
        whole_block_sha256=digest("\n".join(block)),
        complete_name_chain_sha256=digest(chain),
        native_atoms_sha256=digest(rows),
        name_prefix_annotations=annotations,
        explicit_share_unit="SHARE",
        rank_authority="EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER",
        percentage_authority="NOT_DISCLOSED_IN_OFFICIAL_FLOATING_TABLE",
        source_priority_or_transaction_equivalence_granted=False,
    )


def _parse_split_floating_matrix(
    body: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Narrow physical-column family with a split, repeated share class."""
    _validate_hints(hints)
    if not 0 < len(body.encode()) <= 16 * 1024**2:
        raise BoundedError("OFFICIAL_HOLDER_BODY_BOUND")
    lines = body.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(
            r"前(?:10|十)名无限售条件股东持股情况(?:[（(]不含通过转融通出借股份[）)])?", _norm(line)
        )
    ]
    if len(starts) != 1:
        raise BoundedError("OFFICIAL_FLOATING_UNIQUE_COMPLETE_MATRIX_REQUIRED")
    start = starts[0]
    units = re.findall(r"单位\s*[:：]\s*(\S+)", "\n".join(lines[max(0, start - 200) : start + 6]))
    if not units or units[-1] != "股":
        raise BoundedError("OFFICIAL_HOLDER_EXPLICIT_SHARE_UNIT_REQUIRED")
    ends = [
        i
        for i in range(start + 1, len(lines))
        if _norm(lines[i]).startswith(
            ("上述股东", "前十名股东中回购专户情况说明", "前10名股东中回购专户情况说明")
        )
    ]
    if not ends:
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ROW_EXTENT_UNPROVEN")
    block = lines[start + 1 : ends[0]]
    headers = [
        (i, m.start()) for i, line in enumerate(block) for m in re.finditer(r"种类\s+数量", line)
    ]
    qtycols = {m.start() for line in block[:8] for m in re.finditer("持有无限售条件", line)}
    if (
        len(headers) != 1
        or len(qtycols) != 1
        or "股份种类及数量" not in _norm("\n".join(block[: headers[0][0] + 1]))
    ):
        raise BoundedError("OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED")
    h, kindcol = headers[0]
    qtycol = next(iter(qtycols))
    if not 0 < qtycol < kindcol or not any(
        abs(match.start() - qtycol) <= 1
        for line in block[: h + 1]
        for match in re.finditer("流通股的数量", line)
    ):
        raise BoundedError("OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED")
    parts = []
    anchors: list[dict[str, Any]] = []
    classes = []
    offset = 0
    for i, line in enumerate(block[h + 1 :], h + 1):
        match = re.fullmatch(
            r"(.*?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s*", line
        )
        if match:
            if match.start(2) < qtycol or match.start(3) < kindcol:
                raise BoundedError("OFFICIAL_FLOATING_EXPLICIT_PHYSICAL_COLUMNS_REQUIRED")
            left = match[1]
            fragment = _norm(left)
            anchors.append(
                dict(
                    line=i,
                    start=offset,
                    end=offset + len(fragment),
                    qty=match[2],
                    repeated=match[3],
                )
            )
        else:
            left = line[:qtycol]
            right = line[qtycol:].strip()
            if right:
                if right not in {"人民币普", "通股"} or abs(line.index(right) - kindcol) > 1:
                    raise BoundedError("OFFICIAL_FLOATING_SPLIT_SHARE_CLASS_UNPROVEN")
                classes.append((i, right))
            fragment = _norm(left)
        parts.append(fragment)
        offset += len(fragment)
    if (
        len(anchors) != 10
        or len(classes) != 20
        or any(
            classes[2 * i] != (a["line"] - 1, "人民币普")
            or classes[2 * i + 1] != (a["line"] + 1, "通股")
            for i, a in enumerate(anchors)
        )
    ):
        raise BoundedError("OFFICIAL_FLOATING_SPLIT_SHARE_CLASS_UNPROVEN")
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    names = [_norm(r["HOLDER_NAME"]) for r in ordered]
    if "".join(parts) != "".join(names):
        raise BoundedError("OFFICIAL_HOLDER_COMPLETE_NAME_CHAIN_UNPROVEN")
    spans = []
    offset = 0
    for name in names:
        spans.append((offset, offset + len(name)))
        offset += len(name)
    rows = []
    for anchor in anchors:
        indices = [
            i
            for i, (a, b) in enumerate(spans)
            if (
                a < anchor["start"] <= b
                if anchor["start"] == anchor["end"]
                else anchor["start"] < b and anchor["end"] > a
            )
        ]
        if len(indices) != 1:
            raise BoundedError("OFFICIAL_HOLDER_NUMERIC_ROW_NAME_BINDING_UNPROVEN")
        index = indices[0]
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
        layout_contract="physical-split-rmb-share-class-paired-quantities-v1",
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
    cover_codes = set(re.findall(r"公司代码[:：]([0-9]{6})", _norm(body.split("\f", 1)[0][:4000])))
    cover_binds_issuer = (
        cover_codes == {parent.stock_code}
        and original_index.get("secCode") == parent.stock_code
        and str(original_index.get("announcementId")) == announcement_id
        and bool(original_index.get("orgId"))
    )
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
        (cover_codes and not cover_binds_issuer)
        or (not cover_codes and reported_codes and reported_codes != {parent.stock_code})
        or (not cover_codes and not reported_codes and not index_binds_issuer)
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
    if cover_binds_issuer and (
        bool(reported_codes and reported_codes != {parent.stock_code})
        or (not reported_codes and not index_binds_issuer)
    ):
        proof["report_issuer_binding"] = "FIRST_PAGE_COMPANY_CODE_AND_EXACT_OFFICIAL_INDEX"
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
