from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_f10_dividend import (
    MAX_BLOCKS,
    MAX_CONTENT_BYTES,
    materialize_dividend_record,
    parse_dividend_content,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def raw(*, plan="10派4.123456789元(含税)", status="实施", ex="2026-07-10", note=""):
    content = (
        "【分红】\n"
        f"｜2025-12-31｜进展说明:{status}｜其他列｜\n"
        f"｜｜{plan}｜其他列｜\n"
        f"｜｜预案公布日:2026-06-01除权除息日:{ex}股权登记日:2026-07-09｜其他列｜\n"
        f"｜｜{note}｜其他列｜\n【增发】\n"
    )
    return {
        "stock_code": "000001",
        "category": "分红扩股",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "acquired_at": "2026-08-01T12:01:02.123456+08:00",
    }


def parse(r=None):
    return parse_dividend_content(raw() if r is None else r)


def test_exact_per_ten_plan_does_not_depend_on_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 3
        rows, evidence = parse()
    assert rows[0]["cash_per_share"] == Decimal("0.4123456789")
    assert evidence["publication_instant"] == "UNAVAILABLE_DATE_PRECISION_ONLY"
    assert evidence["provider_acquisition"] == "UNAVAILABLE"
    assert evidence["source_acquisition_instant"].microsecond == 123456
    assert evidence["blocks"][0]["numeric"]["cash_per_share"]["source_denominator"] == 10


def test_all_term_roles_and_zero_absence_remain_distinct():
    rows, e = parse(raw(plan="10送0转增2.5派3.6元(含税)"))
    assert rows[0]["bonus_shares"] == 0
    assert rows[0]["transfer_shares"] == Decimal("0.25")
    assert rows[0]["cash_per_share"] == Decimal("0.36")
    assert rows[0]["action_type"] == "mixed"
    assert e["blocks"][0]["numeric"]["bonus_shares"]["classification"] == "ZERO"
    only, proof = parse()
    assert only[0]["bonus_shares"] is None
    assert proof["blocks"][0]["numeric"]["bonus_shares"]["classification"] == "ABSENT"


@pytest.mark.parametrize(
    "arguments,reason",
    [
        ({"status": "预案"}, "IMPLEMENTATION_NOT_ESTABLISHED"),
        ({"note": "本期含优先股股息"}, "PREFERRED_SHARE_OR_MIXED_NOTE_IDENTITY_UNQUALIFIED"),
        ({"plan": "20派4.0元(含税)"}, "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"),
        ({"plan": "110派4.0元(含税)"}, "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"),
        ({"plan": "10派4.0美元(含税)"}, "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"),
        ({"plan": "10派4.0元(税后)"}, "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"),
        ({"plan": "10派NaN元(含税)"}, "PER_TEN_STANDARD_PLAN_NOT_ESTABLISHED"),
        ({"plan": "10派0.12345678901元(含税)"}, "CANONICAL_DECIMAL_NOT_EXACTLY_REPRESENTABLE"),
        ({"ex": ""}, "EX_DIVIDEND_DATE_UNAVAILABLE"),
        ({"ex": "2026-02-30"}, "INVALID_SOURCE_DATE"),
        ({"ex": "2026-09-01"}, "EXCLUDED_AFTER_BUSINESS_CUTOFF"),
        ({"note": "除权除息日:2026-07-11"}, "CONFLICTING_SOURCE_DATES"),
        ({"note": "10派1.0元(含税)"}, "MULTIPLE_PLAN_TOKENS_UNQUALIFIED"),
    ],
)
def test_unqualified_dated_block_has_exact_safe_disposition(arguments, reason):
    rows, evidence = parse(raw(**arguments))
    assert rows == []
    assert evidence["dated_blocks"] == 1
    assert evidence["blocks"][0]["disposition"] == reason
    assert not evidence["old_correction001_restoration"]


def test_no_dividend_section_never_establishes_no_change():
    r = raw()
    r["content"] = "【分红扩股】\n没有分红块"
    r["content_sha256"] = hashlib.sha256(r["content"].encode()).hexdigest()
    rows, evidence = parse(r)
    assert rows == []
    assert not evidence["dividend_section_present"]
    assert not evidence["no_dated_blocks_is_full_no_change_evidence"]


@pytest.mark.parametrize("change", ["hash", "category", "naive", "submicrosecond", "content_size"])
def test_source_integrity_and_clock_bounds_fail_closed(change):
    r = raw()
    if change == "hash":
        r["content"] += "changed"
    elif change == "category":
        r["category"] = "股东研究"
    elif change == "naive":
        r["acquired_at"] = "2026-08-01T12:01:02"
    elif change == "submicrosecond":
        r["acquired_at"] = "2026-08-01T12:01:02.1234567+08:00"
    else:
        r["content"] = "a" * (MAX_CONTENT_BYTES + 1)
    with pytest.raises((BoundedError, SourceAssessmentError)):
        parse(r)


def test_content_block_bound():
    r = raw()
    r["content"] = "【分红】\n" + "｜2025-12-31｜进展说明:实施｜｜\n" * (MAX_BLOCKS + 1)
    r["content_sha256"] = hashlib.sha256(r["content"].encode()).hexdigest()
    with pytest.raises(BoundedError, match="BLOCK_BOUND"):
        parse(r)


def test_exact_typed_materialization_keeps_parent_time_and_never_double_divides():
    _, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)
    vocab = json.loads(Path("config/pipelines/disclosure-event-vocabulary.v1.json").read_text())
    original = dict(vocab)
    rows, _ = parse(raw(plan="10送2转1派4.123456789元(含税)"))
    clock = datetime(2026, 8, 4, 12, 1, 2, 123456, tzinfo=timezone.utc)
    parent = SimpleNamespace(company_id="c", security_id="s", knowledge_from=clock)
    with localcontext() as ctx:
        ctx.prec = 3
        out = materialize_dividend_record(
            rows[0],
            acquired_at=raw()["acquired_at"],
            source_sha256="sha256:" + "a" * 64,
            revision="test",
            parent=parent,
            vocabulary=vocab,
            schemas=schema,
        )
    assert vocab == original
    assert out["dwd_corporate_action"][0]["knowledge_from"] == clock
    assert out["dwd_corporate_action"][0]["announced_at"] is None
    terms = {r["term_type"]: r for r in out["dwd_corporate_action_term"]}
    assert terms["BONUS_SHARES_PER_SHARE"]["ratio_numerator"] == Decimal("0.2")
    assert terms["TRANSFER_SHARES_PER_SHARE"]["ratio_numerator"] == Decimal("0.1")
    assert terms["CASH_AMOUNT_PER_SHARE"]["amount_per_share"] == Decimal("0.4123456789")
