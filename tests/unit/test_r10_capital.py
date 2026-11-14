from decimal import Decimal

import pytest

from quanthus.backtest.engine import UnsupportedModeError
from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.r10_capital import audit_semantic, failure_class, trade_summary
from quanthus.r10_inputs import write


def event(sequence, kind, day, payload):
    return {
        "event_id": str(sequence),
        "sequence": sequence,
        "event_time": day + "T15:00:00+08:00",
        "event_type": kind,
        "payload": payload,
    }


def ledger(sequence, day, kind, quantity, postings):
    return event(
        sequence,
        "ledger_entry",
        day,
        {
            "unit_id": "U",
            "security_code": "L",
            "reference_type": kind,
            "quantity_delta": quantity,
            "postings": [
                {"account": "unit/U/" + key, "amount": str(value)}
                for key, value in postings.items()
            ],
        },
    )


def test_roundtrip_cash_dividend_fees_and_profit_pool_balance():
    events = [
        ledger(
            1,
            "2021-06-03",
            "fill",
            100,
            {"cash": -1005, "position_cost/L": 1000, "commission_expense": 5},
        ),
        event(
            2,
            "daily_snapshot",
            "2021-06-03",
            {"unit_id": "U", "position_quantity": 100, "session_date": "2021-06-03"},
        ),
        ledger(
            3,
            "2021-06-15",
            "corporate_action_cash_dividend",
            30,
            {"cash": 20, "dividend_income": -20},
        ),
        ledger(
            4,
            "2021-06-16",
            "fill",
            -130,
            {
                "cash": 1090,
                "position_cost/L": -1000,
                "realized_gain": -100,
                "commission_expense": 10,
            },
        ),
        ledger(5, "2021-06-16", "capital_pool_transfer", 0, {"cash": -105, "external_pool": 105}),
        event(
            6,
            "daily_snapshot",
            "2021-06-16",
            {"unit_id": "U", "position_quantity": 0, "session_date": "2021-06-16"},
        ),
    ]
    result = trade_summary(list(reversed(events)), {"L": {"company_id": "C"}})
    assert result["closed_roundtrips"] == 1 and result["open_roundtrips"] == 0
    assert result["closed_pnl_by_company"] == {"C": Decimal(105)}
    assert result["wins"] == 1 and result["win_rate"] == "1"
    events[0]["payload"]["postings"][0]["amount"] = "-1004"
    with pytest.raises(ValueError, match="LEDGER_UNBALANCED"):
        trade_summary(events, {"L": {"company_id": "C"}})


def test_cold_audit_ignores_only_observed_wall_clock(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write(
        a,
        {
            "audit_digest": "first",
            "frames": [
                {"observed_at": "2026-11-14T09:00:00Z", "ledger_digest": "fixed", "cash": "100.00"}
            ],
        },
    )
    write(
        b,
        {
            "audit_digest": "second",
            "frames": [
                {"observed_at": "2026-11-14T10:00:00Z", "ledger_digest": "fixed", "cash": "100.00"}
            ],
        },
    )
    assert audit_semantic(a) == audit_semantic(b)
    b.write_text(b.read_text().replace("100.00", "99.00"))
    assert audit_semantic(a) != audit_semantic(b)


def test_source_hold_does_not_mask_an_implementation_error():
    text = "corporate action creates a fractional share outside the first P2 rule"
    assert failure_class(UnsupportedModeError(text)) == "SOURCE_HOLD"
    assert failure_class(ValueError(text)) == "IMPLEMENTATION_FAILURE"
    assert (
        failure_class(ExpertEvaluationError("formal_sell_bonus_share_unsupported", "detail"))
        == "SOURCE_HOLD"
    )
    assert (
        failure_class(ExpertEvaluationError("other", "quantity-changing action"))
        == "IMPLEMENTATION_FAILURE"
    )
