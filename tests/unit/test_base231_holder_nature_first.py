"""Synthetic physical source cells; no real holder values or price rows."""

from copy import deepcopy
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from quanthus.pipelines.securities_warehouse.base231_holder_nature_first import (
    parse_nature_first_matrix,
)


def fixture():
    words = []

    def add(text, x, y, width=50):
        words.append(dict(text=text, x=x, y=y, width=width))

    add("单位：股", 450, 80)
    add("持股", 60, 100, 25)
    add("5%以上的股东或前", 85, 100, 100)
    add("10", 185, 100, 15)
    add("名股东持股情况（不含通过转融通出借股份）", 200, 100, 330)
    for text, x, y, width in [
        ("股东名称", 60, 130, 60), ("股东性质", 160, 130, 60),
        ("持股比例", 230, 130, 60), ("报告期末", 320, 130, 60),
        ("持股数量", 320, 145, 60), ("报告期内", 410, 130, 60),
    ]:
        add(text, x, y, width)
    hints = []
    for rank in range(1, 11):
        y = 180 + rank * 48
        name = "合成股东" + str(rank)
        hints.append(dict(HOLDER_RANK=rank, HOLDER_NAME=name, HOLD_NUM=str(10000 + rank)))
        add(name, 60, y, 70)
        add("境内自然", 160, y - 5, 50)
        add("人", 160, y + 7, 10)
        add("1.20%", 245, y, 40)
        add("10,00" + str(rank) if rank < 10 else "10,010", 320, y, 60)
        add("999", 420, y, 35)
    add("前十名无限售条件股东持股情况", 60, 750, 400)
    return words, hints


def xml(words):
    root = Element("doc")
    page = SubElement(root, "page", width="600", height="850")
    for w in words:
        el = SubElement(page, "word", xMin=str(w["x"]), xMax=str(w["x"] + w["width"]),
                        yMin=str(w["y"]), yMax=str(w["y"] + 10))
        el.text = w["text"]
    return tostring(root, encoding="unicode")


def test_source_columns_preserve_ten_names_native_digits_percent_and_category():
    words, hints = fixture()
    before = deepcopy(hints)
    first = next(w for w in words if w["text"] == "10,001")
    first.update(text="10,00", width=42)
    words.append(dict(text="1", x=370, y=first["y"] + 8, width=7))
    rows, proof = parse_nature_first_matrix(xml(words), hints)
    assert rows[0]["quantity"].as_tuple() == Decimal("10001").as_tuple()
    assert rows[0]["raw_quantity"] == "10,001"
    assert [r["rank"] for r in rows] == list(range(1, 11))
    assert all(r["percentage"] == Decimal("1.20") for r in rows)
    assert all(r["category"] == "境内自然人" for r in rows)
    assert proof["source_priority_or_transaction_equivalence_granted"] is False
    assert hints == before


def test_only_complete_following_strategic_investor_label_ends_the_matrix():
    words, hints = fixture()
    words.extend([
        dict(text="战略投资者或一般法人", x=60, y=690, width=120),
        dict(text="因配售新股成为前10名", x=60, y=705, width=120),
        dict(text="股东的情况（参见注）", x=60, y=720, width=120),
    ])
    rows, _ = parse_nature_first_matrix(xml(words), hints)
    assert len(rows) == 10
    words[-2]["text"] = "未说明的另一业务"
    with pytest.raises(RuntimeError, match="COMPLETE_NAMES"):
        parse_nature_first_matrix(xml(words), hints)


@pytest.mark.parametrize("field", ["股东名称", "股东性质", "持股比例", "报告期末"])
def test_wrapped_headers_must_bind_to_their_own_physical_column(field):
    words, hints = fixture()
    first, second = {
        "股东名称": ("股东名", "称"), "股东性质": ("股东性", "质"),
        "持股比例": ("持股比", "例"), "报告期末": ("报告期末持", "股数量"),
    }[field]
    w = next(w for w in words if w["text"] == field)
    w["text"] = first
    suffix = dict(text=second, x=w["x"], y=w["y"] + 15, width=w["width"])
    if field == "报告期末":
        next(w for w in words if w["text"] == "持股数量")["text"] = second
        suffix = next(w for w in words if w["text"] == second)
    else:
        words.append(suffix)
    rows, _ = parse_nature_first_matrix(xml(words), hints)
    assert len(rows) == 10
    suffix["x"] += 12
    with pytest.raises(RuntimeError):
        parse_nature_first_matrix(xml(words), hints)


def test_quantity_only_quarter_header_still_needs_explicit_right_column():
    words, hints = fixture()
    words.remove(next(w for w in words if w["text"] == "报告期末"))
    next(w for w in words if w["text"] == "报告期内")["text"] = "持有有限售条件"
    rows, _ = parse_nature_first_matrix(xml(words), hints)
    assert len(rows) == 10
    next(w for w in words if w["text"] == "持有有限售条件")["text"] = "未知数据"
    with pytest.raises(RuntimeError, match="COLUMNS"):
        parse_nature_first_matrix(xml(words), hints)


def test_offline_nature_first_requires_both_pinned_physical_modules(monkeypatch):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base231_official_holder_replay import (
        extract_physical_layout,
    )

    modules = [
        "src/quanthus/pipelines/securities_warehouse/base231_holder_physical_columns.py",
        "src/quanthus/pipelines/securities_warehouse/base231_holder_nature_first.py",
    ]
    codes = {n: file_sha(Path(n)) for n in modules}

    def forbidden(*a, **kw):
        raise AssertionError("subprocess ran before code qualification")

    monkeypatch.setattr("subprocess.run", forbidden)
    for missing in modules:
        subset = {n: h for n, h in codes.items() if n != missing}
        with pytest.raises(RuntimeError, match="PHYSICAL_CONTRACT_OR_CODE_UNPINNED"):
            extract_physical_layout(
                Path("synthetic.pdf"),
                dict(physical_layout_contract="explicit-native-holder-nature-first-columns-v1"),
                subset,
            )


def test_reconstructed_source_label_includes_separate_numeric_word_only_in_left_cell():
    words, hints = fixture()
    words.extend([
        dict(text="战略投资者或一般法人", x=60, y=680, width=90),
        dict(text="因配售新股成为前", x=60, y=695, width=72),
        dict(text="10", x=134, y=695, width=10),
        dict(text="名", x=147, y=695, width=9),
        dict(text="不适用", x=180, y=700, width=30),
        dict(text="股东的情况（如有）", x=60, y=710, width=96),
    ])
    rows, _ = parse_nature_first_matrix(xml(words), hints)
    assert len(rows) == 10
    next(w for w in words if w["text"] == "10" and w["y"] == 695)["text"] = "20"
    with pytest.raises(RuntimeError, match="COMPLETE_NAMES"):
        parse_nature_first_matrix(xml(words), hints)


def test_native_project_dispatch_does_not_infer_a_new_layout_from_an_old_contract():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

    words, hints = fixture()
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    args = dict(
        parent=FinancialCompanyIdentity(
            "000400", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
        ),
        business_date=date(2025, 12, 31), announcement_id="123",
        official_sha256="sha256:" + "a" * 64, hint_sha256="sha256:" + "b" * 64,
        source_known_at=now, observed_at=now,
        original_index=dict(secCode="000400", orgId="official-org", announcementId="123"),
        physical_layout_xml=xml(words),
    )
    body = "公司代码：000400\n2025年年度报告\f"
    rows, _ = project_top_matrix(
        body, hints, **args,
        physical_layout_contract="explicit-native-holder-nature-first-columns-v1",
    )
    assert len(rows) == 10 and rows[0]["knowledge_from"] == now
    assert rows[0]["holding_quantity"] == Decimal("10001")
    for contract in [None, "unreviewed", "explicit-native-holder-physical-columns-v1"]:
        with pytest.raises(RuntimeError):
            project_top_matrix(body, hints, **args, physical_layout_contract=contract)


def test_quantity_boundary_uses_both_explicit_header_centres():
    words, hints = fixture()
    neighbor = next(w for w in words if w["text"] == "报告期内")
    neighbor.update(text="持有有限售条件", width=80)
    quantity = next(w for w in words if w["text"] == "10,001")
    quantity["width"] = 76
    rows, _ = parse_nature_first_matrix(xml(words), hints)
    assert rows[0]["quantity"] == Decimal("10001")
    quantity["width"] = 90
    with pytest.raises(RuntimeError, match="QUANTITY"):
        parse_nature_first_matrix(xml(words), hints)


def test_following_complete_label_may_cross_one_physical_page_without_answer_text():
    words, hints = fixture()
    end = words.pop()
    words.append(dict(text="战略投资者或一般", x=60, y=720, width=72))
    second = [
        dict(text="法人因配售新股成", x=60, y=100, width=72),
        dict(text="为前10名股东的情况", x=60, y=115, width=90),
        dict(text="不适用", x=160, y=110, width=30),
        dict(end, y=200),
    ]
    root = Element("doc")
    for items in [words, second]:
        page = SubElement(root, "page", width="600", height="850")
        for w in items:
            el = SubElement(page, "word", xMin=str(w["x"]),
                            xMax=str(w["x"] + w["width"]), yMin=str(w["y"]),
                            yMax=str(w["y"] + 10))
            el.text = w["text"]
    rows, _ = parse_nature_first_matrix(tostring(root, encoding="unicode"), hints)
    assert len(rows) == 10


@pytest.mark.parametrize("fault", [
    "rank", "name", "quantity", "missing_quantity", "percent_unit", "extra_percent",
    "category", "share_unit", "extra_name", "column_order", "extent", "wrong_hint",
])
def test_incomplete_or_misbound_cells_fail_closed(fault):
    words, hints = fixture()
    if fault == "rank":
        hints[-1]["HOLDER_RANK"] = 9
    elif fault == "wrong_hint":
        hints[0]["HOLD_NUM"] = "55555"
    elif fault == "extra_name":
        words.append(dict(text="未列股东", x=60, y=710, width=70))
    elif fault == "extra_percent":
        words.append(dict(text="2%", x=245, y=710, width=40))
    else:
        old, new = {
            "name": ("合成股东1", "合成错名"), "quantity": ("10,001", "10,002"),
            "missing_quantity": ("10,001", ""), "percent_unit": ("1.20%", "1.20"),
            "category": ("境内自然", "未证实"), "share_unit": ("单位：股", "单位：万元"),
            "column_order": ("报告期内", "报告期末"),
            "extent": ("前十名无限售条件股东持股情况", "后续说明"),
        }[fault]
        next(w for w in words if w["text"] == old)["text"] = new
    with pytest.raises(RuntimeError):
        parse_nature_first_matrix(xml(words), hints)
