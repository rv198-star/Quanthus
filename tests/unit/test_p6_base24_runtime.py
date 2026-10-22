from datetime import date, datetime

import pytest

from quanthus.forward_validation.base24_runtime import AfterCloseDecisionClock
from quanthus.forward_validation.contracts import ForwardValidationContractError
from tests.unit.test_base24_semantic_projection import view as view


def clock(**overrides):
    values = dict(
        market_date=date(2026, 10, 20),
        source_available_at=datetime.fromisoformat("2026-10-20T19:00:00+08:00"),
        decision_at=datetime.fromisoformat("2026-10-20T20:00:00+08:00"),
        next_session_open=datetime.fromisoformat("2026-10-21T09:30:00+08:00"),
    )
    values.update(overrides)
    return AfterCloseDecisionClock(**values)


def test_runtime_clock_keeps_observation_visibility_and_decision_distinct():
    value = clock()
    assert value.observation_end.hour == 15
    assert value.source_available_at.hour == 19
    assert value.decision_at.hour == 20
    assert (
        value.identity
        != clock(decision_at=datetime.fromisoformat("2026-10-20T20:01:00+08:00")).identity
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"decision_at": datetime.fromisoformat("2026-10-20T18:59:59+08:00")}, "not_yet_visible"),
        ({"decision_at": datetime.fromisoformat("2026-10-21T09:30:00+08:00")}, "missed_next_open"),
        ({"decision_at": datetime.fromisoformat("2026-10-21T14:00:00+08:00")}, "missed_next_open"),
        (
            {"next_session_open": datetime.fromisoformat("2026-10-21T10:00:00+08:00")},
            "missed_next_open",
        ),
        ({"source_available_at": datetime(2026, 10, 20, 19)}, "clock"),
        ({"market_date": date(2025, 12, 31)}, "market_scope"),
    ],
)
def test_runtime_clock_rejects_unavailable_backfill_and_ambiguous_instants(changes, reason):
    with pytest.raises(ForwardValidationContractError, match=reason):
        clock(**changes)


def test_runtime_clock_can_cross_midnight_without_backdating():
    value = clock(
        source_available_at=datetime.fromisoformat("2026-10-21T02:44:23+08:00"),
        decision_at=datetime.fromisoformat("2026-10-21T03:00:00+08:00"),
    )
    assert value.market_date == date(2026, 10, 20)
    assert value.decision_at.date() == date(2026, 10, 21)


@pytest.fixture
def runtime_source(view, tmp_path, monkeypatch):
    """Composed, already-qualified source boundary; no real data or activation."""
    import json
    from datetime import timedelta, timezone
    from types import MappingProxyType

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.backtest import base24_semantic_projection as projection
    from quanthus.forward_validation import base24_runtime
    from quanthus.forward_validation.base24_inputs import P6Base24QualifiedContext
    from quanthus.forward_validation.base24_runtime import VerifiedTargetDaily
    from quanthus.forward_validation.serialization import digest_value
    from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint, _tree
    from quanthus.pipelines.securities_warehouse.base24_p6_purpose import _schemas

    monkeypatch.setattr(
        base24_runtime, "_utcnow", lambda: datetime.fromisoformat("2026-10-01T00:00:01+00:00")
    )
    root = tmp_path / "qualified"
    root.mkdir()
    from decimal import Decimal

    from tests.unit.test_base24_semantic_projection import mutate

    view = mutate(
        view,
        "dwd_security_bar",
        lambda rows: [
            {
                **r,
                "open": Decimal("1.0000000001"),
                "high": Decimal("1.1000000001"),
                "low": Decimal("0.9000000001"),
                "close": Decimal("1.0000000001"),
                "volume": Decimal("100"),
                "turnover": Decimal("100"),
            }
            for r in rows
        ],
    )
    projection.build(view, root / "source")
    source_manifest = root / "source/manifest.json"
    source_meta = json.loads(source_manifest.read_bytes())
    source_meta["knowledge_cutoff"] = "2026-10-01T00:00:00+00:00"
    source_manifest.write_text(json.dumps(source_meta))
    tables = root / "source/tables"
    day = date(2026, 9, 30)
    dates = []
    cursor = day
    while len(dates) < 130:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()
    for name, field in [
        ("daily_ohlcv_none", "date"),
        ("daily_universe", "market_date"),
        ("price_limit_execution_daily", "execution_date"),
        ("trading_calendar", "trade_date"),
    ]:
        p = tables / (name + ".parquet")
        template = pq.read_table(p)
        one = template.to_pylist()[0]
        rows = []
        for i, d in enumerate(dates):
            row = {**one, field: d}
            if "calendar_session_id" in row:
                row["calendar_session_id"] = "c" + str(i)
            rows.append(row)
        pq.write_table(pa.Table.from_pylist(rows, schema=template.schema), p)
    q = root / "qualified"
    q.mkdir()
    state_schema, action_schema = _schemas()
    states = [
        dict(
            listing_id="l",
            security_id="s",
            market_date=d,
            status="NORMAL",
            status_basis="PROVIDER_STATE",
            warmup_eligible=True,
            execution_source_qualified=True,
            qualified_entry_allowed=True,
            reason_code="QUALIFIED",
            execution_authorized=False,
            evidence_digest="sha256:" + "1" * 64,
        )
        for d in dates
    ]
    state = pa.Table.from_pylist(states, schema=state_schema)
    pq.write_table(state, q / "execution.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=action_schema), q / "actions.parquet")
    (root / "manifest.json").write_text("{}")
    context = P6Base24QualifiedContext(
        root,
        "sha256:" + "2" * 64,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        "{}",
        MappingProxyType({p: _fingerprint(root / p) for p in _tree(root)}),
        ("qualified/execution.parquet",),
    )
    core = tmp_path / "core"
    core.mkdir()
    (core / "manifest.json").write_text("{}")
    evidence = tmp_path / "raw-evidence"
    evidence.mkdir()
    status = evidence / "status"
    status.write_text("synthetic")
    release = evidence / "release"
    release.write_text("{}")
    target = VerifiedTargetDaily(
        json.dumps({"market_date": str(day)}),
        core,
        status,
        release,
        MappingProxyType(
            {str(p): _fingerprint(p) for p in [core / "manifest.json", status, release]}
        ),
        frozenset({"manifest.json"}),
    )
    industry = tmp_path / "industry-source" / "mapping.json"
    industry.parent.mkdir()
    m = {
        "schema_version": "1.0.0",
        "mapping_version": "static-level1-concentration-research-v1",
        "usage": "static_non_pit_concentration_only",
        "source": {
            "provider": "synthetic",
            "endpoint": "fixture",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
            "source_revision": "v1",
        },
        "memberships": [
            {
                "security_id": "s",
                "stock_code": "000001",
                "exchange": "SZSE",
                "level1_industry_id": "i",
            }
        ],
    }
    m["artifact_digest"] = digest_value(m)
    industry.write_text(json.dumps(m))
    return context, target, state.slice(129, 1), industry


def build_runtime(runtime_source, tmp_path):
    from quanthus.forward_validation.base24_runtime import materialize_runtime_inputs
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    context, target, state, industry = runtime_source
    root = tmp_path / "runtime"
    result = materialize_runtime_inputs(
        context,
        target=target,
        target_state=state,
        industry_mapping=industry,
        industry_sha256=file_sha(industry),
        output=root,
        code_revision="a" * 40,
    )
    return root, result


def test_runtime_materialization_portable_real_feature_bundle_and_age(runtime_source, tmp_path):
    import shutil

    import pyarrow.parquet as pq

    from quanthus.forward_validation.base24_runtime import open_runtime_inputs

    root, result = build_runtime(runtime_source, tmp_path)
    assert result["context_listings"] == result["qualified_entry_listings"] == 1
    age = pq.read_table(root / "candidate-input.parquet")["listing_sessions"].to_pylist()
    assert min(age) == 70 and max(age) == 130
    # Source clock is retained as October 1, rather than backdated to September 30.
    decision = clock(
        market_date=date(2026, 9, 30),
        source_available_at=datetime.fromisoformat("2026-10-01T00:00:01+00:00"),
        decision_at=datetime.fromisoformat("2026-10-01T08:01:00+08:00"),
        next_session_open=None,
    )
    for index in range(2):
        new = tmp_path / ("moved" + str(index))
        shutil.copytree(root, new)
        shutil.rmtree(root)
        root = new
        opened = open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"])
        bundle = opened.bundle(decision)
        assert bundle.snapshot.knowledge_cutoff == decision.decision_at
        assert len(bundle.datasets) == 7 and not bundle.execution_authorized
    shutil.rmtree(runtime_source[0].root)
    assert open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"]).bundle(decision)


@pytest.mark.parametrize("mode", ["missing", "bytes", "extra", "symlink"])
def test_runtime_physical_failure_no_fallback(runtime_source, tmp_path, mode):
    from quanthus.forward_validation.base24_runtime import open_runtime_inputs
    from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError

    root, result = build_runtime(runtime_source, tmp_path)
    p = root / "candidate-input.parquet"
    if mode == "missing":
        p.unlink()
    elif mode == "bytes":
        p.write_bytes(b"corrupt")
    elif mode == "extra":
        (root / "unexpected").write_bytes(b"")
    else:
        saved = tmp_path / "saved"
        p.rename(saved)
        p.symlink_to(saved)
    with pytest.raises((ForwardValidationContractError, SourceAssessmentError, FileNotFoundError)):
        open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"])


@pytest.mark.parametrize("mode", ["duplicate", "late_date", "entry_flag", "wrong_venue"])
def test_repacked_runtime_semantic_failure(runtime_source, tmp_path, mode):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.forward_validation.base24_runtime import open_runtime_inputs
    from quanthus.forward_validation.serialization import digest_value
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    root, _ = build_runtime(runtime_source, tmp_path)
    relative = (
        "tables/price_limit_execution_daily.parquet"
        if mode == "entry_flag"
        else "tables/daily_ohlcv_none.parquet"
    )
    p = root / relative
    table = pq.read_table(p)
    rows = table.to_pylist()
    if mode == "duplicate":
        rows.append(rows[0])
    elif mode == "late_date":
        rows[0]["date"] = date(2026, 10, 1)
    elif mode == "wrong_venue":
        rows[0]["dwd_venue_id"] = "other"
    else:
        rows[0]["entry_policy_allows"] = True
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
    m = json.loads((root / "manifest.json").read_bytes())
    m["files"][relative] = {
        "sha256": file_sha(p),
        "bytes": p.stat().st_size,
        "rows": len(rows),
        "schema_sha256": digest_value(str(table.schema)),
    }
    (root / "manifest.json").write_text(json.dumps(m))
    sha = file_sha(root / "manifest.json")
    (root / "READY").write_text(sha)
    with pytest.raises(ForwardValidationContractError, match="runtime_semantics"):
        open_runtime_inputs(root, manifest_sha256=sha)


def test_runtime_sealed_output_and_changed_input_are_not_reused(runtime_source, tmp_path):
    from quanthus.forward_validation.base24_runtime import materialize_runtime_inputs
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    root, _ = build_runtime(runtime_source, tmp_path)
    context, target, state, industry = runtime_source
    with pytest.raises(ForwardValidationContractError, match="output_exists"):
        materialize_runtime_inputs(
            context,
            target=target,
            target_state=state,
            industry_mapping=industry,
            industry_sha256=file_sha(industry),
            output=root,
            code_revision="a" * 40,
        )
    (context.root / "unexpected").write_text("new")
    with pytest.raises(ForwardValidationContractError, match="member_changed"):
        context.check_unchanged()


def test_new_clock_event_round_trip_keeps_legacy_close_rule():
    import json
    from dataclasses import replace

    from quanthus.event_library.contracts import (
        EventContractError,
        SplitAssignment,
        event_record_from_dict,
    )
    from tests.unit.test_event_library_contracts import EventLibraryContractTests

    old = EventLibraryContractTests()._record()
    assert "prospective_clock" not in old.to_dict()
    assert old.decision_at == old.signal_close
    time = clock()
    identity = replace(old.identity, signal_date=time.market_date)
    kwargs = dict(
        identity=identity,
        signal_close=time.observation_end,
        knowledge_cutoff=time.source_available_at,
        split_assignment=SplitAssignment.PROSPECTIVE,
    )
    with pytest.raises(EventContractError, match="after T close"):
        replace(old, **kwargs)
    new = replace(old, **kwargs, prospective_clock_json=json.dumps(time.to_dict(), sort_keys=True))
    assert new.decision_at == time.decision_at
    assert event_record_from_dict(new.to_dict()) == new
    with pytest.raises(EventContractError, match="does not bind"):
        replace(new, split_assignment=SplitAssignment.DEVELOPMENT)
    tampered = new.to_dict()
    tampered["prospective_clock"]["schema_version"] = "other"
    with pytest.raises(EventContractError, match="invalid prospective"):
        event_record_from_dict(tampered)


def test_candidate_scanner_opt_in_after_close_preserves_numeric_rule_and_legacy():
    from dataclasses import replace
    from datetime import timedelta

    from quanthus.event_library.scan import scan_forward_candidate_series
    from quanthus.forward_validation.base24_runtime import P6Base24CandidatePolicy
    from quanthus.forward_validation.prospective_source import (
        P6ProspectiveSourcePolicy,
        P6ProspectiveSplitPolicy,
    )
    from tests.unit.test_event_scan import CARD, _series

    time = clock()
    old = _series("000001", signal_indices=(200,))
    days = [time.market_date - timedelta(days=21 - i) for i in range(22)]
    rows = [
        replace(
            r,
            session_date=d,
            close_at=time.observation_end.replace(year=d.year, month=d.month, day=d.day),
            knowledge_cutoff=time.source_available_at,
        )
        for r, d in zip(old, days, strict=True)
    ]
    split = P6ProspectiveSplitPolicy(days[0] - timedelta(days=1), time.market_date)
    policies = [
        P6ProspectiveSourcePolicy(
            days[0] - timedelta(days=1), time.market_date, days[0], "sha256:" + "a" * 64
        ),
        P6Base24CandidatePolicy(time, days[0], "sha256:" + "b" * 64),
    ]
    results = [
        scan_forward_candidate_series(
            (rows,),
            trading_dates=(*days, time.market_date + timedelta(days=1)),
            snapshot_id="synthetic",
            snapshot_manifest_digest="sha256:" + "c" * 64,
            card=CARD,
            split_policy=split,
            source_policy=p,
        )
        for p in policies
    ]
    assert not results[0].candidates
    assert results[0].counters.reason_counts["knowledge_after_t_close"] == 1
    assert len(results[1].candidates) == 1
    event = results[1].candidates[0].record
    assert event.signal_close.hour == 15 and event.decision_at.hour == 20
    assert event.knowledge_cutoff.hour == 19


@pytest.mark.parametrize("mode", ["ok", "revision", "release", "member", "date", "mutation"])
def test_target_daily_exact_original_binding(runtime_source, tmp_path, monkeypatch, mode):
    import json
    from dataclasses import replace
    from types import MappingProxyType, SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.forward_validation.base24_runtime import verify_target_daily
    from quanthus.pipelines.securities_warehouse import daily_core_domain
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint, _tree

    context, target, _, _ = runtime_source
    day = "2026-09-30"
    chosen = "sha256:" + "b" * 64
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    stock_code="000001",
                    market_date=day,
                    date=("2026-10-01" if mode == "date" else day),
                    isST="0",
                    tradestatus="1",
                    acquired_at=day + "T19:00:00+08:00",
                )
            ]
        ),
        target.status,
    )
    target.release.write_text(
        json.dumps(
            {
                "market_date": day,
                "release_id": "r",
                "artifacts": [
                    {
                        "name": "daily_status_observation_delta",
                        "sha256": file_sha(target.status)
                        if mode != "member"
                        else "sha256:" + "0" * 64,
                        "size_bytes": target.status.stat().st_size,
                    }
                ],
            }
        )
    )
    manifest = {
        "kind": "cn01_daily_core_dwd_increment",
        "consumable_intervals": ["1d"],
        "selected_releases": {
            "market_daily_core": {
                "manifest_sha256": file_sha(target.release)
                if mode != "release"
                else "sha256:" + "0" * 64,
                "release_id": "r",
            }
        },
    }
    (target.core_root / "manifest.json").write_text(json.dumps(manifest))
    (context.root / "source/source-evidence.json").write_text(
        json.dumps(
            {
                "selected_input_view": {
                    "Daily": [
                        {
                            "market_date": day,
                            "core_manifest_sha256": chosen if mode != "revision" else "wrong",
                        }
                    ]
                }
            }
        )
    )
    context = replace(
        context,
        fingerprints=MappingProxyType(
            {p: _fingerprint(context.root / p) for p in _tree(context.root)}
        ),
    )
    monkeypatch.setattr(
        daily_core_domain,
        "verify_daily_domain_increment",
        lambda _: SimpleNamespace(
            manifest_sha256=chosen,
            market_date=date.fromisoformat(day),
            content_digest="digest",
            package_id="p",
        ),
    )

    def verify():
        return verify_target_daily(
            context,
            core_root=target.core_root,
            core_manifest_sha256=chosen,
            release=target.release,
            status=target.status,
        )

    if mode in ("ok", "mutation"):
        bound = verify()
        if mode == "mutation":
            target.status.write_bytes(b"changed")
            with pytest.raises(ForwardValidationContractError, match="target_changed"):
                bound.check_unchanged()
        else:
            assert bound.identity()["status_sha256"] == file_sha(target.status)
    else:
        with pytest.raises(
            ForwardValidationContractError,
            match={
                "revision": "target_revision",
                "release": "status_release",
                "member": "status_member",
                "date": "status_scope",
            }[mode],
        ):
            verify()


def test_held_target_keeps_feature_context_but_never_produces_entry_input(runtime_source, tmp_path):
    import json

    import pyarrow as pa

    from quanthus.forward_validation.base24_runtime import open_runtime_inputs

    context, target, state, industry = runtime_source
    values = state.to_pylist()
    values[0].update(
        execution_source_qualified=False,
        qualified_entry_allowed=False,
        reason_code="STATUS_UNKNOWN",
    )
    state = pa.Table.from_pylist(values, schema=state.schema)
    root, result = build_runtime((context, target, state, industry), tmp_path)
    assert result["context_listings"] == 1 and result["qualified_entry_listings"] == 0
    opened = open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"])
    time = clock(
        market_date=date(2026, 9, 30),
        source_available_at=datetime.fromisoformat(
            json.loads(opened.manifest_json)["source_available_at"]
        ),
        decision_at=datetime.fromisoformat("2026-10-01T08:01:00+08:00"),
        next_session_open=None,
    )
    assert list(opened.candidate_series(time)) == []
    assert not opened.context_bundle().execution_authorized


def test_actual_feature_engine_reads_the_portable_seven_table_adapter(runtime_source, tmp_path):
    from pathlib import Path

    from quanthus.feature_registry import load_feature_registry
    from quanthus.forward_validation.base24_runtime import open_runtime_inputs

    root, result = build_runtime(runtime_source, tmp_path)
    opened = open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"])
    artifact = opened.materialize_feature_panel(
        registry=load_feature_registry(Path("config/p3-baseline-features.research.v1.json")),
        output=tmp_path / "features.parquet",
    )
    assert artifact.report.row_count == 61
    assert artifact.report.data_manifest_sha256 == result["manifest_sha256"]


def test_native_candidate_adapter_keeps_decimal_clock_market_and_listing_age(
    runtime_source, tmp_path
):
    import json
    from decimal import Decimal

    from quanthus.forward_validation.base24_runtime import open_runtime_inputs

    root, result = build_runtime(runtime_source, tmp_path)
    opened = open_runtime_inputs(root, manifest_sha256=result["manifest_sha256"])
    value = clock(
        market_date=date(2026, 9, 30),
        source_available_at=datetime.fromisoformat(
            json.loads(opened.manifest_json)["source_available_at"]
        ),
        decision_at=datetime.fromisoformat("2026-10-01T08:01:00+08:00"),
        next_session_open=None,
    )
    rows = list(opened.candidate_series(value))
    assert len(rows) == 1 and len(rows[0]) == 61
    assert rows[0][-1].close_price == Decimal("1.0000000001")
    assert rows[0][-1].venue_id == "SZSE"
    assert rows[0][-1].listing_sessions == 130
    assert rows[0][-1].knowledge_cutoff == value.source_available_at
    assert rows[0][-1].close_at.hour == 15
