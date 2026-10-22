from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_column_continuation import (
    holder_observation,
)
from quanthus.pipelines.securities_warehouse.base22_holder_column_revision import (
    apply_column_restoration,
    qualify_column_restorations,
)
from quanthus.pipelines.securities_warehouse.base22_holder_literals import (
    holder_observation as frozen,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
)
from tests.unit.test_base22_f10_ownership import raw

BODY = (
    "截至日期：2026-06-30 十大股东情况\n"
    "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
    "｜合成股东｜1.234567万｜1.25%｜普通A｜不变｜\n｜ ｜ ｜ ｜股｜ ｜\n└"
)


def case():
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base22 import load_contract

    c, _, _ = load_contract(Path("config/pipelines"))
    rule = c["tables"]["dwd_shareholding_observation"]
    resolver = FinancialCompanyResolver(
        [
            FinancialCompanyIdentity(
                "002001",
                "security",
                "company",
                datetime(2010, 1, 1, tzinfo=timezone.utc),
                None,
                datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc),
            )
        ]
    )
    original = raw(BODY, "股东研究")
    original["acquired_at"] = "2026-07-31T21:35:18+08:00"
    sha = "sha256:" + "a" * 64
    before_raw = raw(BODY.replace("｜ ｜ ｜ ｜股｜ ｜\n", ""), "股东研究")
    before_raw["acquired_at"] = original["acquired_at"]
    before = frozen(before_raw, source_sha256=sha, resolver=resolver)[0][
        "dwd_shareholding_observation"
    ][0]
    before["source_record_id"] = (
        "stock_code=002001&category=股东研究&content_sha256=" + original["content_sha256"]
    )
    weekly = []
    clocks = {}
    for day, suffix in [(1, "b"), (8, "c")]:
        item = raw(BODY, "股东研究")
        item["acquired_at"] = f"2026-08-{day:02d}T08:02:00+08:00"
        source_sha = "sha256:" + suffix * 64
        row = holder_observation(item, source_sha256=source_sha, resolver=resolver)[0][
            "dwd_shareholding_observation"
        ][0]
        weekly.append(row)
        clocks[(source_sha, row["source_record_id"])] = datetime.fromisoformat(item["acquired_at"])
    return {
        "native": [before],
        "weekly": weekly,
        "original_raw": original,
        "original_member_sha256": sha,
        "actual_weekly_acquisitions": clocks,
        "resolver": resolver,
        "rule": rule,
        "observed_at": datetime(2026, 10, 7, 13, 40, tzinfo=timezone.utc),
    }


def test_restoration_requires_original_native_reproduction_and_two_true_witnesses():
    args = case()
    before = dict(args["native"][0])
    proof = qualify_column_restorations(**args)
    assert len(proof["revisions"]) == 1 and proof["held"] == {}
    item = next(iter(proof["revisions"].values()))
    old, new = item["output"]
    assert args["native"][0] == before
    assert old == {**before, "knowledge_to": args["observed_at"]}
    assert new["share_nature"] == "普通A股"
    assert new["knowledge_from"] == args["observed_at"]
    for field in [
        "holding_quantity",
        "holding_percentage",
        "raw_holding_quantity",
        "raw_holding_percentage",
        "observation_start",
    ]:
        assert old[field] == new[field]
    assert proof["historical_PIT_admission"] is False
    assert proof["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "defect",
    [
        "native-body",
        "native-member",
        "original-hash",
        "missing-acquisition",
        "unknown-acquisition",
        "later-numeric",
        "later-class",
        "no-weekly",
        "governance-clock",
        "unknown-original-header",
    ],
)
def test_unproved_origin_time_or_numeric_revision_stays_unqualified(defect):
    args = case()
    if defect == "native-body":
        args["native"][0]["holding_quantity"] += 1
    elif defect == "native-member":
        args["native"][0]["source_artifact_sha256"] = "sha256:" + "d" * 64
    elif defect == "original-hash":
        args["original_raw"]["content_sha256"] = "sha256:" + "e" * 64
    elif defect == "missing-acquisition":
        args["actual_weekly_acquisitions"] = {}
    elif defect == "unknown-acquisition":
        args["original_raw"]["acquired_at"] = None
    elif defect == "later-numeric":
        args["weekly"][-1]["holding_percentage"] += 1
    elif defect == "later-class":
        args["weekly"][-1]["share_nature"] = "另一个类别"
    elif defect == "no-weekly":
        args["weekly"] = []
    elif defect == "governance-clock":
        args["observed_at"] = datetime(2026, 8, 1, tzinfo=timezone.utc)
    elif defect == "unknown-original-header":
        from tests.unit.test_base22_f10_ownership import raw as make_raw

        args["original_raw"] = make_raw(BODY.replace("股份性质", "未知列"), "股东研究")
        args["original_raw"]["acquired_at"] = "2026-07-31T21:35:18+08:00"
    try:
        proof = qualify_column_restorations(**args)
    except RuntimeError:
        return
    assert not proof["revisions"] and proof["held"]


def test_a_partial_hold_does_not_grant_other_keys_or_change_frozen_scope():
    args = case()
    original_before = dict(args["native"][0])
    args["weekly"][-1]["share_nature"] = "冲突类别"
    proof = qualify_column_restorations(**args)
    assert proof["revisions"] == {} and len(proof["held"]) == 1
    assert args["native"][0] == original_before


@pytest.mark.parametrize("defect", [None, "missing", "duplicate", "changed", "proof"])
def test_exact_complete_group_is_required_at_application(defect):
    args = case()
    proof = next(iter(qualify_column_restorations(**args)["revisions"].values()))
    rows = [*args["native"], *args["weekly"]]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(rows[-1])
    elif defect == "changed":
        rows[-1] = {**rows[-1], "holding_quantity": rows[-1]["holding_quantity"] + 1}
    elif defect == "proof":
        proof["changed_fields"] = []
    if defect is None:
        assert apply_column_restoration(rows, args["rule"], proof) == proof["output"]
    else:
        with pytest.raises(RuntimeError, match="COMPLETE_QUALIFIED_GROUP_CHANGED"):
            apply_column_restoration(rows, args["rule"], proof)
