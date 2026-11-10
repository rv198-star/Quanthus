"""Explicit nature-first holder columns from the pinned PDF's physical cells.

Every name, percentage and quantity must be physically present. Provider hints
only reconcile ten complete names, ranks and quantities; they supply no digits,
percentages, category, historical priority or transaction identity.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base231_holder_physical_columns import _Word, _words

CONTRACT_ID = "explicit-native-holder-nature-first-columns-v1"


def parse_nature_first_matrix(
    xml: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from .base231_official_holder import _NATURE, _norm, _validate_hints
    from .base231_official_holder import CONTRACT_ID as HOLDER_CONTRACT

    _validate_hints(hints)
    words = _words(xml)
    lines: list[list[_Word]] = []
    for w in words:
        if lines and w.page == lines[-1][0].page and abs(w.y0 - lines[-1][0].y0) <= 2:
            lines[-1].append(w)
        else:
            lines.append([w])
    titles = [
        line for line in lines
        if re.fullmatch(
            r"(?:持股5%以上的股东或)?前(?:10|十)名股东持股情况"
            r"(?:\(不含通过转融通出借股份\))?",
            _norm("".join(w.text for w in line)),
        )
    ]
    if len(titles) != 1:
        raise BoundedError("HOLDER_NATURE_FIRST_UNIQUE_TABLE_REQUIRED")
    start = titles[0][0]
    ends = [
        line[0] for line in lines if line[0].at > start.at
        and re.match(
            r"(?:前(?:10|十)名无限售条件股东持股情况|上述股东关联关系)",
            _norm("".join(w.text for w in line)),
        )
    ]
    ends.extend(
        line[0] for index, line in enumerate(lines) if line[0].at > start.at
        and _norm(line[0].text).startswith("战略投资者或一般法人")
        and re.match(
            r"战略投资者或一般法人因配售新股成为前(?:10|十)名股东",
            _norm("".join(
                w.text for part in lines[index:index + 6] for w in part
                if w.x0 < line[0].x1 + (line[0].x1 - line[0].x0) / len(line[0].text)
            )),
        )
    )
    # Some PDFs split both the legal-form suffix and the literal 10 into
    # separate words. Reconstruct only this exact left-hand section label,
    # using its own glyph width to exclude the adjacent answer cell.
    for w in words:
        if w.at <= start.at:
            continue
        if _norm(w.text).startswith("上述股东关联关系"):
            ends.append(w)
        if not _norm(w.text).startswith("战略投资者"):
            continue
        edge = w.x1 + (w.x1 - w.x0) / len(w.text)
        label = _norm("".join(
            part.text for part in words if w.at <= part.at <= w.at + 400 and part.x0 < edge
        ))
        if re.match(r"战略投资者或一般法人因配售新股成为前(?:10|十)名?股东", label):
            ends.append(w)
    ends.sort(key=lambda w: w.at)
    if not ends or ends[0].page - start.page > 5:
        raise BoundedError("HOLDER_NATURE_FIRST_TABLE_EXTENT_UNPROVEN")
    end = ends[0]
    units = [w for w in words if 0 < start.at - w.at <= 400 and "单位" in w.text]
    if not units or _norm(units[-1].text) != "单位:股":
        raise BoundedError("HOLDER_NATURE_FIRST_EXPLICIT_SHARE_UNIT_REQUIRED")
    percentages = [
        w for w in words if start.at < w.at < end.at
        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%", w.text)
    ]
    if len(percentages) != 10:
        raise BoundedError("HOLDER_NATURE_FIRST_TEN_EXPLICIT_PERCENTAGES_REQUIRED")
    header = [w for w in words if start.at < w.at < percentages[0].at]

    def one(text: str) -> _Word:
        found = [w for w in header if _norm(w.text) == text]
        if len(found) != 1:
            raise BoundedError("HOLDER_NATURE_FIRST_EXPLICIT_COLUMNS_REQUIRED")
        return found[0]

    def vertical(pairs: list[tuple[str, str]]) -> tuple[_Word, _Word]:
        found = [
            (a, b) for first, second in pairs for a in header for b in header
            if _norm(a.text) == first and _norm(b.text) == second
            and a.page == b.page and 0 < b.y0 - a.y0 <= 25
            and abs((a.x0 + a.x1 - b.x0 - b.x1) / 2) <= 6
        ]
        if len(found) != 1:
            raise BoundedError("HOLDER_NATURE_FIRST_WRAPPED_HEADER_UNPROVEN")
        return found[0]

    def heading(full: str, pairs: list[tuple[str, str]]) -> list[_Word]:
        found = [w for w in header if _norm(w.text) == full]
        return [one(full)] if found else list(vertical(pairs))

    name_parts = heading("股东名称", [("股东名", "称")])
    nature_parts = heading("股东性质", [("股东性", "质")])
    pct_parts = heading("持股比例", [("持股比", "例"), ("持股", "比例")])
    try:
        quantity_parts = list(vertical([
            ("报告期末", "持股数量"), ("报告期末持", "股数量"), ("报告期", "末持股"),
        ]))
    except BoundedError:
        quantity_parts = [one("持股数量")]
    if _norm(quantity_parts[-1].text) == "末持股":
        first, last = vertical([("末持股", "数量")])
        if first != quantity_parts[-1]:
            raise BoundedError("HOLDER_NATURE_FIRST_WRAPPED_HEADER_UNPROVEN")
        quantity_parts.append(last)
    pct_left, pct_right = min(w.x0 for w in pct_parts), max(w.x1 for w in pct_parts)
    qty_left, qty_right = min(w.x0 for w in quantity_parts), max(w.x1 for w in quantity_parts)
    neighbors = [w for w in header if w.x0 > qty_right and re.fullmatch(
        r"报告期(?:内(?:增减|增)?)?|持有有限(?:售(?:条(?:件)?)?)?", _norm(w.text)
    )]
    if not neighbors:
        raise BoundedError("HOLDER_NATURE_FIRST_EXPLICIT_COLUMNS_REQUIRED")
    delta = min(neighbors, key=lambda w: w.x0)
    name_right = max(w.x1 for w in name_parts)
    nature_left, nature_right = min(w.x0 for w in nature_parts), max(w.x1 for w in nature_parts)
    if not (
        name_right < nature_left < nature_right < pct_left < pct_right
        < qty_left < qty_right < delta.x0
    ):
        raise BoundedError("HOLDER_NATURE_FIRST_ORDERED_COLUMNS_REQUIRED")
    bottom = max(w.at + (w.y1 - w.y0) / 2 for w in
                 [*name_parts, *nature_parts, delta, *pct_parts, *quantity_parts])
    cells = [
        w for w in words if bottom < w.at < end.at
        and w.y0 >= 55 and w.y1 < w.height - 50
    ]
    if any(w.x0 == w.x1 for w in cells):
        raise BoundedError("HOLDER_NATURE_FIRST_ZERO_WIDTH_CELL_UNPROVEN")
    name_edge = (name_right + nature_left) / 2
    nature_edge = (nature_right + pct_left) / 2
    qty_edge = (pct_right + qty_left) / 2
    delta_edge = (qty_left + qty_right + delta.x0 + delta.x1) / 4
    # Wrapped source words may extend slightly beyond a centered header. Bind
    # their physical column by the left edge; never silently discard a long
    # unknown name or an unrecognized following label crossing that boundary.
    native_names = [w for w in cells if w.x0 < name_edge]
    chain = "".join(_norm(w.text) for w in native_names)
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    groups: list[list[_Word]] = []
    position, cursor = 0, 0
    annotations = []
    for rank, hint in enumerate(ordered, 1):
        wanted = _norm(hint["HOLDER_NAME"])
        begin = position
        if chain[position:position + 1] in {"#", "*"}:
            annotations.append(dict(rank=rank, marker=chain[position]))
            position += 1
        if not chain[position:].startswith(wanted):
            raise BoundedError("HOLDER_NATURE_FIRST_COMPLETE_NAMES_UNPROVEN")
        position += len(wanted)
        group = []
        while cursor < len(native_names) and begin < position:
            w = native_names[cursor]
            begin += len(_norm(w.text))
            group.append(w)
            cursor += 1
        if begin != position or not group:
            raise BoundedError("HOLDER_NATURE_FIRST_NAME_CELL_UNPROVEN")
        groups.append(group)
    if position != len(chain) or cursor != len(native_names):
        raise BoundedError("HOLDER_NATURE_FIRST_COMPLETE_NAMES_UNPROVEN")
    bounds = [bottom] + [
        (a[-1].at + b[0].at) / 2 for a, b in zip(groups, groups[1:], strict=False)
    ] + [end.at]
    rows = []
    for index, hint in enumerate(ordered):
        band = [w for w in cells if bounds[index] <= w.at < bounds[index + 1]]
        pct_cells = [w for w in percentages if bounds[index] <= w.at < bounds[index + 1]]
        if len(pct_cells) != 1 or not nature_edge < pct_cells[0].x0 < qty_edge:
            raise BoundedError("HOLDER_NATURE_FIRST_PERCENTAGE_ROW_UNPROVEN")
        quantity = [w for w in band if qty_edge < w.x0 and w.x1 < delta_edge]
        literal = "".join(w.text for w in quantity)
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", literal):
            raise BoundedError("HOLDER_NATURE_FIRST_COMPLETE_QUANTITY_UNPROVEN")
        value = Decimal(literal.replace(",", ""))
        percentage = Decimal(pct_cells[0].text[:-1])
        if value != Decimal(str(hint["HOLD_NUM"])) or not 0 <= percentage <= 100:
            raise BoundedError("HOLDER_NATURE_FIRST_NATIVE_VALUE_CONFLICT")
        category = _norm("".join(
            w.text for w in band if name_edge < w.x0 and w.x1 < nature_edge
        ))
        if re.fullmatch(_NATURE, category) is None:
            raise BoundedError("HOLDER_NATURE_FIRST_EXPLICIT_CATEGORY_UNPROVEN")
        rows.append(dict(
            rank=index + 1, name=hint["HOLDER_NAME"], category=category,
            quantity=value, percentage=percentage, raw_quantity=literal,
            raw_percentage=pct_cells[0].text,
        ))
    return rows, dict(
        contract_id=HOLDER_CONTRACT, layout_contract=CONTRACT_ID, rows=10,
        physical_layout_sha256=digest(xml), native_atoms_sha256=digest(rows),
        complete_name_chain_sha256=digest(chain), name_prefix_annotations=annotations,
        explicit_share_unit="SHARE", percentage_authority="EXPLICIT_NATIVE_PERCENT_SIGN",
        rank_authority="EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER",
        source_priority_or_transaction_equivalence_granted=False,
    )
