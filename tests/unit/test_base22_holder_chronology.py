"""A recorded revision needs actual complete raw chronology, not a newer package."""

from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_chronology import (
    apply_retained_chronology,
    qualify_retained_chronology,
)
from quanthus.pipelines.securities_warehouse.base22_holder_column_continuation import (
    holder_observation,
)
from quanthus.pipelines.securities_warehouse.base22_holder_column_revision import (
    qualify_column_restorations,
)
from tests.unit.test_base22_f10_ownership import raw
from tests.unit.test_base22_holder_column_revision import BODY, case


def setup():
    args = case()
    args.pop("actual_weekly_acquisitions")
    later = []
    rows = []
    for day, suffix in [(1, "b"), (8, "c")]:
        item = raw(BODY if day == 1 else BODY.replace("普通A", "境内普通A"), "股东研究")
        item["acquired_at"] = f"2026-08-{day:02d}T08:02:00+08:00"
        sha = "sha256:" + suffix * 64
        later.append((sha, item))
        rows += holder_observation(item, source_sha256=sha, resolver=args["resolver"])[0][
            "dwd_shareholding_observation"
        ]
    args["weekly"], args["later_raw"] = rows, later
    return args


def test_later_body_revision_has_a_new_true_governance_version_with_all_raw_history():
    args = setup()
    before = dict(args["native"][0])
    result = qualify_retained_chronology(**args)
    item = next(iter(result["revisions"].values()))
    assert result["held"] == {}
    assert args["native"][0] == before
    old, new = item["output"]
    assert old == {**before, "knowledge_to": args["observed_at"]}
    assert new["share_nature"] == "境内普通A股"
    assert new["knowledge_from"] == args["observed_at"]
    assert item["latest_actual_acquisition"] == datetime(2026, 8, 8, 0, 2, tzinfo=timezone.utc)
    assert len(item["retained_source_observation_sha256s"]) == 2
    assert result["historical_PIT_admission"] is False
    assert result["different_provider_priority_granted"] is False
    frozen_args = {k: v for k, v in args.items() if k != "later_raw"}
    frozen_args["actual_weekly_acquisitions"] = {
        (r["source_artifact_sha256"], r["source_record_id"]): datetime.fromisoformat(
            raw["acquired_at"]
        )
        for r, (_, raw) in zip(args["weekly"], args["later_raw"], strict=True)
    }
    assert not qualify_column_restorations(**frozen_args)["revisions"]


def test_numeric_percentage_revision_is_a_literal_same_provider_source_not_a_guessed_priority():
    args = setup()
    sha, item = args["later_raw"][-1]
    updated = raw(item["content"].replace("1.25%", "1.234567890123%"), "股东研究")
    updated["acquired_at"] = item["acquired_at"]
    args["later_raw"][-1] = (sha, updated)
    args["weekly"][-1] = holder_observation(updated, source_sha256=sha, resolver=args["resolver"])[
        0
    ]["dwd_shareholding_observation"][0]
    result = qualify_retained_chronology(**args)
    new = next(iter(result["revisions"].values()))["output"][-1]
    assert new["holding_percentage"] == args["weekly"][-1]["holding_percentage"]
    assert new["raw_holding_percentage"] == args["weekly"][-1]["raw_holding_percentage"]
    assert new["holding_quantity"] == args["native"][0]["holding_quantity"]


def test_quantity_revision_is_exact_literal_same_provider_and_old_atom_is_retained():
    args = setup()
    sha, item = args["later_raw"][-1]
    updated = raw(item["content"].replace("1.234567万", "2.345678万"), "股东研究")
    updated["acquired_at"] = item["acquired_at"]
    args["later_raw"][-1] = (sha, updated)
    args["weekly"][-1] = holder_observation(updated, source_sha256=sha, resolver=args["resolver"])[
        0
    ]["dwd_shareholding_observation"][0]
    revision = next(iter(qualify_retained_chronology(**args)["revisions"].values()))
    assert revision["output"][0]["holding_quantity"] == args["native"][0]["holding_quantity"]
    assert revision["output"][1]["holding_quantity"] == args["weekly"][-1]["holding_quantity"]
    assert {"holding_quantity", "raw_holding_quantity"} <= set(revision["changed_fields"])


def test_latest_raw_reconfirmation_does_not_admit_intervening_raw_as_old_PIT():
    from quanthus.pipelines.securities_warehouse.base22_holder_literals import (
        holder_observation as frozen,
    )

    args = setup()
    text = BODY.replace("普通A", "普通A股").replace("｜ ｜ ｜ ｜股｜ ｜\n", "")
    original = raw(text, "股东研究")
    original["acquired_at"] = args["original_raw"]["acquired_at"]
    args["original_raw"] = original
    args["native"] = frozen(
        original, source_sha256=args["original_member_sha256"], resolver=args["resolver"]
    )[0]["dwd_shareholding_observation"]
    args["weekly"] = []
    for n, (sha, item) in enumerate(args["later_raw"]):
        updated = raw(text if n else text.replace("普通A股", "境内普通A股"), "股东研究")
        updated["acquired_at"] = item["acquired_at"]
        args["later_raw"][n] = (sha, updated)
        args["weekly"] += holder_observation(updated, source_sha256=sha, resolver=args["resolver"])[
            0
        ]["dwd_shareholding_observation"]
    revision = next(iter(qualify_retained_chronology(**args)["revisions"].values()))
    assert revision["changed_fields"] == []
    assert revision["latest_source_reconfirms_native_body"] is True
    assert len(revision["output"]) == 2


@pytest.mark.parametrize(
    "defect",
    [
        "native-body",
        "native-source",
        "raw-content-hash",
        "provider",
        "code",
        "unknown-acquisition",
        "package-clock-only",
        "governance-clock",
        "missing-witness",
        "changed-witness",
        "duplicate-witness",
        "missing-original-header",
        "unapproved-holder-name-change",
        "same-clock-conflict",
    ],
)
def test_missing_or_conflicting_source_closure_does_not_grant_a_repair(defect):
    args = setup()
    if defect == "native-body":
        args["native"][0]["holding_quantity"] += 1
    elif defect == "native-source":
        args["native"][0]["source_artifact_sha256"] = "sha256:" + "d" * 64
    elif defect == "raw-content-hash":
        args["original_raw"]["content_sha256"] = "sha256:" + "e" * 64
    elif defect == "provider":
        args["later_raw"][-1][1]["source"] = "another-provider"
    elif defect == "code":
        args["later_raw"][-1][1]["stock_code"] = "002002"
    elif defect in ("unknown-acquisition", "package-clock-only"):
        args["later_raw"][-1][1]["acquired_at"] = None
        args["later_raw"][-1][1]["built_at"] = args["observed_at"]
    elif defect == "governance-clock":
        args["observed_at"] = datetime(2026, 8, 1, tzinfo=timezone.utc)
    elif defect == "missing-witness":
        args["weekly"].pop()
    elif defect == "changed-witness":
        args["weekly"][-1]["share_nature"] = "unproved"
    elif defect == "duplicate-witness":
        args["weekly"].append(args["weekly"][-1])
    elif defect == "missing-original-header":
        args["original_raw"] = raw(BODY.replace("股份性质", "未知列"), "股东研究")
    elif defect == "unapproved-holder-name-change":
        sha, item = args["later_raw"][-1]
        updated = raw(item["content"].replace("合成", "另一"), "股东研究")
        updated["acquired_at"] = item["acquired_at"]
        args["later_raw"][-1] = (sha, updated)
        args["weekly"][-1] = holder_observation(
            updated, source_sha256=sha, resolver=args["resolver"]
        )[0]["dwd_shareholding_observation"][0]
    else:
        args["later_raw"][-1][1]["acquired_at"] = args["later_raw"][0][1]["acquired_at"]
        args["weekly"][-1] = holder_observation(
            args["later_raw"][-1][1],
            source_sha256=args["later_raw"][-1][0],
            resolver=args["resolver"],
        )[0]["dwd_shareholding_observation"][0]
    try:
        result = qualify_retained_chronology(**args)
    except BoundedError:
        return
    assert result["revisions"] == {} and result["held"]


@pytest.mark.parametrize("defect", [None, "missing", "duplicate", "changed", "proof"])
def test_application_requires_exact_complete_key_version_and_observation_census(defect):
    args = setup()
    proof = next(iter(qualify_retained_chronology(**args)["revisions"].values()))
    rows = [*args["native"], *args["weekly"]]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(rows[-1])
    elif defect == "changed":
        rows[-1] = {**rows[-1], "holding_percentage": rows[-1]["holding_percentage"] + 1}
    elif defect == "proof":
        proof["changed_fields"] = []
    if defect is None:
        assert apply_retained_chronology(rows, args["rule"], proof) == proof["output"]
    else:
        with pytest.raises(BoundedError, match="COMPLETE_QUALIFIED_GROUP_CHANGED"):
            apply_retained_chronology(rows, args["rule"], proof)
