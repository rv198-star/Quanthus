from __future__ import annotations

import pytest

from quanthus.backtest.native_capital_qualification import business_predicate, member_table

SCOPE = {
    "market_start": "2020-01-02",
    "execution_end": "2021-12-31",
    "calendar_start": "2019-01-01",
}


@pytest.mark.parametrize(
    "name",
    [
        "payload/dwd_security_bar/observation_start_year=2022/a.parquet",
        "payload/dwd_corporate_action/effective_at_year=2026/a.parquet",
        "payload/dwd_trading_calendar/trade_date_year=2018/a.parquet",
        "payload/not_declared/all/a.parquet",
        "../payload/dwd_security/all/a.parquet",
    ],
)
def test_protected_or_undeclared_members_refused(name):
    with pytest.raises(ValueError):
        member_table(name)


def test_calendar_only_prefix_and_no_clock_fallback():
    _, calendar = business_predicate("dwd_trading_calendar", {"trade_date"}, SCOPE)
    _, market = business_predicate("dwd_security_bar", {"observation_start"}, SCOPE)
    assert "2019-01-01" in calendar and "2020-01-02" in market
    with pytest.raises(ValueError, match="business clock missing"):
        business_predicate("dwd_corporate_action", {"announced_at"}, SCOPE)


def test_interval_overlap_is_half_open_and_unknown_retained():
    _, predicate = business_predicate("dwd_listing", {"effective_from", "effective_to"}, SCOPE)
    assert "effective_from IS NULL" in predicate
    assert "effective_to AS DATE)>DATE '2020-01-02'" in predicate
    with pytest.raises(ValueError, match="interval end missing"):
        business_predicate("dwd_listing", {"effective_from"}, SCOPE)


def test_bounded_census_preserves_source_clock_and_directions(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from quanthus.backtest.native_capital_qualification import KEYS, TABLES, census

    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    members = []
    source = tmp_path / "source"
    for table in sorted(TABLES):
        fields = {
            name: ("TIMESTAMPTZ" if name == "effective_from" else "VARCHAR") for name in KEYS[table]
        }
        fields.update(knowledge_from="TIMESTAMPTZ", knowledge_to="TIMESTAMPTZ")
        if "effective_from" in fields:
            fields["effective_to"] = "TIMESTAMPTZ"
        clock = {
            "dwd_trading_calendar": "trade_date",
            "dwd_security_bar": "observation_start",
            "dwd_tradability_state": "observation_start",
            "dwd_corporate_action": "effective_at",
            "dwd_corporate_action_term": "effective_at",
            "dwd_quality_exception": "affected_effective_at",
        }.get(table, "effective_from")
        fields[clock] = "TIMESTAMPTZ"
        if table == "dwd_tradability_state":
            fields.update(
                is_tradable="BOOLEAN", can_buy="BOOLEAN", can_sell="BOOLEAN", reason_code="VARCHAR"
            )
        if table == "dwd_corporate_action_term":
            fields.update(corporate_action_id="VARCHAR", term_type="VARCHAR")
        c.execute(
            "CREATE TABLE "
            + table
            + "("
            + ",".join(name + " " + kind for name, kind in fields.items())
            + ")"
        )
        rows = []
        for year in (2020, 2022):
            row = []
            for name in fields:
                if name == clock:
                    row.append(f"{year}-01-02T00:00:00+08:00")
                elif name == "knowledge_from":
                    row.append("2026-10-31T00:00:00+08:00")
                elif name in ("knowledge_to", "effective_to"):
                    row.append(None)
                elif name in ("is_tradable", "can_sell"):
                    row.append(True)
                elif name == "can_buy":
                    row.append(False)
                elif name == "corporate_action_id":
                    row.append(str(year))
                else:
                    row.append(str(year))
            rows.append(row)
        c.executemany(
            "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in fields) + ")", rows
        )
        relative = "payload/" + table + "/all/part.parquet"
        p = source / "DWD" / relative
        p.parent.mkdir(parents=True)
        c.execute(f"COPY {table} TO '{p}' (FORMAT PARQUET)")
        members.append({"path": relative, "size_bytes": p.stat().st_size})
    c.close()
    q = {
        "scope": SCOPE | {"source_clock": "preserve"},
        "budget": {
            "threads": 2,
            "duckdb_memory_bytes": 268435456,
            "max_scratch_bytes": 536870912,
            "max_action_examples": 8,
        },
    }
    result = census(source, members, q, tmp_path / "scratch")
    assert result["strategy_or_pnl_evaluated"] is False
    assert len(result["stages"]) == len(TABLES)
    assert all(x["rows"] == 1 for x in result["tables"].values())
    assert result["tables"]["dwd_corporate_action"]["source_knowledge_after_business_date"] == 1
    assert result["relations"]["directional_states"] == [[True, False, True, 1]]
