"""Official pagination must stop before repeated pages spend more source budget."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.securities_warehouse.p10_notice_census import NoticeCensus


def notice(identifier: str, *, code: str = "000001", org: str = "org1") -> dict:
    clock = datetime(2020, 1, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
    return {
        "secCode": code, "orgId": org, "announcementId": identifier,
        "announcementTime": int(clock.timestamp()*1000),
        "announcementTitle": "合成公告",
    }


def census() -> NoticeCensus:
    return NoticeCensus("000001", "org1", date(2020, 1, 1), date(2020, 1, 31))


def page(rows: list[dict], *, total: int = 3, more: bool = True) -> dict:
    return {"announcements": rows, "totalAnnouncement": total, "hasMore": more}


def test_repeated_page_stops_on_first_no_progress_and_retains_prior_state() -> None:
    c = census()
    first = page([notice("a"), notice("b")])
    c.accept(1, first)
    with pytest.raises(ValueError, match="NOTICE_CENSUS_NO_PROGRESS"):
        c.accept(2, first)
    assert c.pages == 1 and c.unique_count == 2 and not c.complete


def test_terminal_distinct_count_and_exact_identity_close_two_pages() -> None:
    c = census()
    c.accept(1, page([notice("a"), notice("b")]))
    c.accept(2, page([notice("c")], more=False))
    assert c.complete and c.unique_count == 3
    assert [r["announcementId"] for r in c.notices] == ["a", "b", "c"]


@pytest.mark.parametrize("wrong", [notice("c", code="000002"), notice("c", org="other")])
def test_other_security_or_organization_cannot_complete_inventory(wrong: dict) -> None:
    c = census()
    with pytest.raises(ValueError, match="NOTICE_CENSUS_ISSUER"):
        c.accept(1, page([wrong], total=1, more=False))
    assert c.pages == 0 and c.unique_count == 0


@pytest.mark.parametrize("last", [
    page([], total=3, more=True), page([notice("c")], total=4, more=False),
    page([], total=3, more=False),
])
def test_incomplete_or_changed_count_never_passes(last: dict) -> None:
    c = census()
    c.accept(1, page([notice("a"), notice("b")]))
    with pytest.raises(ValueError):
        c.accept(2, last)
    assert not c.complete and c.pages == 1 and c.unique_count == 2


def test_conflicting_duplicate_is_rejected_before_state_mutation() -> None:
    c = census()
    c.accept(1, page([notice("a")]))
    conflict = dict(notice("a"), announcementTitle="修订内容")
    with pytest.raises(ValueError, match="NOTICE_CENSUS_CONFLICT"):
        c.accept(2, page([conflict, notice("b")]))
    assert c.unique_count == 1 and c.pages == 1


def test_publication_date_uses_shanghai_and_rejects_out_of_range() -> None:
    c = census()
    clock = datetime(2020, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    boundary = dict(notice("a"), announcementTime=int(clock.timestamp()*1000))
    c.accept(1, page([boundary], total=1, more=False))
    assert c.complete
    before = dict(boundary, announcementTime=boundary["announcementTime"]-1)
    with pytest.raises(ValueError, match="NOTICE_CENSUS_DATE"):
        census().accept(1, page([before], total=1, more=False))


def test_zero_terminal_page_is_only_an_inventory_result_not_action_qualification() -> None:
    c = census()
    c.accept(1, page([], total=0, more=False))
    assert c.complete and c.notices == ()
    with pytest.raises(ValueError, match="NOTICE_CENSUS_PAGE_ORDER"):
        c.accept(2, page([], total=0, more=False))


def test_caller_mutation_cannot_change_the_retained_inventory() -> None:
    c = census()
    row = dict(notice("a"), secNameList=["合成名称"])
    c.accept(1, page([row], total=1, more=False))
    row["secNameList"].append("改动")
    returned, = c.notices
    returned["secNameList"].append("另一次改动")
    assert c.notices[0]["secNameList"] == ["合成名称"]
