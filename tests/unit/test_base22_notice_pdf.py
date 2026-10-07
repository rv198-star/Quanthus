"""Geometric proof is required before removing a page number from source text."""

import pytest

from quanthus.pipelines.securities_warehouse.base22_notice_pdf import clean_proven_page_footers


def bbox(footers=("1", "2"), y=775, x=297):
    pages = []
    for token in footers:
        pages.append(
            f'<page width="595" height="842"><flow><block>'
            '<line xMin="90" yMin="90" xMax="400" yMax="103">'
            '<word xMin="90" yMin="90" xMax="400" yMax="103">body</word></line>'
            f'<line xMin="{x}" yMin="{y}" xMax="{x + 8}" yMax="{y + 10}">'
            f'<word xMin="{x}" yMin="{y}" xMax="{x + 8}" yMax="{y + 10}">{token}</word>'
            "</line></block></flow></page>"
        )
    return '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + "".join(pages) + "</body></html>"


def test_cross_page_date_loses_only_proven_footer_digits_and_keeps_body_order():
    text = "现金红利1.230000元\n除权除息日：2019年7月\n  1\n\f15日。\n 2\n\f"
    cleaned, proof = clean_proven_page_footers(text, bbox())
    assert cleaned == text.replace("  1\n", "  \n").replace(" 2\n", " \n")
    assert "2019年7月15日" in "".join(cleaned.split())
    assert "1.230000" in cleaned and proof["removed_page_footers"] == [1, 2]
    assert proof["body_digits_changed"] is False


@pytest.mark.parametrize(
    "defect", ["wrong-page", "body-position", "side-position", "only-one-proof"]
)
def test_a_numeric_body_or_unproven_footer_cannot_be_removed(defect):
    text = "body\n1\n\fbody\n2\n\f"
    xml = bbox()
    if defect == "wrong-page":
        xml = bbox(("3", "4"))
    elif defect == "body-position":
        xml = bbox(y=730)
    elif defect == "side-position":
        xml = bbox(x=90)
    else:
        xml = bbox(("1", "9"))
    cleaned, proof = clean_proven_page_footers(text, xml)
    assert cleaned == text and proof["removed_page_footers"] == []


@pytest.mark.parametrize(
    "defect", ["page-count", "nonlast-text", "malformed-geometry", "multiple-footer"]
)
def test_layout_and_bbox_disagreement_fails_closed(defect):
    text = "body\n1\n\fbody\n2\n\f"
    xml = bbox()
    if defect == "page-count":
        text += "third\f"
    elif defect == "nonlast-text":
        text = text.replace("body\n1\n", "1\nbody\n")
    elif defect == "malformed-geometry":
        xml = xml.replace('height="842"', 'height="NaN"')
    else:
        xml = xml.replace(
            "</block>", xml[xml.index('<line xMin="297"') : xml.index("</block>")] + "</block>"
        )
    with pytest.raises(RuntimeError):
        clean_proven_page_footers(text, xml)


def test_body_number_equal_to_page_number_at_another_location_is_preserved():
    text = "重要数量\n1\n除权除息日2019年7月15日\n1\n\f其它数量2\n2\n\f"
    cleaned, proof = clean_proven_page_footers(text, bbox())
    assert "重要数量\n1\n" in cleaned and "其它数量2\n" in cleaned
    assert proof["removed_page_footers"] == [1, 2]
