"""P10 successor evidence never changes the frozen qualification default."""

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base24_execution_evidence as repair
from quanthus.pipelines.securities_warehouse.base24_p6_purpose import _state_row
from tests.unit.test_base24_p6_purpose import market_row, state


def evidence():
    known = datetime(2026, 10, 14, tzinfo=timezone.utc)
    target = date(2026, 9, 30)
    listing = dict(
        listing_id="l",
        code="600001",
        board="SSE_main",
        venue="XSHG",
        effective_from=date(1999, 1, 1),
        effective_to=None,
        versions=1,
        knowledge_from=known,
    )
    sessions = [
        dict(
            day=target - timedelta(days=i),
            listing_id="l",
            code="600001",
            venue="XSHG",
            calendar_versions=1,
            listing_versions=1,
            is_open=True,
            trading=True,
            source_sha256="sha256:" + "a" * 64,
            knowledge_from=known,
        )
        for i in range(6)
    ]
    return listing, sessions, target, known + timedelta(days=1)


def rule():
    return repair.SseRule(repair.SSE_RULE_SHA, datetime(2026, 10, 14, tzinfo=timezone.utc))


def test_six_exact_observations_prove_only_current_window_exit():
    listing, sessions, target, cutoff = evidence()
    got = repair.exclude_ipo_window(rule(), listing, sessions, target, cutoff)
    assert got["in_ipo_window"] is False
    assert got["original_ipo_rule_recovered"] is False
    assert got["historical_pit_granted"] is False
    assert got["observed_session_count"] == 6


@pytest.mark.parametrize(
    "change",
    [
        "five",
        "duplicate",
        "wrong_venue",
        "wrong_listing",
        "future",
        "suspended",
        "ambiguous",
        "before_listing",
    ],
)
def test_calendar_count_or_listing_age_is_not_session_evidence(change):
    listing, sessions, target, cutoff = evidence()
    if change == "five":
        sessions.pop()
    if change == "duplicate":
        sessions[-1] = dict(sessions[0])
    if change == "wrong_venue":
        sessions[-1]["venue"] = "XSHE"
    if change == "wrong_listing":
        sessions[-1]["listing_id"] = "other"
    if change == "future":
        sessions[-1]["knowledge_from"] = cutoff + timedelta(days=1)
    if change == "suspended":
        sessions[-1]["trading"] = False
    if change == "ambiguous":
        sessions[-1]["calendar_versions"] = 2
    if change == "before_listing":
        listing["effective_from"] = target
    with pytest.raises(repair.RepairError):
        repair.exclude_ipo_window(rule(), listing, sessions, target, cutoff)


@pytest.mark.parametrize(
    "board,day",
    [
        ("SZ_main", date(2026, 9, 30)),
        ("ChiNext", date(2026, 9, 30)),
        ("SSE_main", date(2026, 7, 3)),
    ],
)
def test_sse_rule_cannot_be_applied_to_other_markets_or_before_effective_date(board, day):
    listing, sessions, _, cutoff = evidence()
    listing["board"] = board
    with pytest.raises(repair.RepairError):
        repair.exclude_ipo_window(rule(), listing, sessions, day, cutoff)


def test_future_or_unverified_official_rule_does_not_qualify():
    listing, sessions, target, cutoff = evidence()
    for invalid in [
        repair.SseRule("sha256:" + "f" * 64, rule().known),
        repair.SseRule(repair.SSE_RULE_SHA, cutoff + timedelta(days=1)),
    ]:
        with pytest.raises(repair.RepairError):
            repair.exclude_ipo_window(invalid, listing, sessions, target, cutoff)


def test_exclusive_bond_title_is_not_stock_special_treatment():
    assert (
        repair.event_instrument("关于2013年公司债券（13荣信01）实行风险警示的公告") == "BOND_ONLY"
    )
    assert repair.event_instrument("关于2012年公司债券停牌暨实行风险警示的公告") == "BOND_ONLY"
    for title in [
        "关于股票及公司债券实施风险警示的公告",
        "关于股票撤销退市风险警示的公告",
        "关于申请撤销股票其他风险警示的公告",
        "关于可转换公司债券的公告",
    ]:
        assert repair.event_instrument(title) == "UNRESOLVED"


def test_unproven_ipo_is_still_blocked_without_explicit_successor_proof():
    row = market_row()
    row["regimes"] = [r for r in row["regimes"] if r["ipo"] is None]
    assert state(row)["reason_code"] == "IPO_SOURCE_CLOSURE"


def test_explicit_successor_preserves_original_source_and_frozen_default():
    listing, sessions, target, cutoff = evidence()
    proof = repair.exclude_ipo_window(rule(), listing, sessions, target, cutoff)
    row = market_row()
    row.update(listing_code="600001", board="SSE_main")
    row["regimes"] = [r for r in row["regimes"] if r["ipo"] is None]
    before = deepcopy(row)
    result = _state_row(
        row,
        ordinal=6,
        complete=False,
        prior=Decimal("10"),
        policy=json.loads(Path("config/price-limit.v2.json").read_bytes()),
        raw_sha="sha256:" + "a" * 64,
        window_proof=proof,
    )
    assert result["execution_source_qualified"] is True
    assert result["execution_authorized"] is False
    assert result["ipo_rule"] == "CURRENT_SSE_RULE_WINDOW_EXCLUDED"
    assert row == before
    assert state(row)["reason_code"] == "IPO_SOURCE_CLOSURE"
    row["listing_code"] = "600002"
    bad = _state_row(
        row,
        ordinal=6,
        complete=False,
        prior=Decimal("10"),
        policy=json.loads(Path("config/price-limit.v2.json").read_bytes()),
        raw_sha="sha256:" + "a" * 64,
        window_proof=proof,
    )
    assert bad["reason_code"] == "WINDOW_PROOF_TARGET"


def test_exclusive_bond_withdrawal_is_unknown_not_inferred_normal():
    raw = dict(
        stock_code="002099",
        interval_id="interval",
        event_kind="implement_other",
        announcement_date=date(2015, 5, 13),
        source_url_or_notice_id="http://www.cninfo.com.cn/new/disclosure/detail?stockCode=002099&announcementId=123",
    )
    event = dict(
        stock_code="002099",
        ann_id="123",
        event_kind="implement_other",
        announcement_date=date(2015, 5, 13),
        title="关于2012年公司债券停牌暨实行风险警示的公告",
    )
    native = dict(
        status_type="SPECIAL_TREATMENT",
        status_value="ST",
        source_artifact_sha256="sha256:" + "a" * 64,
        source_record_id="payload/canonical/ref_st_status_pit.parquet#interval_id=interval",
    )
    originals = deepcopy((native, raw, event))
    kwargs = dict(
        raw_sha256="sha256:" + "a" * 64,
        event_sha256="sha256:" + "b" * 64,
        repair_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    proof = repair.withdraw_bond_claim(native, raw, event, **kwargs)
    assert proof["successor_value"] == "UNKNOWN"
    assert (native, raw, event) == originals
    event["ann_id"] = "other"
    with pytest.raises(repair.RepairError, match="EVENT_SOURCE_LINK"):
        repair.withdraw_bond_claim(native, raw, event, **kwargs)


def test_sse_st_revision_preserves_old_record_and_splits_effective_interval():
    old = dict(
        price_limit_regime_id="old",
        listing_id=None,
        board_code=None,
        security_status="ST",
        upper_limit_ratio=Decimal("0.05"),
        lower_limit_ratio=Decimal("0.05"),
        effective_from=datetime(1998, 4, 22, tzinfo=timezone.utc),
        effective_to=None,
    )
    snapshot = deepcopy(old)
    known = datetime(2026, 10, 23, tzinfo=timezone.utc)
    proof = repair.sse_st_successor(rule(), old, mic="XSHG", repair_at=known)
    assert old == snapshot
    assert proof["replacement"]["upper_limit_ratio"] == Decimal("0.1")
    assert proof["replacement"]["knowledge_from"] == known
    assert proof["retained_historical_fragment"]["upper_limit_ratio"] == Decimal("0.05")
    assert (
        proof["retained_historical_fragment"]["effective_to"]
        == proof["replacement"]["effective_from"]
    )
    with pytest.raises(repair.RepairError):
        repair.sse_st_successor(rule(), old, mic="XSHE", repair_at=known)


def test_tampered_exclusion_summary_cannot_bypass_recomputation():
    listing, sessions, target, cutoff = evidence()
    proof = repair.exclude_ipo_window(rule(), listing, sessions, target, cutoff)
    row = market_row()
    row.update(listing_code="600001", board="SSE_main")
    proof["observed_session_count"] = 100
    with pytest.raises(repair.RepairError, match="WINDOW_PROOF_SEMANTICS"):
        repair.validate_window_proof(proof, row)


def test_special_lifecycle_is_not_hidden_by_window_exclusion():
    listing, sessions, target, cutoff = evidence()
    proof = repair.exclude_ipo_window(rule(), listing, sessions, target, cutoff)
    row = market_row()
    row.update(listing_code="600001", board="SSE_main")
    row["lifecycle"].append(dict(kind="OTHER", value="RELISTED"))
    with pytest.raises(repair.RepairError, match="SPECIAL_LIFECYCLE_UNRESOLVED"):
        repair.validate_window_proof(proof, row)


def test_official_receipt_cannot_be_replaced_with_a_fabricated_document(tmp_path):
    body, receipt = tmp_path / "rule", tmp_path / "receipt"
    body.write_bytes(b"not the original rule")
    receipt.write_text("{}")
    with pytest.raises(repair.RepairError, match="OFFICIAL_RULE_HASH"):
        repair.load_sse_rule(body, receipt)


def test_sealer_rejects_old_output_before_writing(tmp_path):
    (tmp_path / "INPUTS.json").write_text('{"files":{}}')
    (tmp_path / "OUTPUTS.json").write_text("previous attempt")
    with pytest.raises(repair.RepairError, match="UNSEALED_FILE_SET"):
        repair.seal_evidence(tmp_path, "a" * 40)
    assert (tmp_path / "OUTPUTS.json").read_text() == "previous attempt"
    assert not (tmp_path / "READY").exists()
