from datetime import date, timedelta

import pytest

from quanthus.pipelines.securities_warehouse.p3_calendar import bind_warmup_calendar

SHA = "sha256:" + "a" * 64
NOTICES = {
    "XSHG": {
        "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml",
        "body": b"",  # Synthetic declarations below, never real source evidence.
    },
    "XSHE": {
        "url": "https://www.szse.cn/disclosure/notice/t20231226_605108.html",
        "body": b"",
    },
}


def notices():
    text = """2024年部分节假日休市安排 2023年12月26日
    元旦：2023年12月30日至2024年1月1日休市。
    春节：2月9日至2月17日休市。清明节：4月4日至4月6日休市。
    劳动节：5月1日至5月5日休市。端午节：6月10日休市。
    中秋节：9月15日至9月17日休市。国庆节：10月1日至10月7日休市。
    周末休市。"""
    return {
        mic: {**v, "body": (text + issuer).encode()}
        for (mic, v), issuer in zip(
            NOTICES.items(), ("上海证券交易所", "深圳证券交易所"), strict=True
        )
    }


def records():
    closed = {date(2024, 1, 1), date(2024, 6, 10)}
    for m, a, b in [(2, 9, 17), (4, 4, 6), (5, 1, 5), (9, 15, 17), (10, 1, 7)]:
        closed.update(date(2024, m, d) for d in range(a, b + 1))
    day = date(2024, 1, 1)
    result = []
    while day.year == 2024:
        for mic in NOTICES:
            result.append(
                {
                    "mic_code": mic,
                    "trade_date": day,
                    "calendar_session_id": f"{mic}-{day}",
                    "is_open": day.weekday() < 5 and day not in closed,
                    "knowledge_from": "2026-10-20T23:00:00+08:00",
                }
            )
        day += timedelta(days=1)
    return result


def bind(rows=None, docs=None):
    return bind_warmup_calendar(
        notices() if docs is None else docs,
        records() if rows is None else rows,
        source_manifest_sha256=SHA,
        calendar_file_sha256=SHA,
        venue_file_sha256=SHA,
    )


def test_exact_sixty_bound_to_both_exchanges_without_backdating():
    r = bind()
    sessions = r["binding"]["sessions"]
    assert len(sessions) == 60
    assert sessions[0]["trade_date"] == "2024-10-09"
    assert sessions[-1]["trade_date"] == "2024-12-31"
    assert r["membership_status"] == "MATCHES_OFFICIAL_DECLARED_SCHEDULE"
    assert r["historical_market_value_availability_qualified"] is False
    assert r["warehouse_knowledge_unchanged"] is True
    assert r["B_accepted"] is False
    assert r["native_asof_warmup_accepted"] is False


@pytest.mark.parametrize("defect", ["missing", "closed", "duplicate", "identity"])
def test_bad_calendar_does_not_shift_or_substitute_sessions(defect):
    rows = records()
    i = next(i for i, r in enumerate(rows) if r["trade_date"] == date(2024, 12, 31))
    if defect == "missing":
        rows.pop(i)
    elif defect == "closed":
        rows[i]["is_open"] = False
    elif defect == "duplicate":
        rows.append(dict(rows[i]))
    else:
        rows[i]["calendar_session_id"] = rows[i - 2]["calendar_session_id"]
    with pytest.raises(ValueError):
        bind(rows)


@pytest.mark.parametrize("defect", ["host", "body", "holiday", "publication"])
def test_incomplete_or_conflicting_official_declaration_is_not_authority(defect):
    docs = notices()
    if defect == "host":
        docs["XSHE"]["url"] = "https://example.test/calendar"
    elif defect == "body":
        docs["XSHE"]["body"] = b"unavailable"
    elif defect == "holiday":
        docs["XSHE"]["body"] = docs["XSHE"]["body"].replace("10月7日".encode(), "10月8日".encode())
    else:
        docs["XSHE"]["body"] = docs["XSHE"]["body"].replace(
            "2023年12月26日".encode(), "2026年10月20日".encode()
        )
    with pytest.raises(ValueError):
        bind(docs=docs)
