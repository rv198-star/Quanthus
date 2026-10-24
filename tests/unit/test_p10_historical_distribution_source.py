"""Exact offline source closure, literal units, correction dates and real clocks."""

import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import p10_historical_distribution_source as s


def seal(root, event):
    (root / "facts.json").write_text(json.dumps({"kind": s.VERSION, "events": [event]}))
    files = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": s.sha(p)}
        for p in sorted(root.iterdir())
        if p.name not in {"manifest.json", "SHA256SUMS"}
    ]
    m = {
        "kind": s.VERSION,
        "source_revision": "synthetic-reviewed-complete-plan",
        "source_information_cutoff": event["known_at"],
        "files": files,
        "historical_PIT_claim": False,
        "production_authorized": False,
        "exchange_reference_qualified": False,
        "consumer_admission": "NOT_GRANTED",
    }
    (root / "manifest.json").write_text(json.dumps(m))
    sha = s.sha(root / "manifest.json")
    (root / "SHA256SUMS").write_text(
        "".join(f"{r['sha256']}  {r['path']}\n" for r in files) + f"{sha}  manifest.json\n"
    )
    return sha


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    plan = "总股本100股为基数，向全体股东每股派发现金红利0.25元（含税），每股转增0.40股。"
    table = (
        "                                          新增无限售\n"
        "                                除权（息）                现金红利发\n"
        " 股份类别      股权登记日        最后交易日             条件流通股\n"
        "                                  日                    放日\n"
        "                                           份上市日\n"
        "  Ａ股        2019/5/31     －      2019/6/3   2019/6/4  2019/6/3\n"
    )
    text = "证券代码：123456\n" + plan + "\n" + table
    (root / "pdf-1.txt").write_text(text)
    (root / "pdf.bin").write_bytes(b"%PDF-synthetic-not-market-data")
    detail = "var v3_cninfo = 'https://static.cninfo.com.cn/';"
    (root / "detail.bin").write_text(detail)
    metadata = {
        "announcements": [
            {"announcementId": "1", "secCode": "123456", "adjunctUrl": "synthetic.PDF"}
        ]
    }
    (root / "metadata.bin").write_text(json.dumps(metadata))
    link = {
        "url": "https://static.cninfo.com.cn/synthetic.PDF",
        "metadata_sha256": s.sha(root / "metadata.bin"),
        "detail_sha256": s.sha(root / "detail.bin"),
    }
    (root / "publisher-link-1.json").write_text(json.dumps(link))

    def fact(value, quote):
        start = text.index(quote)
        end = start + len(quote)
        return {
            "value": value,
            "evidence": {
                "notice_id": "1",
                "start": start,
                "end": end,
                "quote": quote,
                "text_sha256": s.sha(root / "pdf-1.txt"),
                "utf8_byte_start": len(text[:start].encode()),
                "utf8_byte_end": len(text[:end].encode()),
            },
        }

    terms = {
        "record_date": fact("2019-05-31", table),
        "ex_date": fact("2019-06-03", table),
        "cash_per_share_gross": fact("0.25", plan),
        "bonus_per_share": fact("0", plan),
        "transfer_per_share": fact("0.40", plan),
    }
    terms.update({n: {"value": None, "unknown": True} for n in ("share_credit_date",)})
    terms["cash_payment_date"] = fact("2019-06-03", table)
    terms["share_listing_date"] = fact("2019-06-04", table)
    event = {
        "security_code": "123456",
        "facts": terms,
        "documents": [
            {
                "notice_id": "1",
                "metadata_member": "metadata.bin",
                "detail_member": "detail.bin",
                "pdf_member": "pdf.bin",
                "pdf_sha256": s.sha(root / "pdf.bin"),
                "text_member": "pdf-1.txt",
                "acquired_at": "2026-10-24T16:00:00+00:00",
            }
        ],
        "known_at": "2026-10-24T16:00:00+00:00",
        "historical_known_at": None,
    }
    return root, event


def test_exact_literal_per_share_preserves_unknown_and_real_observation(source):
    root, event = source
    (result,) = s.load_source(root, seal(root, event))
    assert result.cash_per_share == Decimal("0.25")
    assert result.bonus_share_ratio == Decimal("0.40")
    assert result.ex_date.isoformat() == "2019-06-03"
    assert result.share_listing_date.isoformat() == "2019-06-04"
    assert result.share_credit_date is None
    assert result.acquired_at == datetime(2026, 10, 24, 16, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "mutation",
    [
        "cash",
        "bonus",
        "date",
        "quote",
        "byte-offset",
        "security",
        "clock",
        "historical-clock",
        "publisher",
    ],
)
def test_fully_resealed_source_rejects_semantic_or_clock_or_lineage_forgery(source, mutation):
    root, original = source
    event = copy.deepcopy(original)
    if mutation == "cash":
        event["facts"]["cash_per_share_gross"]["value"] = "2.51"
    elif mutation == "bonus":
        event["facts"]["bonus_per_share"]["value"] = "5.00"
    elif mutation == "date":
        event["facts"]["record_date"]["value"] = "2011-05-30"
    elif mutation == "quote":
        event["facts"]["record_date"]["evidence"]["quote"] += "altered"
    elif mutation == "byte-offset":
        event["facts"]["ex_date"]["evidence"]["utf8_byte_start"] += 1
    elif mutation == "security":
        event["security_code"] = "123457"
    elif mutation == "clock":
        event["known_at"] = "2012-05-29T16:00:00+00:00"
    elif mutation == "historical-clock":
        event["historical_known_at"] = "2012-05-29T16:00:00+00:00"
    else:
        p = root / "publisher-link-1.json"
        link = json.loads(p.read_text())
        link["url"] = "https://unapproved.example/synthetic.PDF"
        p.write_text(json.dumps(link))
    with pytest.raises(ValueError, match="ACTION_SOURCE_"):
        s.load_source(root, seal(root, event))


def test_changed_bytes_extra_file_and_symlink_rejected(source):
    root, event = source
    sha = seal(root, event)
    (root / "pdf.bin").write_bytes(b"%PDF-corrupted")
    with pytest.raises(ValueError, match="MEMBER_CHANGED"):
        s.load_source(root, sha)
    (root / "extra.txt").write_text("unlisted")
    (root / "link").symlink_to(root / "extra.txt")
    with pytest.raises(ValueError, match="PHYSICAL_SET"):
        s.load_source(root, sha)


@pytest.mark.parametrize("mutation", ["listing-as-ex", "invented-credit", "unknown-cash", "float"])
def test_staged_dates_and_literal_value_types_cannot_be_resealed_away(source, mutation):
    root, event = source
    if mutation == "listing-as-ex":
        event["facts"]["share_listing_date"]["value"] = "2019-06-03"
    elif mutation == "invented-credit":
        event["facts"]["share_credit_date"] = copy.deepcopy(event["facts"]["share_listing_date"])
    elif mutation == "unknown-cash":
        event["facts"]["cash_per_share_gross"] = {"value": None, "unknown": True}
    else:
        event["facts"]["cash_per_share_gross"]["value"] = 0.25
    with pytest.raises(ValueError, match="ACTION_SOURCE_"):
        s.load_source(root, seal(root, event))


@pytest.mark.parametrize("mutation", ["ten-shares", "swapped-columns"])
def test_complete_resealed_text_still_requires_unit_and_header_semantics(source, mutation):
    root, event = source
    path = root / "pdf-1.txt"
    text = path.read_text()

    def change(value):
        if mutation == "ten-shares":
            return value.replace("每股", "每10股")
        return (
            value.replace("份上市日", "TEMP")
            .replace("现金红利发", "份上市日")
            .replace("TEMP", "现金红利发")
        )

    new = change(text)
    path.write_text(new)
    for term in event["facts"].values():
        if term["value"] is None:
            continue
        proof = term["evidence"]
        quote = change(proof["quote"])
        begin = new.index(quote)
        end = begin + len(quote)
        proof.update(
            quote=quote,
            start=begin,
            end=end,
            text_sha256=s.sha(path),
            utf8_byte_start=len(new[:begin].encode()),
            utf8_byte_end=len(new[:end].encode()),
        )
    with pytest.raises(ValueError, match="ACTION_SOURCE_|DISTRIBUTION_DATE_"):
        s.load_source(root, seal(root, event))


@pytest.mark.parametrize("credit", [None, date(2019, 6, 3)])
def test_source_qualification_never_grants_early_sale_of_next_day_shares(source, credit):
    """Real consumer gate after valid listing/session SQL, not a mocked verdict."""
    root, event = source
    (fact,) = s.load_source(root, seal(root, event))
    fact = replace(fact, share_credit_date=credit)
    with _reader_connection() as reader:
        with pytest.raises(ValueError, match="IDENTITY_CALENDAR_OR_CREDIT"):
            reader._install_late_actions(SimpleNamespace(manifest_sha256="synthetic"), (fact,))
        # Qualification must not replace the original mapped action surface.
        assert reader._scalar("SELECT count(*) FROM replay_actions_mapped") == 0


def test_same_day_confirmed_credit_and_listing_remains_supported(source):
    root, event = source
    (fact,) = s.load_source(root, seal(root, event))
    fact = replace(fact, share_credit_date=fact.ex_date, share_listing_date=fact.ex_date)
    with _reader_connection() as reader:
        reader._install_late_actions(SimpleNamespace(manifest_sha256="synthetic"), (fact,))
        assert reader._scalar("SELECT count(*) FROM replay_actions_mapped") == 1


@contextmanager
def _reader_connection():
    import duckdb

    from quanthus.expert_strategy.p3_historical_action_successor import SupplementedReader

    reader = SupplementedReader.__new__(SupplementedReader)
    reader._connection = duckdb.connect(config={"threads": 1})
    c = reader._connection
    c.execute(
        "CREATE TABLE replay_actions (security_id VARCHAR,security_code VARCHAR,"
        "action_id VARCHAR,effective_date DATE,known_at TIMESTAMPTZ,"
        "cash_dividend_per_share DECIMAL(20,8),bonus_share_ratio DECIMAL(20,8),"
        "source_revision VARCHAR,source_snapshot_id VARCHAR,evidence_digest VARCHAR)"
    )
    c.execute("CREATE VIEW replay_actions_mapped AS SELECT * FROM replay_actions")
    c.execute(
        "CREATE TABLE replay_validity AS SELECT '123456' AS security_id,"
        "DATE '2011-01-04' AS valid_from,NULL::DATE AS valid_to"
    )
    c.execute(
        "CREATE TABLE replay_calendar AS SELECT 'SSE' AS exchange,day AS trade_date,"
        "1 AS is_open FROM (VALUES (DATE '2019-05-31'),(DATE '2019-06-03')) d(day)"
    )
    c.execute(
        "CREATE TABLE replay_universe AS SELECT '123456' AS security_id,exchange,"
        "trade_date AS market_date FROM replay_calendar"
    )
    c.execute(
        "CREATE TABLE replay_bars AS SELECT '123456' AS security_id,day AS date,close "
        "FROM (VALUES (DATE '2019-05-31',100.00),(DATE '2019-06-03',71.25)) b(day,close)"
    )
    c.execute(
        "CREATE TABLE replay_limits AS SELECT '123456' AS security_id,"
        "DATE '2019-06-03' AS execution_date,10 AS upper_limit_pct,10 AS lower_limit_pct"
    )
    try:
        yield reader
    finally:
        c.close()
