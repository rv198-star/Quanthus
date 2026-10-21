"""Native P10 purpose qualification, without strategy or runtime activation."""

import json
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base24_p6_purpose import (
    PurposeError,
    action_terms,
    ipo_window,
    resolve_status,
)
from tests.unit.test_base24_semantic_projection import view as view


def test_unknown_status_is_not_assumed_normal_and_provider_may_resolve_it():
    assert resolve_status(["UNKNOWN"], []) == (None, "STATUS_UNKNOWN")
    assert resolve_status(["UNKNOWN"], ["NORMAL"]) == ("NORMAL", "PROVIDER_STATE")
    assert resolve_status(["ST"], ["DELISTING_RISK"]) == ("ST", "CANONICAL_STATE")
    assert resolve_status(["NORMAL"], ["ST"]) == (None, "STATUS_CONFLICT")
    assert resolve_status(["NORMAL", "ST"], []) == (None, "STATUS_CONFLICT")


@pytest.mark.parametrize("count", [None, -1, 1.5, True])
def test_ipo_window_requires_actual_valid_source_duration(count):
    with pytest.raises(PurposeError, match="IPO_DURATION"):
        ipo_window("ipo_first_5_trading_days_no_limit", count, 1, True)


def test_ipo_counts_actual_eligible_sessions_without_universal_five_day_assumption():
    assert ipo_window("ipo_first_5_trading_days_no_limit", 5, 5, True) is True
    assert ipo_window("ipo_first_5_trading_days_no_limit", 5, 6, False) is False
    assert ipo_window("ipo_main_first_trading_day_44pct", 1, 2, False) is False
    with pytest.raises(PurposeError, match="IPO_COUNT_INCOMPLETE"):
        ipo_window("ipo_first_5_trading_days_no_limit", 5, 3, False)
    with pytest.raises(PurposeError, match="IPO_RULE"):
        ipo_window("not-reviewed", 5, 6, True)


def term(kind, **kw):
    return dict(term_type=kind, option_id=None, **kw)


def test_native_units_are_exact_and_not_inferred_from_best_factor_fit():
    rows = [
        term("CASH_AMOUNT_PER_SHARE", amount_per_share=Decimal("0.1234567891"), currency="CNY"),
        term(
            "BONUS_SHARES_PER_SHARE", ratio_numerator=Decimal("1"), ratio_denominator=Decimal("10")
        ),
        term(
            "TRANSFER_SHARES_PER_SHARE",
            ratio_numerator=Decimal("2"),
            ratio_denominator=Decimal("10"),
        ),
    ]
    cash, shares = action_terms("MIXED_DISTRIBUTION", rows)
    assert cash == Decimal("0.1234567891")
    assert shares == Decimal("0.3")
    assert action_terms("CASH_DIVIDEND", rows[:1]) == (cash, Decimal("0"))


@pytest.mark.parametrize(
    "rows,reason",
    [
        (
            [term("CASH_AMOUNT_PER_SHARE", amount_per_share=Decimal("1"), currency=None)],
            "ACTION_CURRENCY",
        ),
        (
            [term("CASH_AMOUNT_PER_SHARE", amount_per_share=Decimal("1"), currency="USD")],
            "ACTION_CURRENCY",
        ),
        ([term("CASH_AMOUNT_PER_SHARE", amount_per_share=None, currency="CNY")], "ACTION_VALUE"),
        (
            [term("CASH_AMOUNT_PER_SHARE", amount_per_share=Decimal("NaN"), currency="CNY")],
            "ACTION_VALUE",
        ),
        ([term("SOME_NEW_TERM")], "ACTION_TERM"),
        (
            [
                term(
                    "BONUS_SHARES_PER_SHARE",
                    ratio_numerator=Decimal("1"),
                    ratio_denominator=Decimal("0"),
                )
            ],
            "ACTION_RATIO",
        ),
        ([], "ACTION_TERM_MISSING"),
    ],
)
def test_incomplete_or_unknown_action_terms_remain_held(rows, reason):
    with pytest.raises(PurposeError, match=reason):
        action_terms("MIXED_DISTRIBUTION", rows)


def test_duplicate_or_alternative_terms_are_not_summed_or_arbitrarily_selected():
    row = term("CASH_AMOUNT_PER_SHARE", amount_per_share=Decimal("1"), currency="CNY")
    for rows in [[row, row], [{**row, "option_id": "alternative"}]]:
        with pytest.raises(PurposeError, match="ACTION_TERM_AMBIGUOUS"):
            action_terms("CASH_DIVIDEND", rows)


def test_factor_evidence_is_compared_without_changing_native_cash_units():
    from quanthus.pipelines.securities_warehouse.base24_p6_purpose import factor_check

    got = factor_check(
        Decimal("10"), Decimal("1"), Decimal("1.25"), Decimal("2"), Decimal("0"), Decimal("0.005")
    )
    assert got[0] == "matched"
    assert got[1] == Decimal("1.25")
    assert (
        factor_check(
            None, Decimal("1"), Decimal("1.25"), Decimal("2"), Decimal("0"), Decimal("0.005")
        )[0]
        == "unavailable"
    )
    assert (
        factor_check(
            Decimal("10"), Decimal("1"), Decimal("2"), Decimal("2"), Decimal("0"), Decimal("0.005")
        )[0]
        == "mismatch"
    )


def market_row():
    return dict(
        listing_id="l",
        security_id="s",
        listing_code="000001",
        market_date=date(2026, 9, 30),
        open=Decimal("10"),
        high=Decimal("10"),
        low=Decimal("10"),
        close=Decimal("10"),
        volume=Decimal("1"),
        amount=Decimal("10"),
        is_tradable=True,
        can_buy=True,
        can_sell=True,
        upper_price=Decimal("11"),
        lower_price=Decimal("9"),
        board="SZ_main",
        listing_count=1,
        security_count=1,
        bar_count=1,
        calendar_count=1,
        venue_count=1,
        state_versions=1,
        bar_known=datetime(2026, 10, 1, tzinfo=timezone.utc),
        state_known=datetime(2026, 10, 1, tzinfo=timezone.utc),
        dated_status=[
            dict(
                value="NORMAL",
                reason="PROVIDER_TRADING",
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        statuses=[
            dict(
                kind="PROVIDER_DAILY_STATE",
                value="NORMAL",
                versions=1,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        lifecycle=[
            dict(
                kind="LISTING_LIFECYCLE",
                value="LISTED",
                versions=1,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        regimes=[
            dict(
                id="base",
                priority=10,
                kind="PERCENT_LIMIT",
                status=None,
                ipo=None,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
                upper_ratio=Decimal("0.1"),
                lower_ratio=Decimal("0.1"),
                versions=1,
            ),
            dict(
                id="ipo",
                priority=1,
                kind="NO_LIMIT",
                status=None,
                ipo="INITIAL_LISTING_WINDOW",
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
                upper_ratio=None,
                lower_ratio=None,
                versions=1,
                raw_evidence="sha256:" + "a" * 64,
                rule="ipo_first_5_trading_days_no_limit",
                duration=5,
            ),
        ],
    )


def state(row):
    from quanthus.pipelines.securities_warehouse.base24_p6_purpose import _state_row

    return _state_row(
        row,
        ordinal=6,
        complete=False,
        prior=Decimal("10"),
        policy=json.loads(Path("config/price-limit.v2.json").read_bytes()),
        raw_sha="sha256:" + "a" * 64,
    )


def test_native_execution_positive_preserves_original_bounds_and_no_runtime_authority():
    result = state(market_row())
    assert result["warmup_eligible"] is True
    assert result["execution_source_qualified"] is True
    assert result["qualified_entry_allowed"] is True
    assert result["in_ipo_window"] is False
    assert result["source_upper_price"] == Decimal("11")
    assert result["execution_authorized"] is False


@pytest.mark.parametrize("collection", ["statuses", "lifecycle", "regimes", "dated_status"])
def test_market_evidence_is_stable_under_unordered_native_join_rows(collection):
    row = market_row()
    if len(row[collection]) == 1:
        row[collection] = [*row[collection], {**row[collection][0], "evidence": "second"}]
    original = list(row[collection])
    reordered = {**row, collection: list(reversed(original))}
    assert state(row) == state(reordered)
    assert row[collection] == original


def test_market_evidence_still_binds_changed_values():
    row = market_row()
    changed = {**row, "amount": Decimal("10.0000000001")}
    assert state(row)["evidence_digest"] != state(changed)["evidence_digest"]


def test_action_evidence_is_stable_under_unordered_native_terms(monkeypatch):
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose

    when = datetime(2026, 9, 30, tzinfo=timezone.utc)
    known = datetime(2026, 10, 1, tzinfo=timezone.utc)
    common = dict(
        corporate_action_id="a", effective_at=when, knowledge_from=known, visible_versions=1
    )
    terms = [
        dict(common, term_type="CASH_AMOUNT_PER_SHARE", option_id=None,
             amount_per_share=Decimal("2"), currency="CNY"),
        dict(common, term_type="BONUS_SHARES_PER_SHARE", option_id=None,
             ratio_numerator=Decimal("1"), ratio_denominator=Decimal("10")),
    ]
    action = dict(common, security_id="s", ex_date=when.date(),
                  action_status="IMPLEMENTED", action_type="MIXED_DISTRIBUTION")
    ref = dict(corporate_action_id="a", prior_close=Decimal("10"),
               factor_before=Decimal("1"), factor_after=Decimal("1.375"),
               bar_known=known, factor_known=known)

    def rows(_connection, query):
        if query.startswith("SELECT * FROM p_dwd_corporate_action_term"):
            return list(terms)
        if query.startswith("SELECT * FROM p_dwd_corporate_action ORDER"):
            return [action]
        return [ref]

    monkeypatch.setattr(purpose, "_rows", rows)
    policy = json.loads(Path("config/p1-corporate-action-normalization.v3.json").read_bytes())
    original = purpose._action_rows(None, policy)
    terms.reverse()
    reordered = purpose._action_rows(None, policy)
    assert original[0]["qualified"] is True
    assert original == reordered


def test_missing_native_bounds_are_derived_separately_with_exact_frozen_policy():
    row = {**market_row(), "upper_price": None, "lower_price": None}
    result = state(row)
    assert result["execution_source_qualified"] is True
    assert (result["source_upper_price"], result["source_lower_price"]) == (None, None)
    assert (result["qualified_upper_price"], result["qualified_lower_price"]) == (
        Decimal("11"),
        Decimal("9"),
    )
    assert result["bound_basis"] == "FROZEN_POLICY_DERIVED_NATIVE_NULLS_PRESERVED"
    assert row["upper_price"] is None
    row["upper_price"] = Decimal("12")
    assert state(row)["reason_code"] == "NATIVE_PRICE_BOUND_DISAGREEMENT"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"dated_status": []}, "DATED_STATUS_UNAVAILABLE_OR_AMBIGUOUS"),
        ({"bar_known": None}, "MARKET_KNOWLEDGE_UNKNOWN"),
        ({"upper_price": Decimal("12")}, "NATIVE_PRICE_BOUND_DISAGREEMENT"),
        ({"listing_count": 0}, "IDENTITY_VERSION_AMBIGUOUS"),
        ({"state_versions": 2}, "STATE_VERSION_AMBIGUOUS"),
        ({"calendar_count": 0}, "CORE_VERSION_AMBIGUOUS"),
        ({"close": None}, "MARKET_OBSERVATION_MISSING"),
        ({"volume": Decimal("0")}, "TRADABLE_ZERO_ACTIVITY"),
        ({"listing_code": "689009"}, "FROZEN_POLICY_OUT_OF_SCOPE"),
    ],
)
def test_affected_execution_key_fails_closed_without_flipping_source_state(change, reason):
    row = {**market_row(), **change}
    result = state(row)
    assert result["reason_code"] == reason
    assert result["qualified_entry_allowed"] is False
    assert result["source_is_tradable"] is True
    assert state(market_row())["qualified_entry_allowed"] is True


def test_same_priority_regime_conflict_is_not_arbitrarily_resolved():
    row = market_row()
    row["regimes"].append({**row["regimes"][0], "id": "conflicting-rule"})
    assert state(row)["reason_code"] == "REGIME_PRIORITY_CONFLICT"


def test_real_materializer_and_cold_verifier_preserve_late_source_and_held_rows(view, tmp_path):
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from tests.unit.test_p6_base24_inputs import build

    original = build(view, tmp_path)
    ipo = tmp_path / "ipo.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [],
            schema=pa.schema(
                [
                    ("interval_id", pa.string()),
                    ("listing_window_rule", pa.string()),
                    ("no_limit_days", pa.float64()),
                    ("effective_start", pa.date32()),
                ]
            ),
        ),
        ipo,
    )
    # The raw source parent is disjoint from the output (a real input file is not a workspace).
    input_dir = tmp_path / "ipo-source"
    input_dir.mkdir()
    ipo = ipo.rename(input_dir / ipo.name)
    out = tmp_path / "qualification"
    result = purpose.build(
        original,
        out,
        manifest_sha256=file_sha(original / "manifest.json"),
        ipo_source=ipo,
        ipo_source_sha256=file_sha(ipo),
        config_root=Path("config"),
        code_revision="a" * 40,
    )
    assert result["market"]["market_rows"] == 1
    assert result["market"]["execution_source_qualified"] == 0
    assert result["historical_pit_granted"] is False
    actions = pq.read_table(out / "qualified/actions.parquet").to_pylist()
    assert actions[0]["native_knowledge_from"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert actions[0]["qualified"] is False
    frozen = file_sha(out / "manifest.json")
    shutil.rmtree(original)
    shutil.rmtree(input_dir)
    moved = tmp_path / "moved-qualification"
    out.rename(moved)
    assert purpose.verify(moved, frozen)["market"] == result["market"]
    from quanthus.forward_validation.base24_inputs import open_base24_qualified_context
    from quanthus.forward_validation.contracts import ForwardValidationContractError

    with pytest.raises(ForwardValidationContractError, match="context_cutoff"):
        open_base24_qualified_context(
            moved,
            manifest_sha256=frozen,
            decision_knowledge_cutoff=datetime(2026, 9, 30, tzinfo=timezone.utc),
        )
    context = open_base24_qualified_context(
        moved,
        manifest_sha256=frozen,
        decision_knowledge_cutoff=datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    assert not context.execution_authorized
    assert sum(b.num_rows for b in context.iter_batches("execution_qualification")) == 1
    assert sum(b.num_rows for b in context.iter_batches("daily_ohlcv_none")) == 0
    with pytest.raises(ForwardValidationContractError, match="reader_scope"):
        list(context.iter_batches("historical_replay"))

    # Repack every outer hash: altered qualification still fails semantics.
    path = moved / "qualified/2026-09/execution.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    rows[0]["qualified_entry_allowed"] = True
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    m = json.loads((moved / "manifest.json").read_bytes())
    m["files"]["qualified/2026-09/execution.parquet"] = {
        "sha256": file_sha(path),
        "bytes": path.stat().st_size,
    }
    (moved / "manifest.json").write_text(json.dumps(m))
    (moved / "READY").write_text(file_sha(moved / "manifest.json") + "\n")
    with pytest.raises(purpose.PurposeError, match="QUALIFIED_VALUES"):
        purpose.verify(moved, file_sha(moved / "manifest.json"))


def test_configuration_and_original_price_action_policy_bytes_are_frozen():
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    assert (
        json.loads(Path("config/forward-validation/data/p6-base24-purpose.v1.json").read_bytes())
        == purpose.CONTRACT
    )
    for name, expected in purpose.POLICY_HASHES.items():
        assert file_sha(Path("config") / name) == expected


def test_open_interval_status_is_not_a_dated_state_attestation():
    row = market_row()
    row["dated_status"] = []
    assert row["statuses"][0]["value"] == "NORMAL"
    assert state(row)["reason_code"] == "DATED_STATUS_UNAVAILABLE_OR_AMBIGUOUS"
    assert state(row)["warmup_eligible"] is True
    row["dated_status"] = [
        dict(
            value="ST", reason="PROVIDER_TRADING", known=datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
    ]
    row["statuses"].append({**row["statuses"][0], "kind": "SPECIAL_TREATMENT"})
    assert state(row)["reason_code"] == "STATUS_CONFLICT"


def test_dated_state_is_local_exact_date_late_knowledge_and_equal_time_conflicts(tmp_path):
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base24_p6_handoff import _connection

    evidence = tmp_path / "evidence"
    (evidence / "status").mkdir(parents=True)
    row = dict(
        stock_code="000001",
        market_date="2026-07-27",
        isST="0",
        tradestatus="1",
        acquired_at="2026-07-30T02:17:02+08:00",
    )
    temp = tmp_path / "temp.parquet"
    pq.write_table(pa.Table.from_pylist([row, row, {**row, "isST": "1"}]), temp)
    sha = file_sha(temp)
    temp.rename(evidence / "status" / (sha[7:] + ".parquet"))
    member = dict(
        path="payload/test/daily_status_observation_delta.parquet",
        sha256=sha,
        size_bytes=(evidence / "status" / (sha[7:] + ".parquet")).stat().st_size,
    )
    inventory = evidence / "status-original-manifest.json"
    inventory.write_text(json.dumps({"content": {"files": [member]}}))
    (evidence / "status-selection.json").write_text(
        json.dumps({sha: {"original_members": [member], "inventory_sha256": file_sha(inventory)}})
    )
    for cutoff, expected in [("2026-07-29T00:00:00+00:00", 0), ("2026-07-31T00:00:00+00:00", 2)]:
        c = _connection(tmp_path)
        try:
            purpose._dated_status(c, tmp_path, cutoff)
            rows = c.execute("SELECT * FROM dated_status").fetchall()
            assert len(rows) == expected
            assert all(r[1] == date(2026, 7, 27) for r in rows)
            assert all(str(tmp_path) not in r[-1] for r in rows)
        finally:
            c.close()


def test_configured_scratch_inside_source_is_rejected_before_any_write(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose

    root = tmp_path / "image"
    root.mkdir()
    payload = root / "protected"
    payload.write_bytes(b"immutable bytes")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    monkeypatch.setenv("TMPDIR", str(root))
    with pytest.raises(purpose.PurposeError, match="SCRATCH_SOURCE_OVERLAP"):
        purpose.verify(root, "sha256:" + "a" * 64)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_status_selection_cannot_dereference_outside_image(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose

    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/status-selection.json").write_text(
        json.dumps({"sha256:/old/source/private": {}})
    )

    def unexpected_hash(path):
        pytest.fail("No source member may be opened for an invalid selection")

    monkeypatch.setattr(purpose, "file_sha", unexpected_hash)
    with pytest.raises(purpose.PurposeError, match="STATUS_LOCAL_SELECTION"):
        purpose._dated_status(None, tmp_path, "2026-10-21T00:00:00+00:00")
