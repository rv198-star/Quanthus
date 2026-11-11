from __future__ import annotations

import copy
import hashlib

import pytest

from quanthus.pipelines.securities_warehouse import base23_calendar_authority as a
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def fixture():
    periods = ["元旦:1月1日至1月3日", "春节:2月15日至2月23日", "清明节:4月4日至4月6日",
               "劳动节:5月1日至5月5日", "端午节:6月19日至6月21日",
               "中秋节:9月25日至9月27日", "国庆节:10月1日至10月7日"]
    docs = {}
    config = {"contract_id": a.CONTRACT_ID, "business_start": "2026-08-01",
              "business_through": "2026-09-30", "timezone": "Asia/Shanghai",
              "claim": "EXCHANGE_BUSINESS_DATES_ONLY", "observed_at": "2026-10-10T01:00:00+08:00",
              "documents": {}}
    for venue, (host, issuer, number) in a._ISSUERS.items():
        blocks = []
        for ordinal, period in zip("一二三四五六七", periods, strict=True):
            text = period.replace("日", "日（星期五）")
            blocks.append(f"（{ordinal}）{text}休市。")
        body = (f"<html><script>假的休市日期</script><p>{issuer}{number}</p>"
                "<p>2026年部分节假日休市安排</p><p>一、休市安排</p>"
                + "".join(blocks) + f"<p>二、清算事宜{issuer}2025年12月22日</p></html>")
        docs[venue] = body.encode()
        config["documents"][venue] = {"url": f"https://{host}/annual",
            "sha256": "sha256:" + hashlib.sha256(docs[venue]).hexdigest(),
            "retrieved_at": "2026-10-10T00:50:00+08:00", "publication_date_literal": "2025-12-22"}
    return config, docs


def mirror(authority):
    venues = [{"venue_id": venue, "mic_code": mic, "timezone": "Asia/Shanghai"}
              for mic, venue in a._MIC_VENUES.items()]
    rows = [{"venue_id": r["exchange_code"], "trade_date": r["trade_date"],
             "is_open": r["is_open"], "session_type": "REGULAR_DAY",
             "knowledge_from": "retained-real-time", "open_at": None} for r in authority["rows"]]
    return rows, venues


def test_complete_three_venue_authority_and_all_versions_preserve_unknown_times():
    config, docs = fixture()
    proof = a.derive_date_authority(config, docs)
    rows, venues = mirror(proof)
    rows.append(copy.deepcopy(rows[0]))  # A second retained knowledge version is checked too.
    rows[-1]["session_type"] = "day"  # Explicit successor label, same date-only contract.
    before = copy.deepcopy(rows)
    result = a.compare_retained_calendar(proof, rows, venues)
    assert result["distinct_venue_dates"] == 183
    assert result["calendar_versions_verified"] == 184
    assert sum(r["is_open"] for r in proof["rows"]) == 126
    assert not any(r["is_open"] for r in proof["rows"] if r["trade_date"] == "2026-09-25")
    assert rows == before
    assert not proof["historical_internal_acquisition_recovered"]
    assert not proof["listing_coverage_qualified"]
    assert not proof["session_hours_qualified"]
    assert proof["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize("damage", ["hash", "host", "time", "publication", "venue", "scope"])
def test_wrong_original_identity_scope_or_actual_observation_fails(damage):
    config, docs = fixture()
    item = config["documents"]["SSE"]
    if damage == "hash":
        docs["SSE"] += b"changed"
    elif damage == "host":
        item["url"] = "https://www.sse.com.cn.evil.test/annual"
    elif damage == "time":
        item["retrieved_at"] = "2026-10-10T01:01:00+08:00"
    elif damage == "publication":
        item["publication_date_literal"] = "2026-09-30"
    elif damage == "venue":
        del docs["BSE"]
    else:
        config["business_through"] = "2026-10-01"
    with pytest.raises(BoundedError):
        a.derive_date_authority(config, docs)


@pytest.mark.parametrize("damage", ["issuer", "year", "missing_holiday", "invalid_date"])
def test_repacked_unsupported_notice_fails_after_rehash(damage):
    config, docs = fixture()
    body = docs["BSE"].decode()
    if damage == "issuer":
        body = body.replace("北京证券交易所", "某供应商")
    elif damage == "year":
        body = body.replace("2026年", "2025年")
    elif damage == "missing_holiday":
        body = body.replace("（六）中秋节", "（六）未审定项目")
    else:
        body = body.replace("9月25日", "9月35日")
    docs["BSE"] = body.encode()
    config["documents"]["BSE"]["sha256"] = "sha256:" + hashlib.sha256(docs["BSE"]).hexdigest()
    with pytest.raises(BoundedError):
        a.derive_date_authority(config, docs)


@pytest.mark.parametrize("damage", ["closed_day", "missing", "venue", "session", "older_version"])
def test_mirror_date_venue_and_old_version_conflicts_fail(damage):
    config, docs = fixture()
    proof = a.derive_date_authority(config, docs)
    rows, venues = mirror(proof)
    if damage == "closed_day":
        next(r for r in rows if r["trade_date"] == "2026-09-25")["is_open"] = True
    elif damage == "missing":
        rows.pop()
    elif damage == "venue":
        rows[0]["venue_id"] = "unqualified"
    elif damage == "session":
        rows[0]["session_type"] = "unreviewed_session"
    else:
        old = copy.deepcopy(rows[0])
        old["is_open"] = not old["is_open"]
        rows.append(old)
    with pytest.raises(BoundedError):
        a.compare_retained_calendar(proof, rows, venues)
