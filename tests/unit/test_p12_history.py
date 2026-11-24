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
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
    )
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
    )
    # no listing rows -> P11 derives no membership; reader does not invent one
    assert res.raw_history["listing_rows"] == []


def test_missing_bars_yield_empty_bars_not_fabrication(tmp_path):
    smap = _build_map(tmp_path, with_bars=False)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
    )
    assert res.raw_history["securities"][0]["bars"] == []


def test_bar_window_is_last_61_sessions(tmp_path):
    smap = _build_map(tmp_path, n_sessions=200)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
    )
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
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap)
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
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap)
    assert res.status == "INPUT_BLOCKED"
    assert res.coverage["day_block"]["reason_code"] == "SHARED_CALENDAR_UNAVAILABLE"


def test_bars_carry_member_and_source_provenance(tmp_path):
    smap = _build_map(tmp_path, n_sessions=130)
    res = reader_mod.read_history(date(2026, 11, 24), CUTOFF, source_map=smap)
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
    )
    uni = res.coverage["universe"]
    assert "security-600000" in uni["held_in_pool"]
    assert "outside-held" in uni["held_unknown_quantity"]
    assert "outside-pending" in uni["pending_only"]
    assert uni["pending_typed"][0]["pending_kind"] == "sell"
