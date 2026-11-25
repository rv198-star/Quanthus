"""The public replay receipt retains each rejection when the day cannot run."""

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation import daily_runner
from tests.unit import test_p11_paper_replay as fixture
from tests.unit.test_p11_e1_runtime import ROOT, e_run_e1_replay, load_e1_rule_assets


def test_empty_day_reports_each_original_failure(monkeypatch):
    assets = load_e1_rule_assets(ROOT)
    day = fixture.T1
    days = tuple(d for d in fixture.SESSION_DAYS if d <= day)
    stocks = [fixture._security(code, days) for code in ("600000", "000002")]
    request = fixture._request(market_dates=(day,), securities=stocks)
    request["listing_rows"].append(fixture._listing("security-000002"))
    stocks[1]["close_at_by_date"][day.isoformat()] = None

    def missing_dependency(**kwargs):
        raise ProductDailyError(
            "feature_adapter_dependency", "duckdb is required for the feature panel"
        )

    monkeypatch.setattr(daily_runner, "compute_security_feature_facts", missing_dependency)
    receipt = e_run_e1_replay(request, day, assets)
    assert receipt["disposition"] == "blocked"
    assert not receipt.get("package")
    block = receipt["day_block"]
    assert block["reason"] == "missing_raw_inputs"
    errors = {row["security_code"]: row for row in block["input_exclusions"]}
    assert errors["600000"]["reason"] == errors["000002"]["reason"] == "raw_input_closure_invalid"
    assert "feature_adapter_dependency" in errors["600000"]["member_or_field"]
    assert "missing_close_at" in errors["000002"]["member_or_field"]
