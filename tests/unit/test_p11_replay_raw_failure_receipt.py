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


def test_bad_signed_catalogue_is_refused_only_for_its_own_coverage():
    from tests.unit import test_p11_paper_replay as f

    day = f.T1
    days = tuple(d for d in f.SESSION_DAYS if d <= day)
    stocks = [f._security(code, days) for code in ("600000", "002040")]
    req = f._request(market_dates=(day,), securities=stocks)
    req["listing_rows"].append(f._listing("security-002040"))
    coverage = req["action_coverage"]
    cid = "b10:listing_e30521d51b0e506341403f5a70da7a1d:2026-10-08"
    coverage["registers"].append({**coverage["registers"][0], "coverage_id": cid})
    for record in coverage["window_evidence"]:
        if record["security_code"] == "002040":
            record["coverage_id"] = cid
    coverage["verified_artifacts"] = [
        {
            "coverage_id": cid,
            "digest": "sha256:" + "9" * 64,
            "producer": "p10.signed-action-members.v1",
            "authority": coverage["window_evidence"][0]["credential"],
            "rows": [
                {
                    "register_row": 302,
                    "ex_date": "2026-07-28",
                    "notice": "1225435299",
                    "cash_gross_per_share": "0.116",
                    "bonus_share_ratio": "0",
                    "transfer_share_ratio": "0",
                    "currency": "CNY",
                    "implementation_sha256": "sha256:" + "a" * 64,
                    "security_id": "None",
                    "listing_id": "None",
                    "venue_id": "None",
                }
            ],
        }
    ]
    receipt = e_run_e1_replay(req, day, load_e1_rule_assets(ROOT))
    (rejected,) = receipt["coverage_exclusions"]
    assert rejected["security_code"] == "002040"
    assert rejected["reason"] == "action_artifact_invalid_identity"
    assert rejected["action_artifact_rejections"] == [
        {
            "reason": "action_artifact_invalid_identity",
            "coverage_id": cid,
            "rows": [{"register_row": 302, "fields": ["security_id", "listing_id", "venue_id"]}],
        }
    ]
    assert receipt["delivered"]["600000"]["entry_eligible"]
    assert receipt["publishable"]
