from datetime import date, datetime, timezone

from quanthus.expert_strategy import p3_retrospective_support as support


def test_static_snapshot_keeps_old_claim_and_does_not_invent_missing_industry():
    securities = [
        {"security_id": "s", "company_id": "c"},
        {"security_id": "missing", "company_id": "missing"},
    ]
    listings = [
        {
            "security_id": "s",
            "effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "listing_code": "000001",
            "venue_id": "v",
        }
    ]
    nodes = [
        {
            "industry_node_id": "old",
            "node_level": 1,
            "industry_scheme_id": "scheme",
            "parent_node_id": None,
        },
        {
            "industry_node_id": "new",
            "node_level": 1,
            "industry_scheme_id": "scheme",
            "parent_node_id": None,
        },
    ]
    memberships = [
        {
            "company_id": "c",
            "industry_node_id": "old",
            "source_declared_as_of": date(2026, 7, 27),
            "membership_type": "PRIMARY_CLASSIFICATION",
        },
        {
            "company_id": "c",
            "industry_node_id": "new",
            "source_declared_as_of": date(2026, 9, 5),
            "membership_type": "PRIMARY_CLASSIFICATION",
        },
    ]
    selected, unknown = support.static_memberships(
        securities,
        listings,
        memberships,
        nodes,
        [{"venue_id": "v", "mic_code": "XSHE"}],
        business_end=date(2026, 9, 18),
    )
    assert selected[0]["level1_industry_id"] == "new"
    assert memberships[0]["industry_node_id"] == "old"
    assert [r["security_id"] for r in unknown] == ["missing"]
    memberships.append(dict(memberships[0], source_declared_as_of=date(2026, 9, 5)))
    selected, unknown = support.static_memberships(
        securities,
        listings,
        memberships,
        nodes,
        [{"venue_id": "v", "mic_code": "XSHE"}],
        business_end=date(2026, 9, 18),
    )
    assert not selected and len(unknown) == 2


def test_identical_company_versions_are_one_static_identity():
    securities = [{"security_id": "s", "company_id": "a"}] * 2
    args = (
        [{"security_id": "s", "effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
          "listing_code": "000001", "venue_id": "v"}],
        [{"company_id": "a", "industry_node_id": "n",
          "source_declared_as_of": date(2026, 7, 27),
          "membership_type": "PRIMARY_CLASSIFICATION"}],
        [{"industry_node_id": "n", "node_level": 1,
          "industry_scheme_id": "scheme", "parent_node_id": None}],
        [{"venue_id": "v", "mic_code": "XSHE"}],
    )
    rows, missing = support.static_memberships(
        securities, *args, business_end=date(2026, 9, 18)
    )
    assert len(rows) == 1 and not missing
    rows, missing = support.static_memberships(
        securities + [{"security_id": "s", "company_id": "b"}],
        *args, business_end=date(2026, 9, 18)
    )
    assert not rows and missing[0]["reason"] == "STATIC_COMPANY_AMBIGUOUS"


def test_full_census_rejects_support_security_swap_and_future_qualification():
    import duckdb
    import pytest

    from quanthus.expert_strategy.p3_base24_native import InputError

    with duckdb.connect(":memory:") as c:
        c.execute("CREATE TABLE j AS SELECT 'l' listing_id, 's' security_id, "
                  "DATE '2025-01-02' trade_date")
        c.execute("""CREATE TABLE s AS SELECT 'l' listing_id, 's' security_id,
          DATE '2025-01-02' market_date, 100 listing_sessions_lower_bound,
          true qualified, true entry, 11.0 upper, 9.0 lower, 'qualified' reason,
          'sha256:' || repeat('a',64) evidence_sha256,
          TIMESTAMPTZ '2026-10-23 00:00:00+00' qualified_at""")
        cutoff = datetime(2026, 10, 23, tzinfo=timezone.utc)
        assert support.validate_relations(c, cutoff=cutoff, expected_rows=1)["rows"] == 1
        c.execute("UPDATE s SET security_id='other'")
        with pytest.raises(InputError, match="EXACT_CENSUS"):
            support.validate_relations(c, cutoff=cutoff, expected_rows=1)
        c.execute("UPDATE s SET security_id='s', qualified_at=qualified_at+INTERVAL 1 DAY")
        with pytest.raises(InputError, match="SEMANTIC_CENSUS"):
            support.validate_relations(c, cutoff=cutoff, expected_rows=1)


def test_bounded_reader_retains_true_snapshot_and_applies_exact_daily_support(
    tmp_path, monkeypatch
):
    import json
    from decimal import Decimal

    import pyarrow as pa
    import pyarrow.parquet as pq

    from tests.unit.test_p3_base24_batch import industry
    from tests.unit.test_p3_retrospective_bridge import INFO, SOURCE, observation

    row = observation(date(2025, 2, 20))
    pq.write_table(pa.Table.from_pylist([row]), tmp_path / "joined.parquet")
    pq.write_table(pa.Table.from_pylist([{
        "listing_id": row["listing_id"], "security_id": row["security_id"],
        "market_date": row["trade_date"], "listing_sessions_lower_bound": 200,
        "qualified": True, "entry": True, "upper": Decimal("12"), "lower": Decimal("8"),
        "reason": "qualified", "evidence_sha256": SOURCE, "qualified_at": INFO,
    }]), tmp_path / "execution.parquet")
    industry(tmp_path / "industry.json")
    (tmp_path / "calendar").mkdir()
    (tmp_path / "calendar/declared-sessions.json").write_text("[]")
    (tmp_path / "actions.json").write_text("[]")
    (tmp_path / "action-exceptions.json").write_text("[]")
    plan = {"information_cutoff": INFO.isoformat(), "source_manifest_sha256": SOURCE,
            "expected_rows": 1, "trading_dates": ["2025-02-20"]}
    monkeypatch.setattr(support, "verify", lambda *a, **k: {"plan": plan, "files": {}})
    result = list(support.ports(tmp_path, manifest_sha256=SOURCE))
    assert len(result) == 1
    assert result[0].information_cutoff == INFO
    assert result[0].selected.bar.close_price.as_tuple() == row["close"].as_tuple()
    assert not result[0].unavailable_reasons
    (tmp_path / "action-exceptions.json").write_text(
        json.dumps([{"security_id": row["security_id"]}])
    )
    blocked = list(support.ports(tmp_path, manifest_sha256=SOURCE))
    assert "ADJUSTMENT_BOUNDARY_UNAVAILABLE" in blocked[0].unavailable_reasons
