from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quanthus.stage_learning.entry_capital_analysis import compare, issuer_contributions


def payload(profit):
    dates = [(date(2021, 4, 2) + timedelta(days=i)).isoformat() for i in range(12)]
    return {
        "equity": [
            {"market_date": day, "system_equity": str(500000 + (profit if i else 0))}
            for i, day in enumerate(dates)
        ],
        "events": [
            {
                "event_type": "ledger_entry",
                "payload": {
                    "security_code": "A",
                    "postings": [{"account": "cash", "amount": "-1000"}],
                },
            },
            {
                "event_type": "ledger_entry",
                "payload": {
                    "security_code": "A",
                    "postings": [{"account": "cash", "amount": str(1000 + profit)}],
                },
            },
        ],
        "ledgers": [
            {
                "cash": str(500000 + profit),
                "external_pool": "0",
                "settled_quantity": 0,
                "last_mark_price": None,
                "security_code": None,
            }
        ],
        "reconciliation": {
            "reconciled": True,
            "sealed_partition_access": False,
            "initial_equity": "500000",
            "ending_equity": str(500000 + profit),
            "unit_wealth": str(500000 + profit),
        },
    }


def test_advantage_over_only_one_control_does_not_grant_use():
    result = compare({"C0": payload(300), "C1": payload(-100), "C2": payload(100)})
    assert result["C2_minus_control"]["C0"]["net_return_delta"] < 0
    assert result["C2_minus_control"]["C1"]["net_return_delta"] > 0
    assert result["value_decision"] == "NO_PASS_FIXED_VALUE_GATE"
    assert result["consumer_admission"] == "NOT_GRANTED"


def test_one_issuer_and_one_session_cannot_supply_independent_value():
    arms = {"C0": payload(100), "C1": payload(200), "C2": payload(300)}
    first, second = compare(arms), compare(deepcopy(arms))
    assert first == second
    assert first["bootstrap"]["terminal_sessions_held_fixed"] == 1
    assert not first["C2_minus_control"]["C0"]["advantage_survives_each_issuer_and_session_removal"]
    assert first["value_decision"] == "NO_PASS_FIXED_VALUE_GATE"


def test_actual_ledger_and_session_mismatches_fail_closed():
    arms = {"C0": payload(100), "C1": payload(200), "C2": payload(300)}
    broken = deepcopy(arms)
    broken["C2"]["ledgers"][0]["cash"] = "999"
    with pytest.raises(ValueError, match="FINAL_LEDGER_MISMATCH"):
        compare(broken)
    broken = deepcopy(arms)
    broken["C2"]["equity"][1]["market_date"] = "2021-03-01"
    with pytest.raises(ValueError, match="UNPAIRED_SESSIONS"):
        compare(broken)


def test_cash_pool_settlement_is_neutral_to_issuer_attribution():
    row = payload(100)
    row["events"].append(
        {
            "event_type": "ledger_entry",
            "payload": {
                "security_code": None,
                "postings": [
                    {"account": "cash", "amount": "-100"},
                    {"account": "external_pool", "amount": "100"},
                ],
            },
        }
    )
    result = issuer_contributions(
        row["events"], row["ledgers"], initial=Decimal(500000), ending=Decimal(500100)
    )
    assert result == {"A": Decimal(100)}
    with pytest.raises(ValueError, match="WEALTH_NOT_RECONCILED"):
        issuer_contributions(
            row["events"], row["ledgers"], initial=Decimal(500000), ending=Decimal(500101)
        )
