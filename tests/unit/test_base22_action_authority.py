from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_action_authority import (
    materialize_implemented_action,
    parse_implemented_action,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError

CLOCK = datetime(2026, 10, 5, tzinfo=timezone.utc)


def raw(**updates):
    return {
        "SECURITY_CODE": "000001",
        "ASSIGN_PROGRESS": "实施分配",
        "EX_DIVIDEND_DATE": "2026-07-25T00:00:00",
        "NOTICE_DATE": "2026-07-20T00:00:00",
        "EQUITY_RECORD_DATE": "2026-07-24T00:00:00",
        "IMPL_PLAN_PROFILE": "10派0.123456789元(含税)",
        "PRETAX_BONUS_RMB": Decimal("0.123456789"),
        "BONUS_RATIO": None,
        "IT_RATIO": Decimal("0"),
        **updates,
    }


def test_exact_tokens_units_and_null_zero_classes_keep_dates_out_of_knowledge():
    data = raw()
    original = deepcopy(data)
    record, proof = parse_implemented_action(data, source_row_ref="em_raw/000001.json#rows[0]")
    assert data == original
    assert record["cash_per_share"] == Decimal("0.0123456789")
    assert record["bonus_shares"] is None and record["transfer_shares"] == 0
    assert proof["numeric_classes"] == {
        "cash_per_share": "EXACT",
        "bonus_shares": "NULL",
        "transfer_shares": "ZERO",
    }
    assert record["announce_date"] == date(2026, 7, 20)
    assert proof["provider_acquisition"] is proof["publication_instant"] is None
    _, model, extension = load_contract(Path("config/pipelines"))
    vocabulary = json.loads(
        Path("config/pipelines/disclosure-event-vocabulary.v1.json").read_bytes()
    )
    out = materialize_implemented_action(
        record,
        observed_at=CLOCK,
        source_sha256="sha256:" + "a" * 64,
        revision="new-scope",
        parent=SimpleNamespace(company_id="c", security_id="s", knowledge_from=CLOCK),
        vocabulary=vocabulary,
        schemas=schemas(model, extension),
    )
    assert out["dwd_corporate_action"][0]["announced_at"] is None
    assert out["dwd_corporate_action"][0]["knowledge_from"] == CLOCK
    assert out["dwd_corporate_action_term"][0]["amount_per_share"] == Decimal("0.0123456789")
    assert out["dwd_corporate_action_term"][0]["ratio_numerator"] is None


@pytest.mark.parametrize(
    "updates",
    [
        {"ASSIGN_PROGRESS": "预案"},
        {"SECURITY_CODE": "bad"},
        {"EX_DIVIDEND_DATE": "2026-09-01"},
        {"NOTICE_DATE": "2026-07-25"},
        {"IMPL_PLAN_PROFILE": "unknown"},
        {"PRETAX_BONUS_RMB": None},
        {"PRETAX_BONUS_RMB": Decimal("0.123456788")},
        {"PRETAX_BONUS_RMB": 0.123456789},
        {"PRETAX_BONUS_RMB": Decimal("NaN")},
        {"PRETAX_BONUS_RMB": Decimal("0.12345678901"), "IMPL_PLAN_PROFILE": "10派0.12345678901元"},
    ],
)
def test_invalid_unhandled_or_inexact_selected_authority_is_not_admitted(updates):
    with pytest.raises(BoundedError):
        parse_implemented_action(raw(**updates), source_row_ref="em_raw/000001.json#rows[0]")


def test_retrieval_instant_is_retained_without_becoming_provider_acquisition():
    _, proof = parse_implemented_action(raw(), source_row_ref="row", fetched_at=CLOCK.isoformat())
    assert proof["response_fetched_at"] == CLOCK
    assert proof["provider_acquisition"] is proof["publication_instant"] is None


def test_retained_sql_midnight_date_encoding_is_not_a_publication_instant():
    record, proof = parse_implemented_action(
        raw(
            EX_DIVIDEND_DATE="2026-07-25 00:00:00",
            NOTICE_DATE="2026-07-20 00:00:00",
            EQUITY_RECORD_DATE="2026-07-24 00:00:00",
        ),
        source_row_ref="retained SQL date",
    )
    assert record["ex_date"] == date(2026, 7, 25)
    assert proof["notice_date"] == date(2026, 7, 20)
    assert proof["publication_instant"] is None
