from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base23_ownership as adapter
from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base22_holder_components_v2 import (
    load_component_contract,
)
from quanthus.pipelines.securities_warehouse.base23_ownership_audit import changed_binding_census
from tests.unit.test_base22_f10_ownership import resolve
from tests.unit.test_base23_ownership_reconfirmation import fixture

BODY = (
    "截至日期：2026-09-03 十大流通股东情况\n"
    "｜股东名称｜持股数(股)｜占流通股比(%)｜股份性质｜增减情况(股)｜\n"
    "｜合成股东｜1.234567万｜1.25%｜无限售A股｜不变｜\n└"
)


def project(content, category="股东研究"):
    import hashlib

    raw = fixture()
    raw.update(stock_code="002001", security_id="002001")
    raw.update(content=content, category=category, market_date="2026-09-05",
               acquired_at="2026-09-05T00:00:00+08:00",
               content_sha256="sha256:" + hashlib.sha256(content.encode()).hexdigest())
    return adapter.ownership_observation(
        raw, source_sha256="sha256:" + "a" * 64, revision="retained-release",
        resolver=resolve(), observed_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        component_contract=load_component_contract(Path("config/pipelines")),
    )


def test_september_dated_matrix_keeps_literal_columns_numbers_and_new_knowledge_time():
    rows, proof = project(BODY)
    row = rows[adapter.HOLDING][0]
    assert row["observation_start"].date().isoformat() == "2026-09-02"  # Shanghai midnight.
    assert row["share_nature"] == "无限售A股" and row["holder_category"] is None
    assert row["raw_holding_quantity"] == "1.234567万" and row["raw_holding_percentage"] == "1.25%"
    assert row["knowledge_from"] == datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert not proof["provider_PIT_qualified"] and not proof["native_history_changed"]


def test_equal_date_conflicting_complete_frames_do_not_arbitrarily_choose_the_first():
    rows, proof = project(BODY + "\n" + BODY.replace("1.234567万", "2万"))
    assert not rows[adapter.HOLDING]
    assert any("NO_SOURCE_PRIORITY" in f.get("reason", "") for f in proof["frames"])


def test_complete_identical_frames_deduplicate_without_dropping_original_frame_proof():
    rows, proof = project(BODY + "\n" + BODY)
    assert len(rows[adapter.HOLDING]) == 1 and len(proof["frames"]) == 2


def test_future_effective_claim_is_retained_without_inventing_implementation_or_availability():
    rows, proof = project(BODY.replace("2026-09-03", "2026-09-07"))
    assert rows[adapter.HOLDING]
    assert proof["future_capture_business_date_claims"]
    assert not proof["implementation_proof_from_future_date"]


def test_incomplete_frame_and_october_business_date_are_accounted_without_outputs():
    for content in (BODY.removesuffix("└"), BODY.replace("2026-09-03", "2026-10-01")):
        rows, proof = project(content)
        assert not rows[adapter.HOLDING]
        assert proof["frames"][0]["status"] == "HELD_NOT_EMPTY_COVERAGE"


def test_september_capital_matrix_preserves_exact_unit_and_original_quantity():
    body = "【1.股本结构】\n｜单位(股)｜2026-09-02｜\n｜总股本｜1.234567万｜\n【2.股本变动】"
    rows, proof = project(body, "股本结构")
    row = rows[adapter.CAPITAL][0]
    assert row["share_quantity"] == Decimal("12345.67") and row["raw_value"] == "1.234567万"
    assert not proof["held"] and not proof["provider_PIT_qualified"]


def test_conflicting_capital_columns_for_one_business_key_are_both_held():
    body = ("【1.股本结构】\n｜单位(股)｜2026-09-02｜2026-09-02｜\n"
            "｜总股本｜1万｜2万｜\n【2.股本变动】")
    rows, proof = project(body, "股本结构")
    assert not rows[adapter.CAPITAL]
    assert any("NO_SOURCE_PRIORITY" in item["reason"] for item in proof["held"])


def test_complete_original_binding_census_selects_latest_and_rejects_forged_counts(tmp_path):
    a = dict(identity_key=["tdx", "002001", "股东研究"], member_sha256="sha256:" + "a" * 64,
             ordinal=0, actual_acquisition_instant="2026-09-01T00:00:00Z",
             literal_scope_fingerprint="sha256:" + "c" * 64, prior_literal_scope_match=True)
    b = dict(a, member_sha256="sha256:" + "b" * 64,
             actual_acquisition_instant="2026-09-05T00:00:00Z", prior_literal_scope_match=False)
    path = tmp_path / "proofs.jsonl"
    path.write_bytes(canonical(a) + b"\n" + canonical(b) + b"\n")
    comparison = dict(complete_incoming_proof={"sha256": file_sha(path)},
                      incoming_complete_capture_counts={"BASELINE_IDENTICAL_LITERAL_SCOPE": 1,
                                                        "CHANGED_LITERAL_SCOPE": 1},
                      latest_identity_keys=1, latest_changed_scopes=1)
    assert changed_binding_census(comparison, path) == {(b["member_sha256"], 0)}
    comparison["latest_changed_scopes"] = 0
    with pytest.raises(RuntimeError, match="COMPLETE_SELECTION_CHANGED"):
        changed_binding_census(comparison, path)
    path.write_bytes(canonical(a) + b"\n" + canonical(a) + b"\n")
    comparison["complete_incoming_proof"]["sha256"] = file_sha(path)
    with pytest.raises(RuntimeError, match="DUPLICATE_CAPTURE"):
        changed_binding_census(comparison, path)
