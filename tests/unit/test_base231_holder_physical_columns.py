"""Synthetic physical-cell tests; no archived holder or price rows."""

from copy import deepcopy
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from quanthus.pipelines.securities_warehouse.base231_holder_physical_columns import (
    parse_physical_top_matrix,
)


def fixture():
    root = Element("doc")
    page = SubElement(root, "page", width="600", height="850")
    words = []

    def word(text, x, y, width=30):
        words.append(dict(text=text, x=x, y=y, width=width))

    word("单位：股", 490, 80)
    word("前十名股东持股情况（不含通过转融通出借股份）", 90, 100, 450)
    for text, x, y, width in [
        ("股东名称", 110, 130, 60),
        ("报告期", 200, 130, 35),
        ("内增减", 200, 145, 35),
        ("期末持股", 250, 130, 45),
        ("数量", 260, 145, 20),
        ("比例(%)", 315, 135, 35),
        ("持有有", 360, 120, 30),
        ("股东", 520, 130, 20),
        ("性质", 520, 145, 20),
    ]:
        word(text, x, y, width)
    hints = []
    for n in range(1, 11):
        y = 180 + 45 * n
        name = "合成完整股东" + str(n)
        hints.append(dict(HOLDER_RANK=n, HOLDER_NAME=name, HOLD_NUM=str(10000 + n)))
        word(name, 90, y, 95)
        # A digit in the explicitly unmodelled change column is not a quantity.
        word("999", 205, y - 6, 25)
        word("1.20", 320, y, 25)
        word("其他", 515, y, 25)
        if n == 1:
            word("10,00", 250, y - 6, 43)
            word("1", 286, y + 6, 7)
        else:
            word("10,00" + str(n) if n < 10 else "10,010", 250, y, 43)
    word("前十名无限售条件股东持股情况", 90, 700, 430)
    return root, page, words, hints


def xml(root, page, words):
    for w in words:
        el = SubElement(
            page,
            "word",
            xMin=str(w["x"]),
            xMax=str(w["x"] + w["width"]),
            yMin=str(w["y"]),
            yMax=str(w["y"] + 10),
        )
        el.text = w["text"]
    return tostring(root, encoding="unicode")


def test_only_exact_physical_quantity_column_rejoins_actual_digit_fragments():
    root, page, words, hints = fixture()
    old = deepcopy(hints)
    rows, proof = parse_physical_top_matrix(xml(root, page, words), hints)
    assert len(rows) == 10 and rows[0]["quantity"] == Decimal("10001")
    assert rows[0]["raw_quantity"] == "10,001"
    assert rows[0]["raw_percentage"] == "1.20"
    assert rows[0]["percentage"] == Decimal("1.20") and hints == old
    assert proof["layout_contract"] == "explicit-native-holder-physical-columns-v1"


def test_mixed_font_name_digits_follow_physical_line_order():
    root, page, words, hints = fixture()
    w = next(w for w in words if w["text"] == "合成完整股东1")
    w["text"], w["width"] = "合成", 25
    hints[0]["HOLDER_NAME"] = "合成50股东"
    words.extend(
        [
            dict(text="50", x=118, y=w["y"] + 0.1, width=12),
            dict(text="股东", x=133, y=w["y"], width=25),
        ]
    )
    rows, _ = parse_physical_top_matrix(xml(root, page, words), hints)
    assert rows[0]["name"] == "合成50股东"


def test_page_footer_is_only_ignored_as_an_exact_lower_margin_page_fraction():
    root, page, words, hints = fixture()
    page2 = SubElement(root, "page", width="600", height="850")
    first, second = [], []
    for w in words:
        if w["y"] > 420:
            second.append(dict(w, y=w["y"] - 300))
        else:
            first.append(w)
    first.extend(
        [
            dict(text="1", x=295, y=780, width=7),
            dict(text="/", x=305, y=780, width=5),
            dict(text="2", x=313, y=780, width=7),
        ]
    )
    xml(root, page, first)
    body = xml(root, page2, second)
    rows, _ = parse_physical_top_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["raw_quantity"] == "10,001"
    with pytest.raises(RuntimeError):
        parse_physical_top_matrix(body.replace(">1<", ">unknown<", 1), hints)


def test_native_projection_keeps_actual_new_knowledge_and_explicit_parser_version():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

    root, page, words, hints = fixture()
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    args = dict(
        parent=FinancialCompanyIdentity(
            "000400", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
        ),
        business_date=date(2025, 12, 31),
        announcement_id="123",
        official_sha256="sha256:" + "a" * 64,
        hint_sha256="sha256:" + "b" * 64,
        source_known_at=now,
        observed_at=now,
        original_index=dict(secCode="000400", orgId="official-org", announcementId="123"),
        physical_layout_xml=xml(root, page, words),
    )
    rows, proof = project_top_matrix("公司代码：000400\n2025年年度报告\f", hints, **args)
    assert rows[0]["holding_quantity"] == Decimal("10001")
    assert rows[0]["knowledge_from"] == now and proof["original_provider_acquisition"] is None
    assert "explicit-native-holder-physical-columns-v1" in rows[0]["parser_version"]
    with pytest.raises(RuntimeError):
        project_top_matrix("公司代码：000401\n2025年年度报告\f", hints, **args)
    with pytest.raises(RuntimeError):
        project_top_matrix(
            "公司代码：000400\n2025年年度报告\f",
            hints,
            holding_type="TOP_FLOATING_SHAREHOLDERS",
            **args,
        )


@pytest.mark.parametrize("fault", ["missing", "unknown", "changed"])
def test_offline_layout_cannot_run_an_unpinned_or_unknown_parser(monkeypatch, fault):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base231_official_holder_replay import (
        extract_physical_layout,
    )

    module = "src/quanthus/pipelines/securities_warehouse/base231_holder_physical_columns.py"
    candidate = dict(physical_layout_contract="explicit-native-holder-physical-columns-v1")
    codes = {module: file_sha(Path(module))}
    if fault == "missing":
        codes = {}
    elif fault == "unknown":
        candidate["physical_layout_contract"] = "unreviewed"
    else:
        codes[module] = "sha256:" + "0" * 64

    def forbidden(*a, **kw):
        raise AssertionError("subprocess ran before the contract gate")

    monkeypatch.setattr("subprocess.run", forbidden)
    with pytest.raises(RuntimeError, match="PHYSICAL_CONTRACT_OR_CODE_UNPINNED"):
        extract_physical_layout(Path("synthetic.pdf"), candidate, codes)


@pytest.mark.parametrize(
    "fault",
    [
        "unit",
        "column",
        "name",
        "quantity",
        "missing",
        "extra",
        "outside_cell",
        "wrong_rank",
        "unknown_numeric",
        "geometry",
        "zero_width",
        "entity",
    ],
)
def test_physical_columns_fail_closed_without_reconstructing_missing_source(fault):
    root, page, words, hints = fixture()
    if fault == "unit":
        words[0]["text"] = "单位：万股"
    elif fault == "column":
        next(w for w in words if w["text"] == "期末持股")["text"] = "未知字段"
    elif fault == "name":
        next(w for w in words if w["text"] == "合成完整股东1")["text"] += "额外"
    elif fault == "quantity":
        next(w for w in words if w["text"] == "10,00")["text"] = "20,00"
    elif fault == "missing":
        words.remove(next(w for w in words if w["text"] == "1"))
    elif fault == "extra":
        words.append(dict(text="10,011", x=250, y=670, width=43))
    elif fault == "outside_cell":
        next(w for w in words if w["text"] == "1")["x"] = 210
    elif fault == "wrong_rank":
        hints[0]["HOLDER_RANK"] = 2
    elif fault == "unknown_numeric":
        next(w for w in words if w["text"] == "1")["text"] = "?"
    elif fault == "geometry":
        next(w for w in words if w["text"] == "1")["width"] = -10
    elif fault == "zero_width":
        next(w for w in words if w["text"] == "1")["width"] = 0
    body = xml(root, page, words)
    if fault == "entity":
        body = '<!DOCTYPE doc [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' + body
    with pytest.raises(RuntimeError):
        parse_physical_top_matrix(body, hints)
