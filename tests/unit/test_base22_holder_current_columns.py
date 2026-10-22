"""An exact current raw row may repair columns without rolling back later values."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract
from quanthus.pipelines.securities_warehouse.base22_holder_chronology import _native_origin
from quanthus.pipelines.securities_warehouse.base22_holder_components_v2 import (
    load_component_contract,
)
from quanthus.pipelines.securities_warehouse.base22_holder_current_columns import (
    qualify_current_columns,
)
from tests.unit.test_base22_f10_ownership import raw, resolve

BODY = (
    "截至日期：2026-06-30 十大流通股东情况\n"
    "｜股东名称｜持股数(股)｜占流通股比(%)｜股份性质｜增减情况(股)｜\n"
    "｜合成股东｜1.234567万｜1.25%｜无限售A股｜不变｜\n└"
)


def case():
    original = raw(BODY, "股东研究")
    sha = "sha256:" + "a" * 64
    resolver = resolve()
    current = list(_native_origin(original, sha, resolver).values())
    native, _, _ = load_contract(Path("config/pipelines"))
    return {
        "current": current,
        "original_raw": original,
        "original_member_sha256": sha,
        "resolver": resolver,
        "component_contract": load_component_contract(Path("config/pipelines")),
        "rule": native["tables"]["dwd_shareholding_observation"],
        "observed_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
    }


def test_exact_current_columns_move_literal_header_value_and_preserve_every_number():
    args = case()
    before = dict(args["current"][0])
    result = qualify_current_columns(**args)
    assert result["held"] == {}
    assert len(result["revisions"]) == 1
    revision = next(iter(result["revisions"].values()))
    closed, new = revision["output"]
    assert closed == {**before, "knowledge_to": args["observed_at"]}
    assert new["share_nature"] == "无限售A股" and new["holder_category"] is None
    for field in (
        "holding_quantity",
        "holding_percentage",
        "raw_holding_quantity",
        "raw_holding_percentage",
        "holding_percentage_unit",
        "observation_start",
    ):
        assert new[field] == before[field]
    assert before == args["current"][0]
    assert result["provider_priority_granted"] is False
    assert result["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "quantity",
        "raw-percentage",
        "source-member",
        "different-provider",
        "native-clock",
        "closed-current",
        "duplicate-current",
        "raw-hash",
        "unknown-acquisition",
        "governance-clock",
        "truncated",
        "plural-components",
    ],
)
def test_missing_origin_or_multiple_components_never_grants_a_current_repair(defect):
    args = case()
    before = args["current"][0]
    if defect == "quantity":
        before["holding_quantity"] += 1
    elif defect == "raw-percentage":
        before["raw_holding_percentage"] = "1.250%"
    elif defect == "source-member":
        before["source_artifact_sha256"] = "sha256:" + "b" * 64
    elif defect == "different-provider":
        before["record_source"] = "different-provider"
    elif defect == "native-clock":
        before["knowledge_from"] = args["observed_at"]
    elif defect == "closed-current":
        before["knowledge_to"] = args["observed_at"]
    elif defect == "duplicate-current":
        args["current"].append(dict(before))
    elif defect == "raw-hash":
        args["original_raw"]["content_sha256"] = "sha256:" + "c" * 64
    elif defect == "unknown-acquisition":
        args["original_raw"]["acquired_at"] = None
    elif defect == "governance-clock":
        args["observed_at"] = before["knowledge_from"]
    else:
        text = (
            BODY.removesuffix("└")
            if defect == "truncated"
            else BODY.replace("1.25%", "1.25 占A股")
            .replace("无限售A股", "机构")
            .replace("股份性质", "股东性质")
            .replace("\n└", "\n｜｜2万｜2.5 占B股｜机构｜不变｜\n└")
        )
        args["original_raw"] = raw(text, "股东研究")
        args["current"] = list(
            _native_origin(
                args["original_raw"], args["original_member_sha256"], args["resolver"]
            ).values()
        )
    try:
        result = qualify_current_columns(**args)
    except (RuntimeError, ValueError):
        return
    assert result["held"] and not result["revisions"]
