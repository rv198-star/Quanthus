from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse import p10_action_support as s


def raw():
    return {
        "ASSIGN_PROGRESS": "实施分配",
        "SECURITY_CODE": "000001",
        "EX_DIVIDEND_DATE": "2026-08-03",
        "NOTICE_DATE": "2026-07-30",
        "IMPL_PLAN_PROFILE": "10派1.234元",
        "PRETAX_BONUS_RMB": Decimal("1.234"),
        "BONUS_RATIO": None,
        "IT_RATIO": Decimal("0"),
    }


def test_literal_units_preserve_original_nulls_and_precision():
    r = raw()
    before = deepcopy(r)
    p = s.literal_plan(r)
    assert p["components"][s.TERMS[0]] == Decimal("0.1234")
    assert p["null_raw_fields_preserved"] == ["BONUS_RATIO"]
    assert r == before and r["BONUS_RATIO"] is None


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"PRETAX_BONUS_RMB": Decimal("0.1234")}, "ACTION_TEXT_STRUCTURED_CONFLICT"),
        ({"PRETAX_BONUS_RMB": 1.234}, "ACTION_RAW_DECIMAL_REQUIRED"),
        ({"PRETAX_BONUS_RMB": None}, "ACTION_STRUCTURED_NONZERO_MISSING"),
        ({"ASSIGN_PROGRESS": "预案"}, "ACTION_NOT_IMPLEMENTED"),
        ({"IMPL_PLAN_PROFILE": "10派1.234元(含税差异)"}, "ACTION_PLAN_QUALIFIER"),
        ({"NOTICE_DATE": "2026-08-04"}, "ACTION_NOTICE_DATE"),
    ],
)
def test_raw_not_arbitrated_or_fitted(changes, reason):
    r = raw()
    r.update(changes)
    with pytest.raises(ValueError, match=reason):
        s.literal_plan(r)


def bound():
    a = {
        "record_source": "p10_retained_eastmoney_implementation",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "source_revision": "r1",
        "source_record_id": "source-row",
        "corporate_action_id": "action",
        "company_id": "company",
        "security_id": "security",
        "action_status": "IMPLEMENTED",
        "ex_date": date(2026, 8, 3),
        "effective_at": datetime.fromisoformat("2026-08-03T23:59:59+08:00"),
        "knowledge_from": datetime(2026, 10, 6, tzinfo=timezone.utc),
    }
    t = {
        **a,
        "term_type": s.TERMS[0],
        "option_id": None,
        "currency": "CNY",
        "amount_per_share": Decimal("0.1234"),
    }
    args = {
        "listing_code": "000001",
        "raw_file_sha256": a["source_artifact_sha256"],
        "raw_fetched_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "qualified_at": datetime(2026, 10, 23, tzinfo=timezone.utc),
    }
    return a, t, args


def test_native_exact_match_and_true_late_knowledge():
    a, t, args = bound()
    out = s.bind_plan(a, [t], raw(), **args)
    assert out["qualified_at"] == args["qualified_at"]
    assert not out["competing_events_arbitrated"] and not out["historical_pit_granted"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"amount_per_share": Decimal("1.234")}, "ACTION_RAW_NATIVE_MISMATCH"),
        ({"currency": "USD"}, "ACTION_CURRENCY"),
        ({"source_revision": "other"}, "ACTION_TERM_SOURCE_MISMATCH"),
    ],
)
def test_bind_rejects_wrong_unit_currency_or_version(change, reason):
    a, t, args = bound()
    t.update(change)
    with pytest.raises(ValueError, match=reason):
        s.bind_plan(a, [t], raw(), **args)


def test_backdating_or_wrong_listing_rejected():
    a, t, args = bound()
    args["listing_code"] = "600000"
    with pytest.raises(ValueError, match="ACTION_DATED_IDENTITY"):
        s.bind_plan(a, [t], raw(), **args)
    args["listing_code"] = "000001"
    args["qualified_at"] = args["raw_fetched_at"]
    with pytest.raises(ValueError, match="ACTION_TRUE_KNOWLEDGE"):
        s.bind_plan(a, [t], raw(), **args)


def test_standard_gross_tax_label_is_not_personal_tax_inference():
    r = raw()
    r["IMPL_PLAN_PROFILE"] += "(含税)"
    assert s.literal_plan(r)["components"][s.TERMS[0]] == Decimal("0.1234")


def test_standard_gross_with_net_annotation_does_not_use_net_amount():
    r = raw()
    r["IMPL_PLAN_PROFILE"] += "(含税,扣税后0.999元)"
    out = s.literal_plan(r)
    assert out["components"][s.TERMS[0]] == Decimal("0.1234")
    assert out["tax_dependent_net_cash_qualified"] is False


def test_new_economic_view_uses_exact_plan_not_provider_vote():
    import json
    from pathlib import Path

    policy = json.loads(Path("config/p1-corporate-action-normalization.v3.json").read_text())
    a, t, args = bound()
    proof = s.bind_plan(a, [t], raw(), **args)
    old = {**a, "corporate_action_id": "old", "record_source": "dividend_history"}
    ot = {
        **t,
        "corporate_action_id": "old",
        "record_source": "dividend_history",
        "amount_per_share": Decimal("1.234"),
    }
    before = deepcopy(ot)
    result = s.reconcile_event([a, old], {"action": [t], "old": [ot]}, [proof], policy=policy)
    assert result["cash_dividend_per_share"] == Decimal("0.1234")
    assert result["known_at"] == args["qualified_at"]
    assert ot == before and not result["native_history_replaced"]
    ot["amount_per_share"] = Decimal("1.334")
    with pytest.raises(ValueError, match="ACTION_PRESENT_COMPONENT_CONFLICT"):
        s.reconcile_event([a, old], {"action": [t], "old": [ot]}, [proof], policy=policy)
    ot["amount_per_share"] = Decimal("1.234")
    old["record_source"] = ot["record_source"] = "base22_f10/dividend_implemented"
    with pytest.raises(ValueError, match="ACTION_PRESENT_COMPONENT_CONFLICT"):
        s.reconcile_event([a, old], {"action": [t], "old": [ot]}, [proof], policy=policy)


def test_reconciliation_does_not_admit_forged_native_binding():
    import json
    from pathlib import Path

    policy = json.loads(Path("config/p1-corporate-action-normalization.v3.json").read_text())
    a, t, args = bound()
    proof = s.bind_plan(a, [t], raw(), **args)
    t["amount_per_share"] = Decimal("9")
    with pytest.raises(ValueError, match="ACTION_PROOF_NATIVE_BINDING"):
        s.reconcile_event([a], {"action": [t]}, [proof], policy=policy)


def test_partial_quantity_revision_retains_unchanged_cash_version_only():
    a, t, args = bound()
    a.update(
        record_source="dividend_history",
        source_revision="p10-base22-native-dividend-perten-ratio-revision-v1:sha256:" + "b" * 64,
    )
    t.update(
        record_source="dividend_history",
        source_revision="dwd-v2-1-gpu-p100-20260810T132353Z-3156b74e-r13",
        knowledge_from=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    before = deepcopy(t)
    assert s.native_components(a, [t], retained_versions=True)[s.TERMS[0]] == Decimal("0.1234")
    assert t == before
    with pytest.raises(ValueError, match="ACTION_TERM_SOURCE_MISMATCH"):
        s.native_components(a, [t])
    t["source_revision"] = "unrelated-version"
    with pytest.raises(ValueError, match="ACTION_TERM_SOURCE_MISMATCH"):
        s.native_components(a, [t], retained_versions=True)


def test_quantity_sum_keeps_more_than_default_context_digits():
    c = {
        s.TERMS[1]: Decimal("0.1234567890123456789012345678901234567"),
        s.TERMS[2]: Decimal("0.0000000000000000000000000000000000001"),
    }
    assert s._share_total(c) == Decimal("0.1234567890123456789012345678901234568")


def test_f10_full_implementation_text_replay_and_tamper():
    import hashlib

    a, t, args = bound()
    a["record_source"] = t["record_source"] = "base22_f10/dividend_implemented"
    content = (
        "【分红】\n｜2025-12-31｜10派1.234元(含税)｜\n"
        "｜｜进展说明:实施｜\n｜｜除权除息日:2026-08-03｜\n【其他】"
    )
    r = dict(
        category="分红扩股",
        content=content,
        stock_code="000001",
        security_id="000001",
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        acquired_at="2026-08-04T12:00:00Z",
    )
    kwargs = {k: v for k, v in args.items() if k != "raw_fetched_at"}
    p = s.bind_f10_plan(a, [t], r, **kwargs)
    assert p["components"][s.TERMS[0]] == Decimal("0.1234")
    assert p["components"][s.TERMS[1]] == 0 and s.TERMS[1] in p["absent_tokens_preserved"]
    r["content"] += "tamper"
    with pytest.raises(ValueError, match="F10_CONTENT_HASH"):
        s.bind_f10_plan(a, [t], r, **kwargs)
