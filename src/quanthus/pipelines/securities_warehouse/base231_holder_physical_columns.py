"""Opt-in physical cells for complete nature-last official holder matrices.

Coordinates come from ordinary pdftotext -bbox-layout over the pinned PDF. The
explicit source headers define the columns; hints only reconcile complete names,
ten ranks and already present quantities. Missing digits are never supplied.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

from .base22 import digest
from .base22_bounded import BoundedError

CONTRACT_ID = "explicit-native-holder-physical-columns-v1"


@dataclass(frozen=True)
class _Word:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    page: int
    offset: float
    height: float

    @property
    def at(self) -> float:
        return self.offset + (self.y0 + self.y1) / 2


def _reading_order(words: list[_Word]) -> list[_Word]:
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: (w.page, w.y0, w.x0)):
        if lines and word.page == lines[-1][0].page and abs(word.y0 - lines[-1][0].y0) <= 2:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [word for line in lines for word in sorted(line, key=lambda w: w.x0)]


def _words(xml: str) -> list[_Word]:
    if not isinstance(xml, str) or not 0 < len(xml.encode()) <= 16 * 1024**2:
        raise BoundedError("HOLDER_PHYSICAL_LAYOUT_SIZE_BOUND")
    if "<!ENTITY" in xml.upper():
        raise BoundedError("HOLDER_PHYSICAL_LAYOUT_ENTITY_FORBIDDEN")
    try:
        root = ElementTree.fromstring(xml)
        result = []
        offset = 0.0
        pages = [p for p in root.iter() if p.tag.rsplit("}", 1)[-1] == "page"]
        if not 0 < len(pages) <= 2000:
            raise ValueError("page count")
        for pn, page in enumerate(pages):
            width, height = float(page.attrib["width"]), float(page.attrib["height"])
            if not all(math.isfinite(v) and 0 < v <= 10000 for v in [width, height]):
                raise ValueError("page dimensions")
            for word in page.iter():
                if word.tag.rsplit("}", 1)[-1] != "word":
                    continue
                x0, x1, y0, y1 = [float(word.attrib[k]) for k in ["xMin", "xMax", "yMin", "yMax"]]
                if not (
                    all(math.isfinite(v) for v in [x0, x1, y0, y1])
                    and 0 <= x0 <= x1 <= width
                    and 0 <= y0 < y1 <= height
                    and word.text
                ):
                    raise ValueError("word geometry")
                result.append(_Word(word.text, x0, x1, y0, y1, pn, offset, height))
                if len(result) > 250000:
                    raise ValueError("word count")
            offset += height + 1
        return _reading_order(result)
    except (ValueError, KeyError, ElementTree.ParseError) as e:
        raise BoundedError("HOLDER_PHYSICAL_LAYOUT_GEOMETRY_UNPROVEN") from e


def parse_physical_top_matrix(
    xml: str, hints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from .base231_official_holder import _NATURE, _norm, _validate_hints
    from .base231_official_holder import CONTRACT_ID as HOLDER_CONTRACT

    _validate_hints(hints)
    words = _words(xml)
    titles = [
        w for w in words if re.fullmatch(r"前(?:十|10)名股东持股情况(?:（.*）|\(.*\))?", w.text)
    ]
    if len(titles) != 1:
        raise BoundedError("HOLDER_PHYSICAL_UNIQUE_TOP_MATRIX_REQUIRED")
    start = titles[0]
    ends = [
        w
        for w in words
        if w.at > start.at
        and any(t in _norm(w.text) for t in ["前十名无限售", "前10名无限售", "上述股东关联"])
    ]
    if not ends or ends[0].page - start.page > 4:
        raise BoundedError("HOLDER_PHYSICAL_COMPLETE_TABLE_EXTENT_REQUIRED")
    end = ends[0]
    units = [
        _norm(w.text)
        for w in words
        if w.page == start.page and start.y0 - 100 <= w.y0 < start.y0 and "单位" in w.text
    ]
    if units != ["单位:股"]:
        raise BoundedError("HOLDER_PHYSICAL_EXPLICIT_SHARE_UNIT_REQUIRED")
    header = [w for w in words if start.at < w.at < start.at + 100]

    def one(tokens: set[str]) -> _Word:
        found = [w for w in header if _norm(w.text) in tokens]
        if len(found) != 1:
            raise BoundedError("HOLDER_PHYSICAL_EXACT_ORDERED_COLUMNS_REQUIRED")
        return found[0]

    name = one({"股东名称"})
    delta = one({"报告期", "报告期内增减"})
    quantity = one({"期末持股", "期末持股数量"})
    percentage = one({"比例(%)", "持股比例(%)"})
    limit = one({"持有有", "持有有限售条件股份数量"})
    nature = one({"股东"})
    if not (
        name.x1
        < delta.x0
        < delta.x1
        < quantity.x0
        < quantity.x1
        < percentage.x0
        < percentage.x1
        < limit.x0
        < nature.x0
    ):
        raise BoundedError("HOLDER_PHYSICAL_EXACT_ORDERED_COLUMNS_REQUIRED")
    for first, second in [(delta, "内增减"), (quantity, "数量"), (nature, "性质")]:
        if first is delta and _norm(first.text) == "报告期内增减":
            continue
        matches = [
            w
            for w in header
            if _norm(w.text) == second
            and 0 < w.y0 - first.y0 <= 25
            and abs((w.x0 + w.x1 - first.x0 - first.x1) / 2) <= 5
        ]
        if len(matches) != 1:
            raise BoundedError("HOLDER_PHYSICAL_WRAPPED_HEADER_BINDING_REQUIRED")
    bottom = max(w.y1 for w in [name, delta, quantity, percentage, nature]) + 15
    cells = [
        w
        for w in words
        if start.at < w.at < end.at
        and (w.page != start.page or w.y0 > bottom)
        and (w.page == start.page or w.y0 >= 65)
        and w.y1 < w.height - 50
    ]
    if any(w.x0 == w.x1 for w in cells):
        raise BoundedError("HOLDER_PHYSICAL_TABLE_ZERO_WIDTH_WORD_UNPROVEN")
    footer_words = set()
    for slash in cells:
        if slash.text != "/" or slash.y0 < slash.height - 110:
            continue
        line = sorted(
            (w for w in cells if w.page == slash.page and abs(w.at - slash.at) <= 2),
            key=lambda w: w.x0,
        )
        if re.fullmatch(r"[0-9]+/[0-9]+", "".join(w.text for w in line)):
            footer_words.update(line)
    cells = [w for w in cells if w not in footer_words]
    pct_cells = [w for w in cells if quantity.x1 < w.x0 and w.x1 < limit.x0]
    if len(pct_cells) != 10 or any(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%?", w.text) is None for w in pct_cells
    ):
        raise BoundedError("HOLDER_PHYSICAL_EXACT_TEN_PERCENTAGE_ROWS_REQUIRED")
    ordered = sorted(hints, key=lambda r: r["HOLDER_RANK"])
    name_words = [w for w in cells if w.x1 < delta.x0]
    wanted = [_norm(r["HOLDER_NAME"]) for r in ordered]
    if "".join(_norm(w.text) for w in name_words) != "".join(wanted):
        raise BoundedError("HOLDER_PHYSICAL_COMPLETE_NAME_ROW_BINDING_REQUIRED")
    name_groups: list[list[_Word]] = [[] for _ in wanted]
    position, rank, end_position = 0, 0, len(wanted[0])
    for w in name_words:
        stop = position + len(_norm(w.text))
        if stop > end_position:
            raise BoundedError("HOLDER_PHYSICAL_AMBIGUOUS_NAME_CELL_REQUIRED")
        name_groups[rank].append(w)
        position = stop
        if position == end_position and rank < 9:
            rank += 1
            end_position += len(wanted[rank])
    if any(not group for group in name_groups) or any(
        left[-1].at >= right[0].at
        for left, right in zip(name_groups, name_groups[1:], strict=False)
    ):
        raise BoundedError("HOLDER_PHYSICAL_COMPLETE_NAME_ROW_BINDING_REQUIRED")
    # Physical complete name cells define row extents. Percentage centers alone
    # are insufficient when adjacent rows use slightly different font metrics.
    bounds = (
        [start.offset + bottom]
        + [
            (left[-1].at + right[0].at) / 2
            for left, right in zip(name_groups, name_groups[1:], strict=False)
        ]
        + [end.at]
    )
    rows = []
    for i, hint in enumerate(ordered):
        if not bounds[i] <= pct_cells[i].at < bounds[i + 1]:
            raise BoundedError("HOLDER_PHYSICAL_PERCENTAGE_NAME_ROW_BINDING_REQUIRED")
        band = [w for w in cells if bounds[i] <= w.at < bounds[i + 1]]
        names = [w for w in band if w.x1 < delta.x0]
        native_name = _norm("".join(w.text for w in names))
        if native_name != _norm(hint["HOLDER_NAME"]):
            raise BoundedError("HOLDER_PHYSICAL_COMPLETE_NAME_ROW_BINDING_REQUIRED")
        atoms = [w for w in band if delta.x1 < w.x0 and w.x1 < percentage.x0]
        raw_quantity = "".join(w.text for w in atoms)
        if (
            not atoms
            or re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", raw_quantity)
            is None
        ):
            raise BoundedError("HOLDER_PHYSICAL_COMPLETE_NATIVE_QUANTITY_REQUIRED")
        qty = Decimal(raw_quantity.replace(",", ""))
        raw_pct = pct_cells[i].text
        pct = Decimal(raw_pct.removesuffix("%"))
        if qty != Decimal(str(hint["HOLD_NUM"])) or not 0 <= pct <= 100:
            raise BoundedError("HOLDER_PHYSICAL_EXACT_QUANTITY_OR_PERCENTAGE_CONFLICT")
        native_nature = _norm("".join(w.text for w in band if w.x0 >= nature.x0 - 16))
        rows.append(
            dict(
                rank=i + 1,
                name=hint["HOLDER_NAME"],
                quantity=qty,
                percentage=pct,
                raw_quantity=raw_quantity,
                raw_percentage=raw_pct,
                category=native_nature if native_nature in _NATURE.split("|") else None,
            )
        )
    return rows, dict(
        contract_id=HOLDER_CONTRACT,
        layout_contract=CONTRACT_ID,
        physical_layout_sha256=digest(xml),
        native_atoms_sha256=digest(rows),
        rows=10,
        explicit_share_unit="SHARE",
        complete_name_chain_sha256=digest("".join(_norm(r["name"]) for r in rows)),
        rank_authority="EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER",
        percentage_authority="ORIGINAL_OFFICIAL_PERCENT_TOKEN_NOT_PROVIDER_EXTRA_DIGITS",
        source_priority_or_transaction_equivalence_granted=False,
    )
