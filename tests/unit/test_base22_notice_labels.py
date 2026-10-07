"""Literal field-label variants never select a value, security or date."""

import re
import unicodedata
from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_action import (
    select_notice_cash_candidates,
)
from quanthus.pipelines.securities_warehouse.base22_notice_labels import (
    CONTRACT_ID,
    literal_label_view,
    parse_literal_label_cash,
)
from tests.unit.test_base22_notice_action import text


def parse(body):
    return parse_literal_label_cash(
        body,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="年度权益分派实施公告",
    )


@pytest.mark.parametrize(
    "label", ["除权（除息）日", "除权(除息)日", "除权（除息）日：除权（除息）日"]
)
def test_literal_ex_date_labels_keep_the_exact_date_and_decimal(label):
    body = text().replace("除权除息日", label)
    result = parse(body)
    assert result["cash_per_share"] == Decimal("0.15")
    assert result["original_text_sha256"] == digest(body)
    assert result["contract_id"] == CONTRACT_ID
    assert result["source_priority_granted"] is False
    assert result["historical_publication_or_acquisition_inferred"] is False


@pytest.mark.parametrize("label", ["证券代码：证券代码", "股票代码：股票代码", "A股代码：A股代码"])
def test_identical_repeated_field_labels_do_not_change_the_security_value(label):
    body = text().replace("证券代码", label)
    assert parse(body)["stock_code"] == "002001"
    viewed, proof = literal_label_view(body)
    original = unicodedata.normalize("NFKC", body)
    assert re.findall(r"\d+", original) == re.findall(r"\d+", viewed)
    assert proof["original_text_sha256"] == digest(body)
    assert proof["numeric_tokens_changed"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "different-day",
        "second-date",
        "three-digit-day",
        "invalid-day",
        "different-code",
        "repeated-code",
        "mixed-currency",
        "different-label",
        "net-only",
        "cash-conflict",
    ],
)
def test_labels_never_override_unresolved_original_global_guards(defect):
    body = text().replace("除权除息日", "除权（除息）日")
    if defect == "different-day":
        body = body.replace("7月10", "7月11")
    elif defect == "second-date":
        body += "\n除权除息日：2026年8月3日"
    elif defect == "three-digit-day":
        body = body.replace("10日", "101日")
    elif defect == "invalid-day":
        body = body.replace("7月10", "2月30")
    elif defect == "different-code":
        body = body.replace("002001", "002002")
    elif defect == "repeated-code":
        body = body.replace("002001", "002001002001")
    elif defect == "mixed-currency":
        body += "\nB股另行派发现金"
    elif defect == "different-label":
        body = body.replace("证券代码", "交易标识")
    elif defect == "net-only":
        body = body.replace("含税", "扣税后")
    else:
        body += "\n每股派发现金红利0.16元（含税）"
    with pytest.raises(BoundedError, match="NOTICE"):
        parse(body)


def test_extra_literal_profile_is_explicit_and_default_remains_frozen():
    targets = [{"action_id": "a", "stock_code": "002001", "ex_date": "2026-07-10"}]
    document = {
        "document_id": "1",
        "target_action_ids": ["a"],
        "publisher_title": "权益分派实施公告",
        "publisher_security_code": "002001",
        "text": text().replace("除权除息日", "除权（除息）日"),
    }
    assert not select_notice_cash_candidates(targets, [document])[0]
    selected, evidence = select_notice_cash_candidates(
        targets, [document], parser_profile=CONTRACT_ID
    )
    assert selected["a"]["atom"]["cash_per_share"] == Decimal("0.15")
    assert evidence["contract_id"] == CONTRACT_ID


def test_complete_day_suffix_separates_a_following_numbered_heading():
    body = text().replace("除权除息日", "除权（除息）日")
    body += "\n3.现金红利发放办法"
    assert parse(body)["cash_per_share"] == Decimal("0.15")


def test_label_atom_keeps_exact_before_versions_and_real_acquisition():
    from tests.unit.test_base22_notice_restatement import case, project

    values = list(case())
    body = text().replace("除权除息日", "除权（除息）日").replace("2026年7月10日", "2019年6月3日")
    atom = parse_literal_label_cash(
        body, stock_code="002001", ex_date=date(2019, 6, 3), publisher_title="权益分派实施公告"
    )
    values[3] = atom
    values[4]["notice_atom_sha256"] = digest(atom)
    actions, terms, proof = project(values)
    assert terms[-1]["amount_per_share"] == Decimal("0.15")
    assert actions[-1]["knowledge_from"] >= proof["actual_new_acquisition"]
