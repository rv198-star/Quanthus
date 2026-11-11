from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import base23_dividend_units as u
from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_dividend import parse_september_dividend_content
from tests.unit.test_base23_dividend import raw


@pytest.mark.parametrize("plan", ["10转2.5股 派4.123456789元(含税)",
                                  "10送2股 转3股 派1元（含税）", "10转2.5股", "10送2股"])
def test_explicit_share_units_are_exact_without_changing_frozen_parser(plan):
    row = raw(plan=plan)
    assert not parse_september_dividend_content(row)[0]
    before = dict(row)
    with localcontext() as context:
        context.prec = 3
        records, proof = u.parse_unit_additions(row)
    assert len(records) == 1
    assert row == before
    assert proof["blocks"][0]["source_denominator"] == 10
    assert not proof["tax_for_absent_cash_inferred"]
    if "派" not in plan:
        assert records[0]["cash_per_share"] is None
        assert proof["blocks"][0]["cash_basis"] is None
    if "4.123456789" in plan:
        assert records[0]["cash_per_share"] == Decimal("0.4123456789")


@pytest.mark.parametrize("plan", ["20转2股", "010转2股", "10转2", "10转2股 派1港元(含税)",
    "10转2股 派1元(税后)", "10转2股 派1元", "10转2股 派NaN元(含税)",
    "10转2股 派0.00000000001元(含税)", "每股转2股", "10转2股\n｜｜另有派息待定｜｜"])
def test_unsupported_units_cash_or_precision_stay_unavailable(plan):
    assert not u.parse_unit_additions(raw(plan=plan))[0]


def test_already_qualified_and_proposed_plans_are_not_added():
    assert not u.parse_unit_additions(raw())[0]
    assert not u.parse_unit_additions(raw(plan="10转2股", status="预案"))[0]


def test_no_guess_of_missing_or_future_business_date():
    assert not u.parse_unit_additions(raw(ex="2026-10-01", plan="10转2股"))[0]
    row = raw(plan="10转2股")
    row["content"] = row["content"].replace("2026-09-03", "日期未知")
    row["content_sha256"] = "sha256:" + hashlib.sha256(row["content"].encode()).hexdigest()
    assert not u.parse_unit_additions(row)[0]


def project(row, *, parent=True, observed=None):
    _, model, ext = load_contract(Path("config/pipelines"))
    identity = SimpleNamespace(company_id="c", security_id="s",
                              knowledge_from=datetime(2026, 8, 4, tzinfo=timezone.utc))
    resolver = SimpleNamespace(resolve_for_report=lambda *a, **kw:
                               (identity, None) if parent else (None, "BEFORE_FIRST_LISTING"))
    return u.materialize_unit_additions(
        row, source_sha256="sha256:" + "a" * 64, revision="retained-raw-revision",
        resolver=resolver,
        observed_at=observed or datetime(2026, 10, 9, 17, tzinfo=timezone.utc),
        vocabulary=json.loads(Path("config/pipelines/disclosure-event-vocabulary.v1.json")
                              .read_text()), schemas=schemas(model, ext),
    )


def test_actual_typed_terms_preserve_decimal_clocks_and_absent_cash():
    with localcontext() as context:
        context.prec = 3
        rows, proof = project(raw(plan="10转2.5股 派4.123456789元(含税)"))
    terms = {r["term_type"]: r for r in rows["dwd_corporate_action_term"]}
    assert terms["CASH_AMOUNT_PER_SHARE"]["amount_per_share"] == Decimal("0.4123456789")
    assert terms["TRANSFER_SHARES_PER_SHARE"]["ratio_numerator"] == Decimal("0.25")
    assert rows["dwd_corporate_action"][0]["knowledge_from"] == proof["observed_at"]
    assert rows["dwd_corporate_action"][0]["announced_at"] is None
    assert proof["original_acquired_at"] == raw()["acquired_at"]
    share_rows, _ = project(raw(plan="10转2.5股"))
    assert [r["term_type"] for r in share_rows["dwd_corporate_action_term"]] == [
        "TRANSFER_SHARES_PER_SHARE"]


def test_capture_and_identity_gates_do_not_invent_acceptance():
    with pytest.raises(BoundedError, match="OBSERVATION_INVALID"):
        project(raw(plan="10转2股"), observed=datetime(2026, 9, 1, tzinfo=timezone.utc))
    row = raw(plan="10转2股")
    row["source_revision"] = "unknown"
    with pytest.raises(BoundedError, match="ORIGINAL_BINDING"):
        project(row)
    rows, proof = project(raw(ex="2026-09-10", plan="10转2股"))
    assert not any(rows.values())
    assert proof["holds"][0]["reason"] == "AFTER_ORIGINAL_CAPTURE"
    rows, proof = project(raw(plan="10转2股"), parent=False)
    assert not any(rows.values())
    assert proof["holds"][0]["reason"] == "BEFORE_FIRST_LISTING"


@pytest.mark.parametrize("conflict", [False, True])
def test_same_literal_event_duplicates_or_conflicts_are_explicit(conflict):
    row = raw(plan="10转2股")
    block = row["content"].split("\n【增发】")[0].removeprefix("【分红】\n")
    if conflict:
        block = block.replace("10转2股", "10转3股")
    row["content"] = row["content"].replace("【增发】", block + "\n【增发】")
    row["content_sha256"] = "sha256:" + hashlib.sha256(row["content"].encode()).hexdigest()
    rows, proof = project(row)
    assert digest(row) == proof["original_raw_row_sha256"]
    assert proof["conflicting_event_keys"] == int(conflict)
    assert len(rows["dwd_corporate_action"]) == int(not conflict)
    assert proof["identical_repeats"] == int(not conflict)
