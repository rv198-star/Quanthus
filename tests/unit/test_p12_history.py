"""Bounded history reader (issue #749) - synthetic-fixture tests.

Builds small synthetic parquet members + a stub SourceMap, then checks
the reader emits a p11.raw-history-input.v1 envelope the strict loader
shape accepts, keeps the three clocks separate, carries UNKNOWN for
unproven coverage, and never fabricates bars / listing age / a member.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.p12_history import reader as reader_mod
from quanthus.p12_history.source_pins import ResolvedMember, SourceMap

UTC = timezone.utc
CUTOFF = datetime(2026, 11, 24, 22, 30, tzinfo=UTC)


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _member(root: Path, rel: str, package_id: str = "test-base", kind: str = "base",
            market_date: str | None = None, declared_sha256: str | None = None) -> ResolvedMember:
    p = root / rel
    # Real sha256 of the member bytes (the audit rejected the earlier
    # placeholder "a"*64 which made every member SHA_MISMATCH and left an
    # empty universe).  A caller may pass declared_sha256 to stage a
    # deliberate-wrong-digest contrast; otherwise hash the real file.
    if declared_sha256 is None:
        declared_sha256 = (
            "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
            if p.exists() else "sha256:" + "0" * 64
        )
    return ResolvedMember(
        member_id=rel,
        package_id=package_id,
        version="2.5.1",
        abs_path=p,
        sha256=declared_sha256,
        size_bytes=p.stat().st_size if p.exists() else 0,
        readability="READABLE",
        package_kind=kind,
        market_date=market_date,
    )


class StubSourceMap(SourceMap):
    """A SourceMap that serves hand-built members instead of the frozen package."""

    def __init__(self, members_by_table, daily_cores):
        self._members_by_table = members_by_table
        self._daily = daily_cores

    def base_members(self, table):
        return list(self._members_by_table.get(table, ()))

    def daily_open_days(self):
        return sorted(self._daily)

    def daily_session_class(self, market_date):
        return "OPEN" if market_date in self._daily else None

    def daily_member(self, market_date, name_fragment):
        for m in self._daily.get(market_date, ()):
            if name_fragment in m.member_id:
                return m
        return None

    def daily_core(self, market_date):
        return None  # members resolved via daily_member

    def resolve(self, member_id):
        for members in self._members_by_table.values():
            for m in members:
                if m.member_id == member_id or str(m.abs_path) == member_id:
                    return m
        for members in self._daily.values():
            for m in members:
                if m.member_id == member_id or str(m.abs_path) == member_id:
                    return m
        return None


def _calendar_table(days, venue="SSE"):
    return pa.table({
        "venue_id": [venue] * len(days),
        "trade_date": days,
        "is_open": [True] * len(days),
        "open_at": [datetime(d.year, d.month, d.day, 1, 30, tzinfo=UTC) for d in days],
        "close_at": [datetime(d.year, d.month, d.day, 15, 0, tzinfo=UTC) for d in days],
        "observation_start": [datetime(d.year, d.month, d.day, 9, 30, tzinfo=UTC) for d in days],
        "knowledge_from": [datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC) for d in days],
    })


def _listing_table():
    return pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000"],
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 1, 1, 0, 0, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [datetime(2026, 1, 1, 0, 0, tzinfo=UTC)],
    })


def _security_table():
    return pa.table({
        "security_id": ["security-600000"],
        "company_id": ["company-1"],
        "instrument_type": ["common_equity"],
        "effective_from": [datetime(2026, 1, 1, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
    })


def _bar_table(days, listing="listing-600000"):
    n = len(days)
    return pa.table({
        "source_record_id": [f"src/code=600000/{d}" for d in days],
        "observation_end": [datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC) for d in days],
        "listing_id": [listing] * n,
        "calendar_session_id": [f"cal-{d}" for d in days],
        "interval_size": ["1d"] * n,
        "price_basis": ["UNADJUSTED"] * n,
        "currency": ["CNY"] * n,
        "open": [10.0 + i for i in range(n)],
        "high": [11.0 + i for i in range(n)],
        "low": [9.0 + i for i in range(n)],
        "close": [10.5 + i for i in range(n)],
        "volume": [1000.0] * n,
        "turnover": [10500.0] * n,
        "source_revision": ["rev-1"] * n,
        "record_source": ["primary"] * n,
        "knowledge_from": [datetime(d.year, d.month, d.day, 8, 0, tzinfo=UTC) for d in days],
        "knowledge_to": pa.array([None] * n, type=pa.timestamp("us", tz="UTC")),
    })


def _sessions(n, end=date(2026, 11, 24)):
    # consecutive open sessions ending at `end` (calendar treats all as open)
    from datetime import timedelta
    return [end - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _build_map(tmp_path: Path, n_sessions=130, with_bars=True, with_listing=True):
    # calendar covers the listing start (2026-01-01) through T so membership
    # is provable; bars use the trailing n_sessions of the same calendar.
    cal_days = _sessions(330)
    members = {}
    cal = tmp_path / "cal.parquet"; _write(cal, _calendar_table(cal_days))
    members["dwd_trading_calendar"] = [_member(tmp_path, "cal.parquet")]
    if with_listing:
        p = tmp_path / "listing.parquet"; _write(p, _listing_table())
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    for table, tbl in {
        "dwd_security": _security_table(),
        "dwd_security_identifier": pa.table({
            "security_id": ["security-600000"], "identifier_namespace": ["code"],
            "identifier_value": ["600000"], "venue_id": ["SSE"]}),
        "dwd_trading_venue": pa.table({"venue_id": ["SSE"], "mic_code": ["XSHG"]}),
        "dwd_listed_company": pa.table({"company_id": ["company-1"], "display_name": ["浦发银行"]}),
        "dwd_company_industry_membership": pa.table({
            "company_id": ["company-1"], "industry_node_id": ["ind-bank"],
            "membership_type": ["primary"],
            "effective_from": [datetime(2026,1,1,tzinfo=UTC)],
            "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC"))}),
        "dwd_industry_node": pa.table({}),
    }.items():
        p = tmp_path / f"{table}.parquet"
        _write(p, tbl)
        members[table] = [_member(tmp_path, f"{table}.parquet")]
    if with_bars:
        # Write the member bytes FIRST so _member hashes the real file
        # (the prior order declared sha256 on a not-yet-written path ->
        # placeholder digest -> SHA_MISMATCH -> empty bars).
        _write(tmp_path / "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet", _bar_table(cal_days))
        members["dwd_security_bar"] = [
            _member(tmp_path, "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet")
        ]
    for t in ("dwd_corporate_action", "dwd_corporate_action_term", "dwd_quality_exception",
              "dwd_observation_gap", "dwd_price_limit_regime", "dwd_tradability_state"):
        members.setdefault(t, [])
    return StubSourceMap(members, {})


def test_envelope_shape_and_membership(tmp_path):
    smap = _build_map(tmp_path, n_sessions=130)
    excl = tmp_path / "excl-empty.json"
    excl.write_text(json.dumps({"securities": [], "excluded_dates": []}))
    import hashlib as _hl
    _sha = "sha256:" + _hl.sha256(excl.read_bytes()).hexdigest()
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
        replay_exclusion={"path": excl, "sha256": _sha})
    rh = res.raw_history
    assert rh["schema_version"] == "p11.raw-history-input.v1"
    assert rh["market_date"] == "2026-11-24"
    assert rh["decision_cutoff"] == "2026-11-24T22:30:00Z"
    assert len(rh["securities"]) == 1
    sec = rh["securities"][0]
    assert sec["identity"]["security_id"] == "security-600000"
    assert sec["identity"]["stock_code"] == "600000"
    # 61-session window: bars are bounded, never the full 130
    assert len(sec["bars"]) == 61
    assert sec["bars"][0]["security_id"] == "security-600000"
    # listing instant preserved (native timestamp, not a bare date)
    assert "T" in rh["listing_rows"][0]["effective_from"]
    # three clocks separated
    assert res.coverage["visible_at_label"] == "ENGINEERING_REPLAY_AS_OBSERVED"
    assert res.read_at == "2026-11-24T23:00:00Z"


def test_open_at_is_native_not_observation_start(tmp_path):
    # 协调/原件: 09-30 open_at=null (exchange gave no native open) vs
    # observation_start=09-29T16:00Z; 10-08 open_at=01:30Z vs
    # observation_start=10-07T16:00Z.  envelope.open_at must carry the
    # NATIVE open_at column - never the observation_start instant.
    from quanthus.p12_history import calendar_listing as cal_mod
    from quanthus.p12_history.envelope import build_envelope
    from quanthus.p12_history.calendar_listing import CalendarRow

    # Direct row-level check: CalendarRow keeps open_at distinct.
    rows = [
        CalendarRow(venue_id="SSE", session_date=date(2026, 9, 30),
                    is_open=True, open_at=None, close_at=None,
                    observation_start=datetime(2026, 9, 29, 16, 0, tzinfo=UTC),
                    knowledge_from=datetime(2026, 10, 8, 0, 0, tzinfo=UTC),
                    source_member_id="part-000039"),
        CalendarRow(venue_id="SSE", session_date=date(2026, 10, 8),
                    is_open=True,
                    open_at=datetime(2026, 10, 8, 1, 30, tzinfo=UTC),
                    close_at=datetime(2026, 10, 8, 7, 0, tzinfo=UTC),
                    observation_start=datetime(2026, 10, 7, 16, 0, tzinfo=UTC),
                    knowledge_from=datetime(2026, 10, 8, 0, 0, tzinfo=UTC),
                    source_member_id="part-000040"),
    ]
    env = build_envelope(
        market_date=date(2026, 10, 8),
        decision_cutoff=datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_member_entries=[], calendar_rows=rows,
        listing_rows=[], securities=[],
    )
    cal = {r["session_date"]: r for r in env["calendar_rows"]}
    # 09-30: open_at stays null (not back-filled from observation_start)
    assert cal["2026-09-30"]["open_at"] is None
    assert cal["2026-09-30"]["observation_start"] == "2026-09-29T16:00:00Z"
    # 10-08: open_at is the real 01:30Z open, not the 10-07 observation_start
    assert cal["2026-10-08"]["open_at"] == "2026-10-08T01:30:00Z"
    assert cal["2026-10-08"]["observation_start"] == "2026-10-07T16:00:00Z"
    # close_at and knowledge_from preserved natively too
    assert cal["2026-10-08"]["close_at"] == "2026-10-08T07:00:00Z"


def test_missing_listing_leaves_membership_unproven(tmp_path):
    smap = _build_map(tmp_path, with_listing=False)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        purpose="production_daily")
    # no listing rows -> P11 derives no membership; reader does not invent one
    assert res.raw_history["listing_rows"] == []


def test_missing_bars_yield_empty_bars_not_fabrication(tmp_path):
    smap = _build_map(tmp_path, with_bars=False)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        purpose="production_daily")
    assert res.raw_history["securities"][0]["bars"] == []


def test_bar_window_is_last_61_sessions(tmp_path):
    smap = _build_map(tmp_path, n_sessions=200)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        purpose="production_daily")
    bars = res.raw_history["securities"][0]["bars"]
    assert len(bars) == 61
    # last session is the market date itself
    assert bars[-1]["session_date"] == "2026-11-24"


class TestActionAuthorityHandoff:
    '''#749 action-authority handoff: per-security-day action_coverage is
    rebuilt from the real, once-verified P10 members via
    build_signed_action_authority - never copied or fabricated.'''

    def _members(self):
        # Minimal manifest + member bytes sufficient for load_release /
        # _batch_member_keys to resolve the batch + shared members.
        return {
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/RESULT.json": b'{"batch":"SSE-02-R2"}',
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/events.json": b'{"events":[]}',
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/security-windows.json": b'{"windows":[]}',
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/coverage.json": b'{"coverage":1}',
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/exception-scope.json": b'{"x":1}',
            "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2/issuer-index-crosswalk.json": b'{"x":1}',
            "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/terms-740-897-v2/ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json": b'{"a":1}',
            "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/terms-740-897-v2/WINDOW-USE.json": b'{"w":1}',
        }

    def _release_tree(self, tmp_path):
        # Build a fake reader-package tree whose MANIFEST declares members
        # under source_prefix, with real member bytes on disk.
        import json, hashlib
        members = self._members()
        manifest = {
            "release": "company-actions-20261125-b02",
            "source_prefix": str(tmp_path / "src"),
            "members": {
                k: {"sha256": hashlib.sha256(v).hexdigest(), "bytes": len(v)}
                for k, v in members.items()
            },
        }
        pkg = tmp_path / "reader-packages" / "company-actions-20261125-b02"
        pkg.mkdir(parents=True)
        (pkg / "MANIFEST.json").write_text(json.dumps(manifest))
        src = tmp_path / "src"
        for k, v in members.items():
            p = src / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(v)
        return tmp_path / "reader-packages"

    def test_build_day_resolves_batch_and_shared_members(self, tmp_path, monkeypatch):
        from quanthus.p12_history import action_authority_749 as m

        reader_root = self._release_tree(tmp_path)
        captured = {}

        def fake_authority(**kw):
            captured.update(kw)
            return {"action_coverage": "REBUILT", "_keys": sorted(kw["members"].keys())}

        def fake_bind(bts, sha):
            return {"manifest_sha256": sha, "bound": True}

        monkeypatch.setitem(
            __import__("sys").modules,
            "quanthus.product_daily.recommendation.paper_replay",
            type("M", (), {
                "build_signed_action_authority": staticmethod(fake_authority),
                "bind_signed_manifest": staticmethod(fake_bind),
            }),
        )
        out = m.build_day("600000", "2026-10-08", reader_root=reader_root)
        # The authority was invoked with the batch RESULT member, the shared
        # accepted + windows index members, and the frozen identity.
        assert captured["result_member"].endswith("batch-02-spdb-r2/RESULT.json")
        assert captured["accepted_member"].endswith("ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json")
        assert captured["index_member"].endswith("WINDOW-USE.json")
        assert captured["signal_date"] == "2026-10-08"
        assert captured["security_id"] == "security_2e02245cf8b28319ec8af0e9554cdc24"
        # The batch member bytes + the two shared members were actually read.
        members_read = out["members_read"]
        assert any("batch-02-spdb-r2/RESULT.json" in x for x in members_read)
        assert any("WINDOW-USE.json" in x for x in members_read)
        assert out["action_coverage"]["action_coverage"] == "REBUILT"


def test_shared_calendar_missing_yields_day_block_not_crash(tmp_path):
    # A shared hard dependency (venue calendar) whose bytes are unreadable
    # must emit a named whole-day block receipt, not FileNotFoundError and
    # not a silent READ.
    smap = _build_map(tmp_path, n_sessions=130)
    (tmp_path / "cal.parquet").unlink()
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap, purpose="production_daily")
    assert res.status == "INPUT_BLOCKED"
    block = res.coverage["day_block"]
    assert block["reason_code"] == "SHARED_CALENDAR_UNAVAILABLE"
    assert block["affected_scope"] == "whole_day"
    assert "cal.parquet" in block["failed_members"]


def test_shared_calendar_bad_sha_yields_day_block(tmp_path):
    # A shared calendar member whose declared sha is wrong is a hard
    # fault -> named whole-day block, never silently READ with 0 rows.
    smap = _build_map(tmp_path, n_sessions=130)
    bad = _member(tmp_path, "cal.parquet", declared_sha256="sha256:" + "b" * 64)
    smap._members_by_table["dwd_trading_calendar"] = [bad]
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap, purpose="production_daily")
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SHARED_CALENDAR_UNAVAILABLE"


def test_bars_carry_member_and_source_provenance(tmp_path):
    smap = _build_map(tmp_path, n_sessions=130)
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap, purpose="production_daily")
    bar = res.raw_history["securities"][0]["bars"][0]
    assert bar["member_id"] == (
        "payload/dwd_security_bar/interval_size=1d/"
        "observation_start_year=2026/observation_start_month=11/part-0.parquet"
    )
    assert bar["package_id"] == "test-base"
    assert bar["source_record_id"].startswith("src/code=600000/")


def test_account_roles_separated_on_universe(tmp_path):
    smap = _build_map(tmp_path, n_sessions=130)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        holdings=[{"security_id": "security-600000", "quantity": 100},
                  {"security_id": "outside-held", "quantity": None}],
        pending=[{"security_id": "outside-pending", "pending_kind": "sell",
                  "pending_quantity": 20}],
        purpose="production_daily")
    uni = res.coverage["universe"]
    assert "security-600000" in uni["held_in_pool"]
    assert "outside-held" in uni["held_unknown_quantity"]
    assert "outside-pending" in uni["pending_only"]
    assert uni["pending_typed"][0]["pending_kind"] == "sell"


def test_future_listing_and_industry_not_admitted(tmp_path):
    """749-03: a future-effective listing/industry never replaces the current."""
    smap = _build_map(tmp_path)
    # Add a future listing + a future industry row for the same security.
    fut_listing = tmp_path / "listing.parquet"
    base_tbl = _listing_table()
    extra = pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000-future"],
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 11, 27, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [datetime(2026, 11, 25, tzinfo=UTC)],
    })
    import pyarrow.parquet as pq
    combined = pa.concat_tables([base_tbl, extra])
    pq.write_table(combined, fut_listing)
    # Rebuild map so the listing member re-hashes the new bytes.
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    ind = tmp_path / "dwd_company_industry_membership.parquet"
    ind_tbl = pa.concat_tables([
        pa.table({
            "company_id": ["company-1"], "industry_node_id": ["ind-bank"],
            "membership_type": ["primary"],
            "effective_from": [datetime(2026,1,1,tzinfo=UTC)],
            "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
            "knowledge_from": [datetime(2026,1,1,tzinfo=UTC)]}),
        pa.table({
            "company_id": ["company-1"], "industry_node_id": ["ind-future"],
            "membership_type": ["primary"],
            "effective_from": [datetime(2026,11,27,tzinfo=UTC)],
            "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
            "knowledge_from": [datetime(2026,11,25,tzinfo=UTC)]}),
    ])
    pq.write_table(ind_tbl, ind)
    members["dwd_company_industry_membership"] = [_member(tmp_path, "dwd_company_industry_membership.parquet")]
    smap2 = StubSourceMap(members, {})
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap2,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    secs = [x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000"]
    assert len(secs) == 1
    assert secs[0]["identity"]["listing_id"] == "listing-600000"
    assert secs[0]["identity"]["industry_id"] == "ind-bank"


def test_retired_at_open_boundary_not_candidate(tmp_path):
    """749-03: a listing retired at the open boundary (effective_to <= T) drops."""
    smap = _build_map(tmp_path)
    lp = tmp_path / "listing.parquet"
    retired = pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000"],
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 1, 1, tzinfo=UTC)],
        "effective_to": [datetime(2026, 11, 20, tzinfo=UTC)],  # ended before T
        "knowledge_from": [datetime(2026, 1, 1, tzinfo=UTC)],
    })
    pq.write_table(retired, lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    assert all(x["identity"]["security_id"] != "security-600000" for x in res.raw_history["securities"])


def test_duplicate_security_id_collapses(tmp_path):
    """749-03: two in-force listings with the same effective_from are ambiguous - block the security, name it."""
    smap = _build_map(tmp_path)
    lp = tmp_path / "listing.parquet"
    dup = pa.concat_tables([_listing_table(), pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000-b"],  # second listing, same window
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 1, 1, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [datetime(2026, 1, 1, tzinfo=UTC)],
    })])
    pq.write_table(dup, lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    ids = [x["identity"]["security_id"] for x in res.raw_history["securities"]]
    # Two listing rows in-force at the same instant on different listing_id
    # is a genuine ambiguity: the security is blocked from new candidates
    # and named, never silently resolved by physical source order.
    assert ids.count("security-600000") == 0
    assert "security-600000" in res.coverage["universe"]["ambiguous_security_ids"]


def test_overlapping_listing_resolves_to_latest(tmp_path):
    """749-03: overlapping intervals on distinct listing_id are ambiguous - block, never auto-pick later."""
    smap = _build_map(tmp_path)
    lp = tmp_path / "listing.parquet"
    dup = pa.concat_tables([_listing_table(), pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000-new"],  # newer listing, later effective_from
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 6, 1, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [datetime(2026, 1, 1, tzinfo=UTC)],
    })])
    # Retire the old listing before the new one's start so both read validly but only new is latest-in-force.
    old = _listing_table().to_pandas()
    pq.write_table(dup, lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    secs = [x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000"]
    # Two distinct listing_ids both cover the open with different
    # effective_from: no named version proves one is live -> ambiguous,
    # blocked from new candidates regardless of source order.
    assert len(secs) == 0
    assert "security-600000" in res.coverage["universe"]["ambiguous_security_ids"]


def test_late_bar_revision_preserved(tmp_path):
    """749-02: a late correction row for the same session is kept in bar_revisions."""
    smap = _build_map(tmp_path)
    # Re-write the bar member with an extra late-correction row for the last day.
    bar_path = tmp_path / "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet"
    days = _sessions(330)
    base = _bar_table(days)
    last = days[-1]
    extra = pa.table({
        "source_record_id": [f"src/code=600000/{last}-rev2"],
        "observation_end": [datetime(last.year, last.month, last.day, 16, 0, tzinfo=UTC)],
        "listing_id": ["listing-600000"],
        "calendar_session_id": [f"cal-{last}"],
        "interval_size": ["1d"], "price_basis": ["UNADJUSTED"], "currency": ["CNY"],
        "open": [25.0], "high": [26.0], "low": [24.0], "close": [25.5],
        "volume": [2000.0], "turnover": [51000.0],
        "source_revision": ["rev-2"],
        "record_source": ["late-correction"],
        "knowledge_from": [datetime(2026, 11, 24, 17, 0, tzinfo=UTC)],
        "knowledge_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
    })
    pq.write_table(pa.concat_tables([base, extra]), bar_path)
    members = smap._members_by_table
    members["dwd_security_bar"] = [_member(tmp_path, "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    sec = next(x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000")
    last_bar = sec["bars"][-1]
    assert last_bar["close_price"] == "25.5"
    revs = sec.get("bar_revisions", {}).get(last_bar["session_date"], [])
    assert len(revs) == 2
    # The revision chain keeps the withdrawn earlier row verbatim.
    assert {r["source_revision"] for r in revs} == {"rev-1", "rev-2"}


def _listing_table_custom(rows):
    return pa.table({
        "security_id": [r[0] for r in rows],
        "listing_id": [r[1] for r in rows],
        "venue_id": [r[2] for r in rows],
        "effective_from": [r[3] for r in rows],
        "effective_to": pa.array([r[4] for r in rows], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [r[5] for r in rows],
    })


def test_same_day_half_open_at_native_open(tmp_path):
    """749-03: half-open tested at the venue's real open instant, not the date."""
    smap = _build_map(tmp_path)
    T = datetime(2026, 11, 24, 1, 30, tzinfo=UTC)  # native open 09:30 +08
    lp = tmp_path / "listing.parquet"
    tbl = _listing_table_custom([
        # opens AFTER the venue open -> not yet listed
        ("security-600000", "listing-600000", "SSE",
         datetime(2026, 11, 24, 2, 0, tzinfo=UTC), None, datetime(2026, 1, 1, tzinfo=UTC)),
    ])
    pq.write_table(tbl, lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    assert all(x["identity"]["security_id"] != "security-600000" for x in res.raw_history["securities"])

    # ends AFTER the venue open -> still in force at the open
    tbl2 = _listing_table_custom([
        ("security-600000", "listing-600000", "SSE",
         datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 11, 24, 2, 0, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ])
    pq.write_table(tbl2, lp)
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    res2 = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    assert any(x["identity"]["security_id"] == "security-600000" for x in res2.raw_history["securities"])


def test_formal_selects_pre_cutoff_revision(tmp_path):
    """749-02: formal cutoff picks the last revision KNOWN before the cutoff."""
    smap = _build_map(tmp_path)
    bar_path = tmp_path / "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet"
    days = _sessions(330)
    base = _bar_table(days)
    last = days[-1]
    # late correction AFTER the 14:30Z cutoff (17:00Z) must not be selected.
    extra = pa.table({
        "source_record_id": ["src/late"], "observation_end": [datetime(last.year,last.month,last.day,16,0,tzinfo=UTC)],
        "listing_id": ["listing-600000"], "calendar_session_id": [f"cal-{last}"],
        "interval_size": ["1d"], "price_basis": ["UNADJUSTED"], "currency": ["CNY"],
        "open": [25.0], "high": [26.0], "low": [24.0], "close": [25.5],
        "volume": [2.0], "turnover": [3.0],
        "source_revision": ["late"], "record_source": ["c"],
        "knowledge_from": [datetime(2026,11,24,17,0,tzinfo=UTC)],
        "knowledge_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
    })
    pq.write_table(pa.concat_tables([base, extra]), bar_path)
    members = smap._members_by_table
    members["dwd_security_bar"] = [_member(tmp_path, "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet")]
    res = reader_mod.read_history(
        # Formal 14:30Z cutoff (Shanghai 22:30) - the 17:00Z late row post-dates it.
        date(2026, 11, 24), datetime(2026, 11, 24, 14, 30, tzinfo=UTC),
        source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    sec = next(x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000")
    # The late 17:00Z correction (close 25.5, knowledge 17:00Z > cutoff)
    # is not selected; the earlier known revision wins.
    assert sec["bars"][-1]["close_price"] != "25.5"
    assert sec["bars"][-1]["late_for_cutoff"] is False
    # The rejected late row is still recorded in the revision chain.
    rev = sec["bar_revisions"][sec["bars"][-1]["session_date"]]
    assert any(r["knowledge_from"] and "17:00" in str(r["knowledge_from"]) for r in rev)


def test_out_of_pool_holding_on_wire(tmp_path):
    """749-04: an out-of-pool held security emits a securities[] entry with account facts."""
    smap = _build_map(tmp_path)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        holdings=[{"security_id": "security-600000", "quantity": 100},
                  {"security_id": "outside-held", "quantity": 50}],
        pending=[{"security_id": "outside-pend", "pending_kind": "sell", "pending_quantity": 5}],
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    sids = {x["identity"]["security_id"] for x in res.raw_history["securities"]}
    assert "outside-held" in sids and "outside-pend" in sids
    held = next(x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "outside-held")
    assert held["account"]["held_quantity"] == 50
    assert held["account"]["account_role"] == "held"
    in_pool = next(x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000")
    assert in_pool["account"]["held_quantity"] == 100


def test_exclusion_prefixed_code(tmp_path):
    """749-09: an exchange-prefixed code 'sz.300308' is matched on its bare code."""
    smap = _build_map(tmp_path)
    excl = tmp_path / "excl.json"
    excl.write_text(json.dumps({"securities": ["600000"], "excluded_dates": ["2026-11-24"]}))
    import hashlib as hl
    sha = "sha256:" + hl.sha256(excl.read_bytes()).hexdigest()
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        replay_exclusion={"path": excl, "sha256": sha},
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    sids = {x["identity"]["security_id"] for x in res.raw_history["securities"]}
    assert "security-600000" not in sids
    assert res.coverage["replay_exclusion"]["excluded_count"] == 1


def test_late_archive_listing_kept_for_replay(tmp_path):
    """749-03 first-segment: in-force listing with archive knowledge post-dating T is KEPT + late_visible for as-observed."""
    smap = _build_map(tmp_path)
    lp = tmp_path / "listing.parquet"
    tbl = _listing_table_custom([
        ("security-600000", "listing-600000", "SSE",
         datetime(2026, 9, 28, tzinfo=UTC), None,
         datetime(2026, 11, 25, 8, 0, tzinfo=UTC)),  # archive after cutoff
    ])
    pq.write_table(tbl, lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    # October replay purpose keeps it + flags late_visible.
    excl = tmp_path / "excl-empty.json"
    excl.write_text(json.dumps({"securities": [], "excluded_dates": []}))
    import hashlib as _hl
    _sha = "sha256:" + _hl.sha256(excl.read_bytes()).hexdigest()
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
        replay_exclusion={"path": excl, "sha256": _sha})
    sec = next((x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000"), None)
    assert sec is not None
    assert sec["identity"]["late_visible"] is True
    # A formal production read drops it on real availability.
    res2 = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=StubSourceMap(members, {}), purpose="production_daily",
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert all(x["identity"]["security_id"] != "security-600000" for x in res2.raw_history["securities"])


def test_exception_window_intersection(tmp_path):
    """749-05: an exception lands only when its key date is inside the security's bar window."""
    smap = _build_map(tmp_path)
    import json as _j
    # In-window exception (a bar session date) + out-of-window exception.
    in_win = "2026-11-24"
    out_win = "2026-07-20"  # outside the 61-session window
    exc_tbl = pa.table({
        "affected_table": ["dwd_security_bar", "dwd_security_bar"],
        "affected_business_key_json": [
            _j.dumps({"listing_id": "listing-600000", "session_date": in_win}),
            _j.dumps({"listing_id": "listing-600000", "session_date": out_win}),
        ],
        "affected_effective_at": [datetime(2026,11,20,tzinfo=UTC), datetime(2026,7,20,tzinfo=UTC)],
        "authority": ["P1"] * 2,
        "disposition": ["fail_closed"] * 2,
        "resolution_state": ["open"] * 2,
        "knowledge_from": [datetime(2026,11,1,tzinfo=UTC)] * 2,
    })
    ep = tmp_path / "dwd_quality_exception.parquet"
    _write(ep, exc_tbl)
    members = smap._members_by_table
    members["dwd_quality_exception"] = [_member(tmp_path, "dwd_quality_exception.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    sec = next(x for x in res.raw_history["securities"] if x["identity"]["security_id"] == "security-600000")
    dates = [_j.loads(e["affected_business_key_json"]).get("session_date") for e in sec["exceptions"]]
    assert in_win in dates and out_win not in dates


def test_exclusion_unpinned_blocks_before_read(tmp_path):
    """749-09: no pinned sha256 -> INPUT_BLOCKED and zero members opened."""
    smap = _build_map(tmp_path)
    calls = []
    orig = smap.verify_member
    smap.verify_member = lambda m: (calls.append(m.member_id), orig(m))[1]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        replay_exclusion={"path": tmp_path / "excl.json", "sha256": None},
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "REPLAY_EXCLUSION_UNPINNED"
    assert calls == []  # no member opened before the gate


def test_exclusion_bad_sha_blocks_before_read(tmp_path):
    """749-09: wrong pinned sha256 -> INPUT_BLOCKED and zero members opened."""
    smap = _build_map(tmp_path)
    excl = tmp_path / "excl.json"
    excl.write_text(json.dumps({"securities": ["600000"], "excluded_dates": ["2026-11-24"]}))
    calls = []
    orig = smap.verify_member
    smap.verify_member = lambda m: (calls.append(m.member_id), orig(m))[1]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        replay_exclusion={"path": excl, "sha256": "sha256:" + "f" * 64},
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "REPLAY_EXCLUSION_SHA_MISMATCH"
    assert calls == []


def test_all_bars_bad_blocks_day(tmp_path):
    """749-05: every declared bar member failing -> whole-day block, not READ."""
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    members["dwd_security_bar"] = [
        _member(tmp_path, "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet",
                declared_sha256="sha256:" + "a" * 64)
    ]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=StubSourceMap(members, {}),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC), purpose="production_daily")
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SHARED_PRICE_UNAVAILABLE"


def _write_exclusion(tmp_path: Path, name="excl-ok.json"):
    """A pinned, correctly-hashed empty #737 exclusion binding."""
    excl = tmp_path / name
    excl.write_text(json.dumps({"securities": [], "excluded_dates": []}))
    sha = "sha256:" + hashlib.sha256(excl.read_bytes()).hexdigest()
    return {"path": excl, "sha256": sha}


def test_exclusion_binding_absent_blocks_replay(tmp_path):
    """R4: REPLAY_AS_OBSERVED with NO binding -> INPUT_BLOCKED, zero members opened."""
    smap = _build_map(tmp_path)
    calls = []
    orig = smap.verify_member
    smap.verify_member = lambda m: (calls.append(m.member_id), orig(m))[1]
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=smap, purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion=None,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "REPLAY_EXCLUSION_UNPINNED"
    assert calls == []  # binding resolves before any member is opened


def test_exclusion_binding_empty_object_blocks_replay(tmp_path):
    """R4: an EMPTY binding {} is the same UNPINNED case, not a silent pass."""
    smap = _build_map(tmp_path)
    calls = []
    orig = smap.verify_member
    smap.verify_member = lambda m: (calls.append(m.member_id), orig(m))[1]
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=smap, purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion={},
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "REPLAY_EXCLUSION_UNPINNED"
    assert calls == []
    # A non-replay purpose with no binding is unaffected: production reads
    # do not carry the pinned October exclusion list.
    res2 = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=_build_map(tmp_path),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
        purpose="production_daily")
    assert res2.status == "READ"


def test_no_bar_member_declared_blocks_day(tmp_path):
    """R4: ZERO declared bar members is SHARED_PRICE_UNAVAILABLE, never READ."""
    smap = _build_map(tmp_path, with_bars=False)
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=smap, purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion=_write_exclusion(tmp_path),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    block = res.coverage["day_block"]
    assert block["reason_code"] == "SHARED_PRICE_UNAVAILABLE"
    assert block["failed_members"] == []  # nothing declared: no failed member list


def test_same_listing_id_version_order_independent(tmp_path):
    """R4: two versions of the same listing_id resolve to the pre-cutoff one,
    regardless of the physical row order in the member."""
    def run(rows):
        smap = _build_map(tmp_path)
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_custom(rows), lp)
        members = smap._members_by_table
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        return reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}),
            purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    # Same fact (identical effective interval); v1 archived pre-cutoff,
    # v2 a post-cutoff re-post.  The pre-cutoff version must win in BOTH
    # row orders - the earlier code took qualifying[0], so reversed rows
    # flipped late_visible on.
    v1 = ("security-600000", "listing-600000", "SSE",
          datetime(2026, 1, 1, tzinfo=UTC), None,
          datetime(2026, 7, 18, tzinfo=UTC))
    v2 = ("security-600000", "listing-600000", "SSE",
          datetime(2026, 1, 1, tzinfo=UTC), None,
          datetime(2026, 11, 20, tzinfo=UTC))  # re-posted after the 10-08 cutoff
    res_fwd = run([v1, v2])
    res_rev = run([v2, v1])

    def proj(res):
        sec = next(
            (x for x in res.raw_history["securities"]
             if x["identity"]["security_id"] == "security-600000"), None)
        return sec and (
            sec["identity"]["listing_id"],
            sec["identity"]["late_visible"],
            sec["identity"]["source_evidence_digest"],
        )
    pf, pr = proj(res_fwd), proj(res_rev)
    assert pf == pr
    assert pf == ("listing-600000", False, pf[2])
    # A genuine conflict - same listing_id, different effective interval -
    # stays ambiguous in either order.
    c1 = ("security-600000", "listing-600000", "SSE",
          datetime(2026, 1, 1, tzinfo=UTC), None,
          datetime(2026, 7, 18, tzinfo=UTC))
    c2 = ("security-600000", "listing-600000", "SSE",
          datetime(2026, 6, 1, tzinfo=UTC), None,
          datetime(2026, 7, 19, tzinfo=UTC))
    for rows in ([c1, c2], [c2, c1]):
        r = run(rows)
        assert "security-600000" in r.coverage["universe"]["ambiguous_security_ids"]


def test_p1_unverified_blocks_factor_dependent_action(tmp_path):
    """R4: P1 ledger missing/bad-sha + a security carrying an action ->
    named dependency block; no fabricated empty exception list."""
    smap = _build_map(tmp_path)
    # StubSourceMap inherits p1_exception_member() pointing at the CN02
    # path, absent locally -> p1_unverified=True without extra staging.
    ca = tmp_path / "dwd_corporate_action.parquet"
    _write(ca, pa.table({
        "corporate_action_id": ["ca-1"],
        "security_id": ["security-600000"],
        "action_type": ["cash_dividend"],
        "action_status": ["announced"],
        "record_date": [date(2026, 11, 20)],
        "ex_date": [date(2026, 11, 21)],
        "payment_date": [date(2026, 11, 28)],
        "announced_at": [datetime(2026, 11, 18, tzinfo=UTC)],
        "record_source": ["primary"],
        "source_record_id": ["src/ca-1"],
        "source_revision": ["rev-1"],
        "knowledge_from": [datetime(2026, 11, 18, tzinfo=UTC)],
        "knowledge_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
    }))
    smap._members_by_table["dwd_corporate_action"] = [
        _member(tmp_path, "dwd_corporate_action.parquet")]
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
        purpose="production_daily")
    sec = next(
        x for x in res.raw_history["securities"]
        if x["identity"]["security_id"] == "security-600000")
    assert sec["actions"]  # the action itself is still delivered verbatim
    dep = sec["dependency_reasons"]
    assert dep["p1_factor_ledger"] == "unverified_or_missing"
    assert dep["factor_dependent_actions"] == "blocked"
    # The unverified ledger must not project a fabricated "no exception":
    # no P1_factor_pit_exception row is emitted for this security.
    assert all(
        e.get("ledger") != "P1_factor_pit_exception"
        for e in sec["exceptions"])
    # Coverage still records the real member read state.
    assert res.coverage["p1_exceptions"]["p1_unverified"] is True


def test_out_of_window_only_price_member_blocks_day(tmp_path):
    """R4-second: only out-of-window Daily/Base members declared -> SHARED_PRICE_UNAVAILABLE."""
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    members.pop("dwd_security_bar", None)
    # A Daily member pinned ONLY for an out-of-window date (11-23 for an
    # Oct-8 T).  Its member_id carries the 1d interval tag so it is a real
    # declared price member - just not for THIS window.
    rel_n = "daily/dwd_security_bar/interval_size=1d/market_date=2026-11-23/part-0.parquet"
    _write(tmp_path / rel_n, _bar_table([date(2026, 11, 23)]))
    class _NovDaily(StubSourceMap):
        def daily_open_days(self):
            return ["2026-11-23"]
        def daily_member(self, day, frag):
            m = _member(tmp_path, rel_n, package_id="daily-2026-11-23", kind="daily", market_date="2026-11-23")
            return m if frag in m.member_id else None
    sm = _NovDaily(members, {})
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=sm, purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion=_write_exclusion(tmp_path),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SHARED_PRICE_UNAVAILABLE"


def test_in_window_bad_out_window_good_price_blocks(tmp_path):
    """R4-second: in-window member all-bad-sha stays blocked even with a good out-of-window member."""
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    rel = "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=10/part-0.parquet"
    _write(tmp_path / rel, _bar_table(_sessions(61, end=date(2026, 10, 8))))
    bad = _member(tmp_path, rel, declared_sha256="sha256:" + "a" * 64)
    grel = "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet"
    _write(tmp_path / grel, _bar_table(_sessions(5, end=date(2026, 11, 24))))
    good = _member(tmp_path, grel)
    members["dwd_security_bar"] = [bad, good]
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion=_write_exclusion(tmp_path),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SHARED_PRICE_UNAVAILABLE"


def test_qualified_empty_pool_stays_read(tmp_path):
    """R4-second: qualified in-window price member + retired listing (empty pool) -> READ, not a block."""
    smap = _build_map(tmp_path)
    lp = tmp_path / "listing.parquet"
    pq.write_table(_listing_table_custom([
        ("security-600000", "listing-600000", "SSE",
         datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC),
         datetime(2026, 1, 1, tzinfo=UTC)),
    ]), lp)
    members = smap._members_by_table
    members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    rel = "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=10/part-0.parquet"
    _write(tmp_path / rel, _bar_table(_sessions(61, end=date(2026, 10, 8))))
    members["dwd_security_bar"] = [_member(tmp_path, rel)]
    res = reader_mod.read_history(
        date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
        source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
        replay_exclusion=_write_exclusion(tmp_path),
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    assert res.status == "READ"
    assert res.coverage["day_block"] is None
    assert res.coverage["universe"]["pool_size"] == 0


def test_same_listing_id_same_clock_conflict_isolated(tmp_path):
    """R4-second: same listing_id/interval/clock but conflicting code -> ambiguous isolation, order-independent."""
    def run(id_rows):
        smap = _build_map(tmp_path)
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_custom([
            ("security-600000", "listing-600000", "SSE",
             datetime(2026, 1, 1, tzinfo=UTC), None, datetime(2026, 7, 18, tzinfo=UTC)),
        ]), lp)
        members = smap._members_by_table
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        ip = tmp_path / "dwd_security_identifier.parquet"
        pq.write_table(pa.table({
            "security_id": [r[0] for r in id_rows],
            "identifier_namespace": [r[1] for r in id_rows],
            "identifier_value": [r[2] for r in id_rows],
            "venue_id": [r[3] for r in id_rows],
        }), ip)
        members["dwd_security_identifier"] = [_member(tmp_path, "dwd_security_identifier.parquet")]
        return reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    i_a = ("security-600000", "code", "300308", "SSE")
    i_b = ("security-600000", "code", "300309", "SSE")
    for rows in ([i_a, i_b], [i_b, i_a]):
        r = run(rows)
        assert "security-600000" in r.coverage["universe"]["ambiguous_security_ids"]
        assert all(
            x["identity"]["security_id"] != "security-600000"
            for x in r.raw_history["securities"])


def _listing_table_with_code(rows):
    """Listing rows carrying listing_code + board_code for key-fact tests."""
    return pa.table({
        "security_id": [r[0] for r in rows],
        "listing_id": [r[1] for r in rows],
        "venue_id": [r[2] for r in rows],
        "listing_code": [r[6] for r in rows],
        "board_code": [r[7] for r in rows],
        "effective_from": [r[3] for r in rows],
        "effective_to": pa.array([r[4] for r in rows], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [r[5] for r in rows],
    })


def test_listing_same_id_same_clock_conflict_isolated(tmp_path):
    """R4-3rd: same listing_id/interval/clock but different listing_code ->
    named ambiguous isolation, identical under row-order swap."""
    def run(rows):
        smap = _build_map(tmp_path)
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_with_code(rows), lp)
        members = smap._members_by_table
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        return reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
    a = ("security-600000", "listing-600000", "SSE", datetime(2026, 1, 1, tzinfo=UTC),
         None, datetime(2026, 7, 18, tzinfo=UTC), "300308", "main")
    b = ("security-600000", "listing-600000", "SSE", datetime(2026, 1, 1, tzinfo=UTC),
         None, datetime(2026, 7, 18, tzinfo=UTC), "300309", "main")
    for rows in ([a, b], [b, a]):
        r = run(rows)
        assert "security-600000" in r.coverage["universe"]["ambiguous_security_ids"]
        assert all(
            x["identity"]["security_id"] != "security-600000"
            for x in r.raw_history["securities"])


def test_mutually_exclusive_code_change_not_isolated(tmp_path):
    """R4-3rd: a historical code-change with non-overlapping effective
    intervals resolves to the open-time-valid code, not ambiguity."""
    def run(order):
        smap = _build_map(tmp_path)
        members = smap._members_by_table
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_custom([
            ("security-600000", "listing-600000", "SSE",
             datetime(2026, 1, 1, tzinfo=UTC), None, datetime(2026, 7, 18, tzinfo=UTC)),
        ]), lp)
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        ip = tmp_path / "dwd_security_identifier.parquet"
        rows = [
            ("security-600000", "CN_STOCK_CODE:SSE", "600849", "SSE",
             datetime(1994, 3, 23, tzinfo=UTC), datetime(2010, 3, 4, tzinfo=UTC),
             datetime(2026, 8, 3, tzinfo=UTC)),
            ("security-600000", "CN_STOCK_CODE:SSE", "601607", "SSE",
             datetime(2010, 3, 4, tzinfo=UTC), None,
             datetime(2026, 8, 3, tzinfo=UTC)),
        ]
        if order:
            rows = rows[::-1]
        pq.write_table(pa.table({
            "security_id": [r[0] for r in rows],
            "identifier_namespace": [r[1] for r in rows],
            "identifier_value": [r[2] for r in rows],
            "venue_id": [r[3] for r in rows],
            "effective_from": [r[4] for r in rows],
            "effective_to": pa.array([r[5] for r in rows], type=pa.timestamp("us", tz="UTC")),
            "knowledge_from": [r[6] for r in rows],
        }), ip)
        members["dwd_security_identifier"] = [_member(tmp_path, "dwd_security_identifier.parquet")]
        rr = reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
        sec = next((x for x in rr.raw_history["securities"]
                    if x["identity"]["security_id"] == "security-600000"), None)
        return sec and sec["identity"]["stock_code"], rr.coverage["universe"]["ambiguous_security_ids"]
    assert run(False) == ("601607", [])
    assert run(True) == ("601607", [])


def _identifier_table_full(rows):
    """security_identifier rows carrying namespace/value/venue plus the
    effective interval and the knowledge clock (audit R4 fourth pass)."""
    return pa.table({
        "security_id": [r[0] for r in rows],
        "identifier_namespace": [r[1] for r in rows],
        "identifier_value": [r[2] for r in rows],
        "venue_id": [r[3] for r in rows],
        "effective_from": [r[4] for r in rows],
        "effective_to": pa.array([r[5] for r in rows], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [r[6] for r in rows],
    })


def test_identifier_uses_listing_open_instant(tmp_path):
    """R4-4th: the trading code resolves at the SAME venue open instant
    as the listing row.  A code-change effective only at 02:00Z must NOT
    be adopted when the venue opened at 01:30Z - both physical orders
    keep 300308; a change effective before/at the open does switch."""
    OPEN = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)  # matches _calendar_table

    def run(swap, change_at):
        smap = _build_map(tmp_path)
        members = smap._members_by_table
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_with_code([
            ("security-600000", "listing-600000", "SSE",
             datetime(2026, 1, 1, tzinfo=UTC), None,
             datetime(2026, 7, 18, tzinfo=UTC), "300308", "main"),
        ]), lp)
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        ip = tmp_path / "dwd_security_identifier.parquet"
        prev = ("security-600000", "CN_STOCK_CODE:SSE", "300308", "SSE",
                datetime(2026, 1, 1, tzinfo=UTC), change_at,
                datetime(2026, 7, 18, tzinfo=UTC))
        nxt = ("security-600000", "CN_STOCK_CODE:SSE", "300309", "SSE",
               change_at, None, datetime(2026, 7, 18, tzinfo=UTC))
        rows = [prev, nxt] if not swap else [nxt, prev]
        pq.write_table(_identifier_table_full(rows), ip)
        members["dwd_security_identifier"] = [_member(tmp_path, "dwd_security_identifier.parquet")]
        rr = reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))
        sec = next((x for x in rr.raw_history["securities"]
                    if x["identity"]["security_id"] == "security-600000"), None)
        return sec and sec["identity"]["stock_code"]

    from datetime import timedelta
    # 02:00Z change is AFTER the 01:30Z open -> still the listed 300308.
    for swap in (False, True):
        assert run(swap, OPEN + timedelta(minutes=30)) == "300308"
    # Change exactly AT the open (half-open: new effective_from == open)
    # and BEFORE the open both switch to the new code.
    for swap in (False, True):
        assert run(swap, OPEN) == "300309"
        assert run(swap, OPEN - timedelta(minutes=30)) == "300309"


def test_listing_latest_replica_collapses_before_conflict(tmp_path):
    """R4-4th: pick the qualified latest knowledge version first, then
    collapse equivalent replicas of the SAME business fact.  An older
    different value plus TWO identical latest rows must yield ONE unique
    identity under both physical orders - not an ambiguity; a genuinely
    different value at the same latest clock still isolates."""
    def run(rows):
        smap = _build_map(tmp_path)
        members = smap._members_by_table
        lp = tmp_path / "listing.parquet"
        pq.write_table(_listing_table_with_code(rows), lp)
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
        return reader_mod.read_history(
            date(2026, 10, 8), datetime(2026, 10, 8, 13, 30, tzinfo=UTC),
            source_map=StubSourceMap(members, {}), purpose=reader_mod.REPLAY_AS_OBSERVED,
            replay_exclusion=_write_exclusion(tmp_path),
            observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC))

    older = ("security-600000", "listing-600000", "SSE",
             datetime(2026, 1, 1, tzinfo=UTC), None,
             datetime(2026, 7, 18, tzinfo=UTC), "300308", "main")
    latest = ("security-600000", "listing-600000", "SSE",
              datetime(2026, 1, 1, tzinfo=UTC), None,
              datetime(2026, 7, 19, tzinfo=UTC), "300309", "main")
    replica = tuple(latest)  # byte-identical copy of the latest fact

    # Older different value + TWO identical latest versions -> unique.
    for rows in ([older, latest, replica], [latest, replica, older],
                 [replica, older, latest]):
        r = run(rows)
        sec = next((x for x in r.raw_history["securities"]
                    if x["identity"]["security_id"] == "security-600000"), None)
        # Unique identity admitted: the latest version's listing_code wins.
        assert sec is not None and sec["identity"]["listing_code"] == "300309"
        assert "security-600000" not in r.coverage["universe"]["ambiguous_security_ids"]

    # A genuinely DIFFERENT value at the same latest clock still isolates.
    conflict = ("security-600000", "listing-600000", "SSE",
                datetime(2026, 1, 1, tzinfo=UTC), None,
                datetime(2026, 7, 19, tzinfo=UTC), "300310", "main")
    for rows in ([older, latest, conflict], [conflict, latest, older]):
        r = run(rows)
        assert "security-600000" in r.coverage["universe"]["ambiguous_security_ids"]


class _FormalStubSourceMap(StubSourceMap):
    """Stub that also serves the producer / channel / knowledge clocks
    per daily package so a FORMAL_DAILY_UPDATE read can be exercised."""

    def __init__(self, members_by_table, daily_cores, clocks):
        super().__init__(members_by_table, daily_cores)
        # clocks: {day: {"knowledge": ts, "producer": ts, "channel": ts}}
        self._clocks = clocks

    def daily_knowledge_at(self, market_date):
        return (self._clocks.get(market_date) or {}).get("knowledge")

    def daily_producer_completed_at(self, market_date):
        return (self._clocks.get(market_date) or {}).get("producer")

    def daily_visible_at(self, market_date):
        return (self._clocks.get(market_date) or {}).get("channel")

    class _Pkg:
        def __init__(self, members):
            self.members = members

    def daily_core(self, market_date):
        return _FormalStubSourceMap._Pkg(self._daily.get(market_date, ()))


def _formal_daily_member(tmp_path, day, fragment="dwd_security_bar"):
    """A daily-core bar member for one market date."""
    rel = f"payload/dwd_security_bar/day={day}/{fragment}.parquet"
    return _member(tmp_path, rel, package_id=f"daily-{day}", kind="daily",
                   market_date=day)


def test_formal_channel_visibility_gates_bar_selection(tmp_path):
    """R4 second segment: a package that COMPLETED production before the
    cutoff but only became CHANNEL-VISIBLE after it must not supply T
    bars under a FORMAL_DAILY_UPDATE read; the member is registered and
    named late with producer/channel/knowledge kept distinct."""
    T = date(2026, 11, 24)
    CUT = datetime(2026, 11, 24, 14, 30, tzinfo=UTC)
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    # A daily-core bar member whose production finished 10:00Z but whose
    # channel only published it 16:05Z - after the 14:30Z formal cutoff.
    dm = _formal_daily_member(tmp_path, "2026-11-24")
    clocks = {"2026-11-24": {
        "knowledge": datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
        "producer": datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
        "channel": datetime(2026, 11, 24, 16, 5, tzinfo=UTC),
    }}
    fmap = _FormalStubSourceMap(members, {"2026-11-24": [dm]}, clocks)
    res = reader_mod.read_history(
        T, CUT, source_map=fmap, purpose=reader_mod.FORMAL_DAILY_UPDATE,
        observed_at=datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
    key = f"daily-2026-11-24::{dm.member_id}"
    # Member is registered and named late for the formal read.
    assert key in res.coverage["late_visible_members"]
    src = next(m for m in res.raw_history["source_members"]
               if m["member_id"] == dm.member_id)
    # The four instants stay distinct on the receipt.
    assert src["producer_completed_at"] == "2026-11-24T10:00:00Z"
    assert src["channel_visible_at"] == "2026-11-24T16:05:00Z"
    assert src["visible_at"] == "2026-11-24T16:05:00Z"


def test_formal_selects_pre_cutoff_revision_over_late(tmp_path):
    """R4 second segment: a 17:00Z revision does NOT displace the last
    revision known by the 14:30Z formal cutoff; a noon-withdrawn row
    stays in the revision chain verbatim but never wins the select."""
    T = date(2026, 11, 24)
    CUT = datetime(2026, 11, 24, 14, 30, tzinfo=UTC)
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    days = _sessions(330)
    rows1 = _bar_table(days).to_pylist()
    # Same session carries: original bar + a noon-withdrawn row + a 17:00Z
    # late fix.  The formal cutoff selects the original (last revision
    # known by 14:30Z), keeps the other two in the chain verbatim.
    withdrawn = dict(rows1[-1], close_price=17.0, source_revision="withdrawn",
                     knowledge_to=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    late_fix = dict(rows1[-1], close_price=25.0, source_revision="late-fix",
                    knowledge_from=datetime(2026, 11, 24, 17, 0, tzinfo=UTC),
                    knowledge_to=None)
    all_rows = rows1 + [withdrawn, late_fix]
    rel = "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet"
    pq.write_table(pa.table({k: [r.get(k) for r in all_rows]
                             for k in rows1[0].keys()}), tmp_path / rel)
    members["dwd_security_bar"] = [_member(tmp_path, rel)]
    res = reader_mod.read_history(
        T, CUT, source_map=StubSourceMap(members, {}),
        purpose=reader_mod.FORMAL_DAILY_UPDATE,
        observed_at=datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
    sec = next(x for x in res.raw_history["securities"]
               if x["identity"]["security_id"] == "security-600000")
    last = sec["bars"][-1]
    # The 17:00Z late fix does not win; the noon-withdrawn row is kept in
    # the chain verbatim but is not selected either.  The selected bar is
    # the original close (neither 17.0 nor 25.0).
    assert float(last["close_price"]) not in (17.0, 25.0)
    chain = sec["bar_revisions"].get("2026-11-24")
    assert chain is not None and len(chain) == 3
    kts = [c["knowledge_to"] for c in chain]
    assert any(k is not None for k in kts)  # withdrawn row preserved verbatim


def test_manifest_chain_fault_blocks_before_member_read(tmp_path):
    """R4 second segment: a declared Base/DATES/descriptor link that
    fails hash or is unreadable blocks the whole day BEFORE any market
    member is opened (SUMMARY_CHAIN_FAULT, zero members read)."""
    smap = _build_map(tmp_path)
    calls = []
    orig = smap.verify_member
    smap.verify_member = lambda m: (calls.append(m.member_id), orig(m))[1]
    # Inject a chain whose declared Base manifest sha does not match.
    smap.manifest_chain = lambda: [
        {"artifact": "base_manifest", "path": "/x/manifest.json",
         "declared_sha256": "sha256:" + "a" * 64,
         "actual_sha256": "sha256:" + "b" * 64,
         "status": "SHA_MISMATCH", "verified_at": "2026-11-24T14:00:00Z"},
    ]
    res = reader_mod.read_history(
        date(2026, 11, 24), datetime(2026, 11, 24, 14, 30, tzinfo=UTC),
        source_map=smap, purpose=reader_mod.FORMAL_DAILY_UPDATE,
        observed_at=datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SUMMARY_CHAIN_FAULT"
    assert calls == []  # no market member opened before the gate


def test_same_name_member_two_packages_kept_distinct(tmp_path):
    """R4 second segment (R3 item 1): the same relative member path
    (e.g. audit.json) shipped under TWO daily packages stays resolvable
    under both package-qualified keys, not collapsed to the first."""
    T = date(2026, 11, 24)
    CUT = datetime(2026, 11, 24, 14, 30, tzinfo=UTC)
    smap = _build_map(tmp_path)
    members = smap._members_by_table
    # The same relative member id under two different daily packages.
    rel = "payload/dwd_security_bar/day=x/audit.json.parquet"
    m_a = _member(tmp_path, rel, package_id="daily-2026-11-23",
                  kind="daily", market_date="2026-11-23")
    m_b = _member(tmp_path, rel, package_id="daily-2026-11-24",
                  kind="daily", market_date="2026-11-24")
    clocks = {
        "2026-11-23": {"knowledge": datetime(2026, 11, 23, 10, 0, tzinfo=UTC),
                        "producer": datetime(2026, 11, 23, 10, 0, tzinfo=UTC),
                        "channel": datetime(2026, 11, 23, 10, 5, tzinfo=UTC)},
        "2026-11-24": {"knowledge": datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
                        "producer": datetime(2026, 11, 24, 10, 0, tzinfo=UTC),
                        "channel": datetime(2026, 11, 24, 10, 5, tzinfo=UTC)},
    }
    fmap = _FormalStubSourceMap(
        members, {"2026-11-23": [m_a], "2026-11-24": [m_b]}, clocks)
    res = reader_mod.read_history(
        T, CUT, source_map=fmap, purpose=reader_mod.FORMAL_DAILY_UPDATE,
        observed_at=datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
    keys = {m["package_key"] for m in res.raw_history["source_members"]
            if m["member_id"] == rel}
    assert keys == {"daily-2026-11-23::" + rel, "daily-2026-11-24::" + rel}
    # Each keeps its own package_id + version on the envelope.
    pkg_ids = {m["package_id"] for m in res.raw_history["source_members"]
               if m["member_id"] == rel}
    assert pkg_ids == {"daily-2026-11-23", "daily-2026-11-24"}
