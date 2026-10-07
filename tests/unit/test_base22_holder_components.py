"""A complete source matrix grants components, never an inferred aggregate/rank."""

from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_components import (
    TABLE,
    check_component_replay,
    component_key,
    load_component_contract,
    project_holder_components,
)
from tests.unit.test_base22_f10_ownership import raw, resolve

CONFIG = Path("config/pipelines")
BODY = (
    "截至日期：2026-06-30 十大股东情况\n"
    "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
    "｜合成股东｜1.234567万｜1.123456789012%｜无限售A股｜未变｜\n"
    "｜｜2.345678万｜2.234567890123%｜限售A股｜未变｜\n"
    "｜另一个股东｜3万｜3%｜无限售A股｜新进｜\n└"
)


def project(body=BODY):
    return project_holder_components(
        raw(body, "股东研究"),
        source_sha256="sha256:" + "a" * 64,
        resolver=resolve(),
        contract=load_component_contract(CONFIG),
    )


def test_complete_group_has_two_literal_components_same_rank_and_distinct_candidate_keys():
    rows, proof = project()
    values = rows[TABLE]
    assert [r["holder_rank"] for r in values] == [1, 1, 2]
    assert [r["shareholder_source_name"] for r in values] == ["合成股东", "合成股东", "另一个股东"]
    assert [r["holding_quantity"] for r in values] == [
        Decimal("12345.67"),
        Decimal("23456.78"),
        Decimal("30000"),
    ]
    assert values[1]["holding_percentage"] == Decimal("2.234567890123")
    assert len({component_key(r) for r in values}) == 3
    assert proof["frames"][0]["census"]["blank_name_components"] == 1
    assert proof["historical_PIT_admission"] is False
    assert proof["frames"][0]["census"]["increment_comments"]["qualification"] == "NOT_GRANTED"


def test_native_key_and_original_profile_stay_frozen():
    from quanthus.pipelines.securities_warehouse.base22_holder_column_continuation import (
        holder_observation,
    )

    native, _, _ = load_contract(CONFIG)
    assert "share_nature" not in native["tables"][TABLE]["business_key"]
    with pytest.raises(BoundedError, match="UNACCOUNTED"):
        holder_observation(
            raw(BODY, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve()
        )


@pytest.mark.parametrize("basis", ["占A股", "占B股", "占H股"])
def test_literal_percentage_denominator_is_preserved_not_converted(basis):
    rows, proof = project(BODY.replace("1.123456789012%", "1.123456789012 " + basis))
    assert rows[TABLE][0]["raw_holding_percentage"] == "1.123456789012 " + basis
    assert rows[TABLE][0]["holding_percentage"] == Decimal("1.123456789012")
    assert proof["percentage_basis_counts"][basis] == 1


@pytest.mark.parametrize(
    "bad",
    [
        "｜｜2.345678万｜2%｜无限售A股｜未变｜",
        "｜｜2.345678万｜2%｜未知股｜未变｜",
        "｜｜｜2%｜限售A股｜未变｜",
        "｜｜2美元｜2%｜限售A股｜未变｜",
        "｜孤立名字｜-｜2%｜限售A股｜未变｜",
    ],
)
def test_unknown_duplicate_or_nonliteral_component_is_explicitly_held(bad):
    rows, proof = project(BODY.replace("｜｜2.345678万｜2.234567890123%｜限售A股｜未变｜", bad))
    assert rows[TABLE] == []
    assert proof["frames"][0]["status"] == "HELD_NOT_EMPTY_COVERAGE"
    assert proof["whole_category"] == "HELD"


def test_orphan_component_and_cross_date_name_carry_are_not_allowed():
    rows, proof = project(
        BODY.replace("｜合成股东｜1.234567万｜1.123456789012%｜无限售A股｜未变｜\n", "")
    )
    assert rows[TABLE] == []
    assert proof["frames"][0]["reason"] == "HOLDER_COMPONENT_EMPTY_NAME_SCOPE_UNPROVEN"
    rows, proof = project(
        BODY
        + "\n"
        + BODY.replace("2026-06-30", "2026-03-31").replace(
            "｜合成股东｜1.234567万｜1.123456789012%｜无限售A股｜未变｜\n", ""
        )
    )
    assert len(rows[TABLE]) == 3
    assert proof["frames"][1]["status"] == "HELD_NOT_EMPTY_COVERAGE"


def test_text_wrap_names_every_component_without_inventing_another_holder():
    rows, _ = project(BODY.replace("｜另一个股东｜", "｜延续名字｜｜｜｜｜\n｜另一个股东｜"))
    assert [r["shareholder_source_name"] for r in rows[TABLE]][:2] == ["合成股东延续名字"] * 2
    assert [r["holder_rank"] for r in rows[TABLE]] == [1, 1, 2]


def test_partial_tail_cannot_be_granted_but_previous_complete_matrix_is_accounted():
    rows, proof = project(BODY + "\n" + BODY.replace("2026-06-30", "2026-03-31").removesuffix("└"))
    assert len(rows[TABLE]) == 3
    assert len(proof["frames"]) == 2
    assert proof["frames"][1]["reason"] == "HOLDER_COMPONENT_MATRIX_INCOMPLETE"
    assert proof["whole_category"] == "HELD"


def test_floating_category_does_not_inherit_a_blank_numeric_share_class():
    rows, proof = project(BODY.replace("十大股东情况", "十大流通股东情况"))
    assert rows[TABLE] == []
    assert proof["frames"][0]["reason"] == "HOLDER_COMPONENT_EMPTY_NAME_SCOPE_UNPROVEN"


def test_same_date_duplicate_global_key_is_held_not_deduplicated_without_a_rule():
    rows, proof = project(BODY + "\n" + BODY)
    assert len(rows[TABLE]) == 3
    assert proof["frames"][1]["reason"] == "HOLDER_COMPONENT_DUPLICATE_GLOBAL_KEY"


@pytest.mark.parametrize("bad", ["1.2345678901234%", "101%", "1.2 占未知股"])
def test_precision_or_unknown_denominator_never_rounds_to_fit(bad):
    rows, proof = project(BODY.replace("1.123456789012%", bad))
    assert rows[TABLE] == []
    assert proof["whole_category"] == "HELD"


def test_original_hash_and_acquisition_are_required_not_package_time():
    for field in ["content_sha256", "acquired_at"]:
        r = raw(BODY, "股东研究")
        r[field] = None
        with pytest.raises((BoundedError, RuntimeError)):
            project_holder_components(
                r,
                source_sha256="sha256:" + "a" * 64,
                resolver=resolve(),
                contract=load_component_contract(CONFIG),
            )


def test_offline_replay_binds_frame_disposition_and_typed_values_not_just_count():
    _, proof = project()
    check_component_replay(proof, proof)
    changed = {**proof, "actual_acquisition": None}
    with pytest.raises(BoundedError, match="REPLAY_CHANGED"):
        check_component_replay(proof, changed)


def test_contract_changes_cannot_silently_replace_the_explicit_grain(tmp_path):
    import json
    import shutil

    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
        "base22-holder-component-grain.v1.json",
    ]:
        shutil.copyfile(CONFIG / name, tmp_path / name)
    path = tmp_path / "base22-holder-component-grain.v1.json"
    rule = json.loads(path.read_bytes())
    rule["business_key"].remove("share_nature")
    path.write_text(json.dumps(rule))
    with pytest.raises(BoundedError, match="CONTRACT_INVALID"):
        load_component_contract(tmp_path)


def group_case():
    from datetime import datetime, timezone

    from quanthus.pipelines.securities_warehouse.base22_holder_chronology import _native_origin

    original = raw(
        BODY.replace("｜｜2.345678万｜2.234567890123%｜限售A股｜未变｜\n", ""), "股东研究"
    )
    original["acquired_at"] = "2026-07-31T19:00:00+08:00"
    sha = "sha256:" + "a" * 64
    before = next(iter(_native_origin(original, sha, resolve()).values()))
    later = raw(BODY.replace("｜限售A股｜未变", "｜限售B股｜未变"), "股东研究")
    return {
        "before": [before],
        "original_raw": original,
        "original_member_sha256": sha,
        "later_raw": [("sha256:" + "b" * 64, later)],
        "resolver": resolve(),
        "contract": load_component_contract(CONFIG),
        "observed_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
    }


def test_complete_group_key_move_keeps_old_atom_and_real_acquisition_without_fake_PIT():
    from quanthus.pipelines.securities_warehouse.base22_holder_components import (
        qualify_component_group,
    )

    args = group_case()
    result = qualify_component_group(**args)
    old, first, second = result["output"]
    assert old == {**args["before"][0], "knowledge_to": args["observed_at"]}
    assert first["holder_rank"] == second["holder_rank"] == 1
    assert first["share_nature"] == "无限售A股" and second["share_nature"] == "限售B股"
    assert first["knowledge_from"] == second["knowledge_from"] == args["observed_at"]
    assert result["input_closure"]["latest_actual_acquisition"].month == 8
    assert result["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "defect", ["native_value", "missing_holder", "same_clock", "provider", "target_partial"]
)
def test_group_rewrite_requires_exact_before_complete_target_and_actual_source_chronology(defect):
    from quanthus.pipelines.securities_warehouse.base22_holder_components import (
        qualify_component_group,
    )

    args = group_case()
    sha, later = args["later_raw"][0]
    if defect == "native_value":
        args["before"][0]["holding_quantity"] += Decimal(1)
    elif defect in {"missing_holder", "target_partial"}:
        body = (
            later["content"].replace("合成股东", "不同股东")
            if defect == "missing_holder"
            else later["content"].removesuffix("└")
        )
        changed = raw(body, "股东研究")
        args["later_raw"][0] = (sha, changed)
    elif defect == "same_clock":
        other = raw(BODY, "股东研究")
        args["later_raw"].append(("sha256:" + "c" * 64, other))
    else:
        later["source"] = "another_provider"
    with pytest.raises(BoundedError):
        qualify_component_group(**args)
