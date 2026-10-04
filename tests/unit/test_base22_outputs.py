from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, partition, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_outputs import (
    acquisition,
    announcement_observation,
    holder_observation,
    knowledge_versions,
    tail_observation,
    weekly_action_observation,
    write_partition,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError

OLD = datetime(2026, 7, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def tail(**change):
    raw = {
        "stock_code": "000001",
        "trade_date": date(2026, 7, 31),
        "tradestatus": "1",
        **{
            k: Decimal("1.0000000001") for k in ("open", "high", "low", "close", "volume", "amount")
        },
        **change,
    }
    listing = {
        "listing_id": "listing",
        "venue_id": "venue",
        "listing_currency": "CNY",
        "effective_from": OLD,
        "effective_to": None,
        "knowledge_from": OLD,
        "knowledge_to": None,
    }
    calendar = {
        "calendar_session_id": "calendar",
        "venue_id": "venue",
        "trade_date": raw["trade_date"],
        "session_type": "day",
        "is_open": True,
        "knowledge_from": OLD,
        "knowledge_to": None,
    }
    return raw, listing, calendar


def project(raw, listing, calendar):
    return tail_observation(
        raw,
        listing=listing,
        calendar=calendar,
        observed_at=NOW,
        source_sha256="sha256:" + "a" * 64,
        revision="new-retained-revision",
    )


@pytest.mark.parametrize(
    "fault", ["date", "venue", "listing", "parent", "float", "scale", "clock", "september"]
)
def test_tail_refuses_invalid_closure(fault):
    raw, listing, calendar = tail()
    if fault == "date":
        calendar["trade_date"] = date(2026, 8, 3)
    if fault == "venue":
        calendar["venue_id"] = "another"
    if fault == "listing":
        listing["effective_from"] = NOW
    if fault == "parent":
        calendar["knowledge_from"] = NOW + timedelta(days=1)
    if fault == "float":
        raw["open"] = 1.0
    if fault == "scale":
        raw["open"] = Decimal("1.00000000001")
    if fault == "clock":
        listing["knowledge_to"] = NOW
    if fault == "september":
        raw["trade_date"] = calendar["trade_date"] = date(2026, 9, 1)
    with pytest.raises(SourceAssessmentError):
        project(raw, listing, calendar)


def test_tail_preserves_decimal_unknown_provider_time_and_zero_observation():
    raw, listing, calendar = tail(volume=Decimal("0"))
    row, evidence = project(raw, listing, calendar)
    assert row["open"] == raw["open"] and row["volume"] == 0
    assert row["knowledge_from"] == NOW and row["observation_start"].hour == 16
    assert evidence["provider_acquisition"] is None and evidence["state_mutations"] == 0
    assert evidence["classification"].startswith("QUARANTINED")


@pytest.mark.parametrize("fault", [None, "count", "partition", "duplicate"])
def test_actual_typed_serialization_and_closed_partial(tmp_path, fault):
    contract, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)["dwd_security_bar"]
    rule = contract["tables"]["dwd_security_bar"]
    row, _ = project(*tail())
    rows = [row, row] if fault == "duplicate" else [row]
    part = partition(row, rule)
    output = tmp_path / "job" / "outputs" / "bars.parquet"
    limits = Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2)
    with Budget(tmp_path / "job", [], limits) as budget:
        kwargs = dict(
            table="dwd_security_bar",
            expected_rows=2 if fault == "count" else len(rows),
            expected_partition="wrong" if fault == "partition" else part,
            schema=schema,
            rule=rule,
            admitted={
                (row["record_source"], row["source_revision"], row["source_artifact_sha256"])
            },
            output=output,
            budget=budget,
        )
        if fault:
            with pytest.raises(BoundedError):
                write_partition(rows, **kwargs)
            assert not output.exists() and output.with_suffix(".parquet.partial").exists()
        else:
            receipt = write_partition(rows, **kwargs)
            restored = pq.ParquetFile(output).read().to_pylist()[0]
            assert restored == row and receipt["rows"] == 1 and output.stat().st_nlink == 1
            with pytest.raises(BoundedError, match="EXISTS"):
                write_partition(rows, **kwargs)


def event(**changes):
    return dict(
        stock_code="000001",
        ann_id="doc",
        announce_date="2026-07-25",
        publish_time="2026-07-25 12:00:00",
        title="synthetic",
        url="synthetic",
        acquired_at="2026-08-01T19:30:04+08:00",
        **changes,
    )


def test_late_announcement_keeps_july_business_and_august_knowledge():
    result = announcement_observation(
        event(),
        company_id="c",
        security_id="s",
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="revision",
    )
    row = result["dwd_disclosure"][0]
    assert row["effective_at"].date() == date(2026, 7, 25)
    assert row["knowledge_from"] == datetime(2026, 8, 1, 11, 30, 4, tzinfo=timezone.utc)
    with pytest.raises(SourceAssessmentError):
        acquisition({"built_at": NOW}, OLD)


def test_true_body_revisions_deduplicate_snapshots_and_close_intervals():
    contract, _, _ = load_contract(Path("config/pipelines"))
    first = announcement_observation(
        event(),
        company_id="c",
        security_id="s",
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="r",
    )["dwd_disclosure"][0]
    same = dict(first, knowledge_from=first["knowledge_from"] + timedelta(days=1))
    revised = dict(
        first, title="revision", knowledge_from=first["knowledge_from"] + timedelta(days=2)
    )
    rows = knowledge_versions([same, revised, first], contract["tables"]["dwd_disclosure"])
    assert len(rows) == 2 and rows[0]["knowledge_to"] == revised["knowledge_from"]
    assert rows[1]["knowledge_to"] is None
    with pytest.raises(SourceAssessmentError):
        knowledge_versions(
            [first, dict(first, title="conflict")], contract["tables"]["dwd_disclosure"]
        )


def test_holder_date_precision_and_real_acquisition():
    row = {
        "code": "000001",
        "end_date": "2026-06-30",
        "shareholder_count": "10",
        "avg_hold_num": "1.0000000001",
        "hold_notice_date": "2026-07-25",
        "acquired_at": "2026-08-01T12:00:00+08:00",
    }
    out = holder_observation(
        row,
        company_id="c",
        security_id="s",
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="r",
    )["dwd_shareholder_count_observation"][0]
    assert out["holder_count"] == 10 and out["published_at"] is None
    assert out["knowledge_from"] == datetime(2026, 8, 1, 4, tzinfo=timezone.utc)


def test_weekly_action_has_no_date_to_publication_instant_inference():
    row = {
        "stock_code": "000001",
        "announce_date": "2026-07-25",
        "ex_date": "2026-08-03",
        "action_type": "cash_div",
        "progress": "done",
        "cash_per_share": "1.0000000001",
        "acquired_at": "2026-08-01T12:00:00+08:00",
    }
    vocab = {
        "action_types": {"cash_div": "CASH_DIVIDEND"},
        "action_statuses": {"done": "IMPLEMENTED"},
        "term_types": {"cash_per_share": "CASH"},
    }
    out = weekly_action_observation(
        row,
        company_id="c",
        security_id="s",
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="r",
        vocabulary=vocab,
    )
    assert out["dwd_corporate_action"][0]["announced_at"] is None
    assert out["dwd_corporate_action_term"][0]["amount_per_share"] == Decimal("1.0000000001")


def test_native_regular_day_session_is_preserved():
    raw, listing, calendar = tail()
    calendar["session_type"] = "REGULAR_DAY"
    row, _ = project(raw, listing, calendar)
    assert row["calendar_session_id"] == calendar["calendar_session_id"]


def test_quarterly_adapter_keeps_date_publication_unknown_and_exact_numeric(tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_outputs import quarterly_observation
    from quanthus.pipelines.securities_warehouse.financial_reporting import (
        FinancialCompanyIdentity,
        FinancialCompanyResolver,
        FinancialReportingSource,
    )

    source = FinancialReportingSource(
        "weekly_full_financial_quarterly",
        "quarterly_projection",
        "ledger",
        tmp_path / "source",
        "source.parquet",
        "hash",
        "r",
        "independent",
    )
    resolver = FinancialCompanyResolver(
        [FinancialCompanyIdentity("000001", "s", "c", OLD, None, OLD)]
    )
    raw = dict(
        stock_code="000001",
        report_date="2026-07-31",
        publish_date="2026-08-01",
        revenue="1.0000000001",
        net_profit=None,
        parent_net_profit="0",
        currency="CNY",
        acquired_at="2026-08-01T12:00:00+08:00",
    )
    result, exceptions = quarterly_observation(raw, source=source, resolver=resolver)
    assert not exceptions and result["dwd_financial_report"][0]["published_at"] is None
    facts = result["dwd_financial_fact"]
    assert any(r["value_decimal"] == Decimal("1.0000000001") for r in facts)
    with pytest.raises(SourceAssessmentError):
        quarterly_observation(dict(raw, acquired_at=None), source=source, resolver=resolver)


def test_industry_snapshot_starts_at_observed_snapshot_date_only():
    from quanthus.pipelines.securities_warehouse.base22_outputs import industry_snapshot_observation

    node = dict(
        industry_node_id="node",
        node_code="code",
        knowledge_from=OLD,
        knowledge_to=None,
        effective_from=OLD,
        effective_to=None,
    )
    out = industry_snapshot_observation(
        dict(
            stock_code="000001",
            market_date="2026-08-01",
            industry_code="code",
            provider_update_date="2012-01-01",
            acquired_at="2026-08-01T12:00:00+08:00",
        ),
        company_id="c",
        node=node,
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="r",
    )
    row = out["dwd_company_industry_membership"][0]
    assert row["effective_from"].astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
    ).date() == date(2026, 8, 1)
    assert row["source_declared_as_of"] == date(2026, 8, 1)
    import pyarrow as pa

    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas

    _, model, extension = load_contract(Path("config/pipelines"))
    typed = pa.Table.from_pylist(
        [row], schema=schemas(model, extension)["dwd_company_industry_membership"]
    )
    assert typed["confidence"].to_pylist() == [None]


def test_st_retrospective_clock_is_not_package_built_at():
    from quanthus.pipelines.securities_warehouse.base22_outputs import st_observation

    raw = dict(
        effective_start="2026-07-25",
        effective_end=None,
        is_st="1",
        status_label="st",
        confidence="source",
        event_kind="ST_CHANGE",
        interval_id="interval",
        built_at=OLD,
    )
    out = st_observation(
        raw,
        security_id="s",
        parent_knowledge=OLD,
        source_sha256="hash",
        revision="r",
        warehouse_observed_at=NOW,
    )["dwd_security_status_interval"][0]
    assert out["knowledge_from"] == NOW and out["effective_to"] is None


def test_sealed_history_resume_requires_exact_identity_and_accounts_existing_files(tmp_path):
    import pyarrow as pa

    from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
    from quanthus.pipelines.securities_warehouse.base22_history import (
        CLOSURE_FIELDS,
        consume_job_history,
        seal_history,
    )

    limits = Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2)
    closure = {k: "sha256:" + "a" * 64 for k in CLOSURE_FIELDS}
    root = tmp_path / "job"
    with Budget(root, [], limits) as budget:
        path = root / "history" / "identity.parquet"
        path.parent.mkdir()
        pq.write_table(pa.table({"id": ["synthetic"]}), path)
        seal = seal_history(
            budget,
            closure,
            {
                "identity.parquet": {
                    "rows": 1,
                    "sha256": file_sha(path),
                    "schema_sha256": digest(
                        str(pq.ParquetFile(path).schema_arrow.remove_metadata())
                    ),
                }
            },
        )
    with consume_job_history(
        Budget(root, [], limits), closure, seal["descriptor_sha256"]
    ) as history:
        assert history["identity"] == seal["identity"] and history["disposition"].startswith(
            "REUSE"
        )
    with (
        pytest.raises(BoundedError),
        consume_job_history(Budget(root, [], limits), closure, "sha256:" + "b" * 64),
    ):
        raise AssertionError("must never start downstream writes")


def test_pre_parent_body_revisions_keep_real_acquisition_without_inventing_cutoffs():
    from quanthus.pipelines.securities_warehouse.base22_outputs import parent_available_versions

    c, _, _ = load_contract(Path("config/pipelines"))
    rule = c["tables"]["dwd_disclosure"]
    row = announcement_observation(
        event(),
        company_id="c",
        security_id="s",
        parent_knowledge=NOW,
        source_sha256="hash",
        revision="r",
    )["dwd_disclosure"][0]
    first = datetime(2026, 8, 1, tzinfo=timezone.utc)
    second = first + timedelta(days=2)
    revised = dict(row, title="revision")
    output, evidence = parent_available_versions([(row, first), (revised, second)], rule)
    assert (
        len(output) == 1 and output[0]["title"] == "revision" and output[0]["knowledge_from"] == NOW
    )
    assert len(evidence[0]["original_observations"]) == 2
    with pytest.raises(SourceAssessmentError, match="EQUAL_SOURCE_CLOCK"):
        parent_available_versions([(row, first), (revised, first)], rule)


def test_financial_concept_cumulative_scope_matches_governed_revision_rule():
    from quanthus.pipelines.securities_warehouse.base22_outputs import financial_concept_versions

    base = dict(
        financial_concept_id="c",
        taxonomy_namespace="ns",
        taxonomy_version="r",
        concept_code="f",
        concept_name="field",
        statement_type="report",
        data_type="decimal",
        period_type="duration",
        balance_type=None,
        source_record_id="one",
        record_source="s",
        source_revision="r",
        source_artifact_sha256="hash",
        knowledge_from=OLD,
        knowledge_to=None,
        effective_from=OLD - timedelta(days=10),
        effective_to=None,
    )
    later = dict(
        base, knowledge_from=OLD + timedelta(days=1), effective_from=OLD - timedelta(days=5)
    )
    earlier = dict(
        base, knowledge_from=OLD + timedelta(days=2), effective_from=OLD - timedelta(days=20)
    )
    rows = financial_concept_versions([later, earlier, base])
    assert len(rows) == 2 and rows[0]["knowledge_to"] == earlier["knowledge_from"]
    assert rows[1]["effective_from"] == earlier["effective_from"]
    with pytest.raises(SourceAssessmentError):
        financial_concept_versions([base, dict(base, concept_name="conflicting_semantics")])


def test_source_shortest_decimal_is_not_silently_rounded_to_fit():
    from quanthus.pipelines.securities_warehouse.base22_outputs import decimal_observation

    assert decimal_observation(1.25) == Decimal("1.25")
    assert decimal_observation(None, nullable=True) is None
    for value in [1.00000000001, float("nan"), True]:
        with pytest.raises(SourceAssessmentError):
            decimal_observation(value)


def test_calendar_observation_preserves_unknown_acquisition_and_blocks_unqualified_venue():
    from quanthus.pipelines.securities_warehouse.base22_outputs import calendar_observation

    row = dict(trade_date="2026-08-03", exchange="SSE", is_open=1, built_at=OLD)
    venue = dict(venue_id="venue", mic_code="XSHG", knowledge_from=OLD)
    out = calendar_observation(
        row, venue=venue, source_sha256="hash", revision="r", warehouse_observed_at=NOW
    )
    calendar = out["dwd_trading_calendar"][0]
    assert calendar["knowledge_from"] == NOW and calendar["session_type"] == "REGULAR_DAY"
    with pytest.raises(SourceAssessmentError):
        calendar_observation(
            dict(row, exchange="BSE"),
            venue=venue,
            source_sha256="hash",
            revision="r",
            warehouse_observed_at=NOW,
        )


def test_market_state_observation_uses_source_flag_and_same_date_venue():
    from quanthus.pipelines.securities_warehouse.base22_outputs import market_observation

    raw, listing, calendar = tail()
    raw.update(
        date="2026-07-31",
        market_date="2026-07-31",
        acquired_at=NOW.isoformat(),
        isST="0",
        tradestatus="0",
    )
    out = market_observation(
        raw,
        listing=listing,
        calendar=calendar,
        source_sha256="hash",
        revision="r",
        family="daily_status_observation_delta",
    )["dwd_tradability_state"][0]
    assert (
        not out["is_tradable"]
        and not out["can_buy"]
        and out["reason_code"] == "SOURCE_NOT_TRADABLE"
    )
    calendar["trade_date"] = date(2026, 8, 3)
    with pytest.raises(SourceAssessmentError):
        market_observation(
            raw,
            listing=listing,
            calendar=calendar,
            source_sha256="hash",
            revision="r",
            family="daily_status_observation_delta",
        )


def test_revision_entity_key_orders_changed_publication_coordinates_without_fake_times():
    from quanthus.pipelines.securities_warehouse.base22_outputs import parent_available_versions

    c, _, _ = load_contract(Path("config/pipelines"))
    rule = c["tables"]["dwd_disclosure"]
    first = announcement_observation(
        event(),
        company_id="c",
        security_id="s",
        parent_knowledge=NOW,
        source_sha256="hash",
        revision="r",
    )["dwd_disclosure"][0]
    revised = dict(
        first,
        effective_at=first["effective_at"] + timedelta(hours=1),
        announced_at=first["announced_at"] + timedelta(hours=1),
    )
    a = datetime(2026, 8, 1, tzinfo=timezone.utc)
    b = a + timedelta(days=2)
    rows, evidence = parent_available_versions(
        [(first, a), (revised, b)], rule, revision_entity_key=["disclosure_id"]
    )
    assert len(rows) == 1 and rows[0]["effective_at"] == revised["effective_at"]
    assert rows[0]["knowledge_from"] == NOW and len(evidence) == 1


def test_holder_adapter_does_not_inherit_legacy_decimal_rounding():
    row = dict(
        code="000001",
        end_date="2026-06-30",
        shareholder_count="10",
        avg_hold_num="1.00000000001",
        acquired_at="2026-08-01T12:00:00+08:00",
    )
    with pytest.raises(SourceAssessmentError, match="PRECISION_CHANGED"):
        holder_observation(
            row,
            company_id="c",
            security_id="s",
            parent_knowledge=OLD,
            source_sha256="hash",
            revision="r",
        )
