"""e1_runtime: production entry -- frozen assets + same-job panel + P12 path.

Synthetic fixtures only; never reads the physical Base.  The panel report is
asserted as the SAME job's actual computation (its row/feature_ready counts
and content digest come from the run's real features_by_security, not a
fixture label).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.e1_runtime import (
    E1RuleAssets,
    load_e1_rule_assets,
    run_e1_from_history,
)
from tests.unit.test_p11_history_input import (
    _run_kwargs,
    _trend_envelope,
)

ROOT = find_project_root()


@pytest.fixture(scope="module")
def assets() -> E1RuleAssets:
    return load_e1_rule_assets(ROOT)


# --- asset loading ------------------------------------------------------------


def test_load_assets_returns_frozen_identities(assets):
    assert assets.card_digest.startswith("sha256:")
    assert assets.registry_digest.startswith("sha256:")
    assert assets.topology_digest.startswith("sha256:")
    assert type(assets.runner).__name__ == "FormalStrategyRunner"


def test_load_assets_pin_match_passes(assets):
    again = load_e1_rule_assets(
        ROOT,
        expected_card_digest=assets.card_digest,
        expected_registry_digest=assets.registry_digest,
    )
    assert again.card_digest == assets.card_digest


def test_load_assets_pin_mismatch_refuses(assets):
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets(ROOT, expected_card_digest="sha256:" + "0" * 64)


def test_load_assets_missing_root_refuses():
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets("/nonexistent-e1-root")


# --- same-job panel + run ----------------------------------------------------


def test_run_e1_from_history_produces_real_panel(assets):
    kw = _run_kwargs()
    output, report = run_e1_from_history(
        _trend_envelope(),
        assets=assets,
        facts=kw["facts"],
        policy=kw["policy"],
        account=kw["account"],
        run_id="e1-test-1",
        snapshot_id="snap-e1",
        snapshot_manifest_digest=sha256_digest({"e1": 1}),
        split_policy=kw["split_policy"],
        source_policy=kw["source_policy"],
        clock=kw["clock"],
    )
    # The panel is the actual same-job computation: rows/ready/eligible come
    # from the run's features_by_security, and the content digest binds the
    # real feature bytes.
    assert report.row_count == 1
    assert report.feature_ready_count == 1
    assert report.entry_eligible_count == 1
    assert report.data_content_digest.startswith("sha256:")
    assert report.digest.startswith("sha256:")
    # The run produced a real entry through the genuine chain.
    assert output.receipt is not None
    assert output.result.disposition.value == "publishable"
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries


def test_run_e1_rejects_empty_universe(assets):
    kw = _run_kwargs()
    env = _trend_envelope()
    env["securities"] = []
    with pytest.raises(ProductDailyError):
        run_e1_from_history(
            env,
            assets=assets,
            facts=kw["facts"],
            policy=kw["policy"],
            account=kw["account"],
            run_id="e1-test-2",
            snapshot_id="snap-e1",
            snapshot_manifest_digest=sha256_digest({"e1": 2}),
            split_policy=kw["split_policy"],
            source_policy=kw["source_policy"],
            clock=kw["clock"],
        )


# --- R2: real panel on the replay path + verified asset identity ---------------


def test_r2_replay_day_binds_real_panel_not_placeholder(assets):
    # Root R2 probe: run_e1_replay_day must sign the actual same-job panel,
    # not the zero-row placeholder.  Two inputs that change real features get
    # two different real reports.
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    out1 = e_run_e1_replay(req, f.T1, assets)
    other = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    other["securities"][0]["bars"][-10]["volume"] = "91234"
    out2 = e_run_e1_replay(other, f.T1, assets)
    placeholder = _ph(assets)
    d1 = out1["source_closure"].get("feature_panel_report_digest")
    d2 = out2["source_closure"].get("feature_panel_report_digest")
    assert d1 != placeholder.digest
    assert d1 != d2
    assert out1.get("publishable") is True


def e_run_e1_replay(
    req, day, assets, observed_at=None, p12_evidence=None, calendar_schedule_evidence=None
):
    from quanthus.product_daily.recommendation.e1_runtime import run_e1_replay_day

    observed_clock = (lambda: datetime.fromisoformat(observed_at)) if observed_at else None
    return run_e1_replay_day(
        req,
        day,
        assets=assets,
        snapshot_id="s",
        snapshot_manifest_digest="sha256:" + "a" * 64,
        observed_clock=observed_clock,
        p12_evidence=p12_evidence,
        calendar_schedule_evidence=calendar_schedule_evidence,
    )


def _ph(assets):
    from quanthus.product_daily.recommendation.e1_runtime import _replay_panel_placeholder

    return _replay_panel_placeholder(assets)


def test_r2_wrong_code_commit_refused(assets):
    with pytest.raises(ProductDailyError):
        load_e1_rule_assets(
            ROOT,
            expected_code_commit="1" * 40,
            expected_code_tree="2" * 40,
        )


def test_r2_default_call_no_clock_not_crash(assets):
    kw = _run_kwargs()
    output, report = run_e1_from_history(
        _trend_envelope(),
        assets=assets,
        facts=kw["facts"],
        policy=kw["policy"],
        account=kw["account"],
        run_id="e1-noclock",
        snapshot_id="s",
        snapshot_manifest_digest=sha256_digest({"n": 1}),
        source_policy=kw["source_policy"],
        # clock omitted -> default logical decision instant, never a TypeError
    )
    assert output.receipt is not None
    assert report.row_count == 1


def test_r3_replay_day_injects_equivalent_day_anchor(assets):
    """run_e1_replay_day carries anchor_mode='equivalent_day': an open_at=null
    session on an old continuously-listed security resolves by the day-bound
    proof, named on the receipt's membership_proof -- not a strict isolation."""
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    # Null out open_at on a run of mid sessions (the real October Base shape):
    # the listing predates the bound calendar so every such session resolves
    # wholly inside the listing interval.
    for row in req["calendar_rows"]:
        if row["session_date"] in {d.isoformat() for d in days[40:60]}:
            row["open_at"] = None
    out = e_run_e1_replay(req, f.T1, assets)
    receipts = out["source_closure"].get("history_receipts") or []
    assert receipts
    proof = receipts[0].get("membership_proof") or {}
    assert proof.get("anchor_mode") == "equivalent_day"
    assert proof.get("equivalent_day_sessions")


# --- october as-observed: sealed post-cutoff archives named, content <= T -------


def _sealed_member_request():
    """A replay request whose source member is sealed AFTER T but whose
    declared business content ends on/before T (the real October shape)."""
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    for m in req["source_members"]:
        # Sealed/acquired after the logical T (honest archival clocks);
        # business content still ends <= T.
        m["knowledge_at"] = "2026-11-20T10:00:00+08:00"
        m["visible_at"] = "2026-11-20T10:00:00+08:00"
        m["date_to"] = "2026-10-30"
    return req


def test_asobserved_sealed_member_named_not_relabelled(assets):
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    out = e_run_e1_replay(req, f.T1, assets)
    block = out.get("as_observed") or {}
    sealed = block.get("sealed_archives", {}).get("members", [])
    assert sealed, "a post-cutoff sealed member must be named, not hidden"
    assert sealed[0]["kind"] == "sealed_post_cutoff_archive"
    assert sealed[0]["visible_at"] > out["logical_decision_at"]
    assert block["pit_disguise"] is False


def test_asobserved_post_T_content_refused(assets):
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    # A USED member whose whole declared span begins past the signal cannot
    # have supplied an on/before-T row: consuming it is a post-window
    # contradiction and stays fail-closed.
    req["source_members"][0]["date_from"] = "2026-11-02"
    req["source_members"][0]["date_to"] = "2026-11-30"
    with pytest.raises(ProductDailyError):
        e_run_e1_replay(req, f.T1, assets)


def test_asobserved_monthly_container_admitted_and_named(assets):
    """R2: a member used by consumed rows whose container edge spans the whole
    month (covering a November partition the period does not read) is admitted;
    the container edge and the used-row business date stay separate."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    # Container spans the whole month (T+1 match window = 10-30; edge 10-31 is
    # within it).  The member is used (identity/listing/calendar rows bind it).
    req["source_members"][0]["date_to"] = "2026-10-31"
    out = e_run_e1_replay(req, f.T1, assets)
    use = out.get("as_observed", {}).get("member_use", {})
    mem = use["members"][0]
    assert mem["is_used"] is True
    assert mem["disposition"] == "used"
    assert use["blocking_used_members"] == []


def test_asobserved_late_partition_excluded_not_refused(assets):
    """R2: a member the period does NOT use, whose whole declared span is past
    T (a November daily market partition), is named as an excluded late
    partition -- moved out of the period input, never a hard failure."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    # Keep the used member; add a wholly-late unused partition member.
    req["source_members"].append(
        {
            "member_id": "nov-daily-1d",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "nov/daily.parquet",
            "sha256": "sha256:" + "2" * 64,
            "date_from": "2026-11-02",
            "date_to": "2026-11-24",
            "knowledge_at": "2026-11-24T16:00:00+08:00",
            "visible_at": "2026-11-24T16:00:00+08:00",
        }
    )
    out = e_run_e1_replay(req, f.T1, assets)
    use = out.get("as_observed", {}).get("member_use", {})
    excl = use["excluded_late_partitions"]
    assert excl and excl[0]["member_id"] == "nov-daily-1d"
    assert use["blocking_used_members"] == []


def test_asobserved_member_sealed_after_actual_read_refused(assets):
    """R3 fail-first: a member sealed LATER than this run's real read instant
    was not yet obtained by the actual verification -> refused by dependency.
    The same member sealed BEFORE the read is admitted (paired control)."""
    from tests.unit import test_p11_paper_replay as f

    read_at = "2026-11-25T03:00:00+08:00"
    # Control: sealed 11-01 (< read 11-25) -> admitted.
    ok = _sealed_member_request()
    ok["source_members"][0]["knowledge_at"] = "2026-11-01T00:00:00+08:00"
    ok["source_members"][0]["visible_at"] = "2026-11-01T00:00:00+08:00"
    out = e_run_e1_replay(ok, f.T1, assets, observed_at=read_at)
    assert out.get("disposition") == "publishable"
    # Fail: sealed 12-01 (> read 11-25) -> not yet obtained at the real read.
    late = _sealed_member_request()
    late["source_members"][0]["knowledge_at"] = "2026-12-01T00:00:00+08:00"
    late["source_members"][0]["visible_at"] = "2026-12-01T00:00:00+08:00"
    with pytest.raises(ProductDailyError) as ei:
        e_run_e1_replay(late, f.T1, assets, observed_at=read_at)
    # A shared member sealed later than the actual read fails the whole-day
    # gate (unavailable_at_read), surfaced via the shared-block reason code.
    assert "unavailable" in str(ei.value) or "shared member" in str(ei.value)


def test_asobserved_late_partition_projected_out_of_envelope(assets):
    """R3: excluded late partitions are projected OUT of the envelope handed
    to H1 -- the used member set is a real projection, not only a label."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    req["source_members"].append(
        {
            "member_id": "nov-daily-1d",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "nov/daily.parquet",
            "sha256": "sha256:" + "2" * 64,
            "date_from": "2026-11-02",
            "date_to": "2026-11-24",
            "knowledge_at": "2026-11-10T16:00:00+08:00",
            "visible_at": "2026-11-10T16:00:00+08:00",
        }
    )
    out = e_run_e1_replay(req, f.T1, assets)
    members = out.get("source_closure", {}).get("source_members") or []
    ids = {m.get("member_id") for m in members}
    assert "nov-daily-1d" not in ids


def test_r4_read_instant_is_signed_into_source_closure(assets):
    """R4: this run's actual read instant is bound INTO source_closure, not
    only the outer receipt -- two read instants on identical input must give
    different source_closure digests."""
    from tests.unit import test_p11_paper_replay as f

    base = _sealed_member_request()
    base["source_members"][0]["knowledge_at"] = "2026-11-01T00:00:00+08:00"
    base["source_members"][0]["visible_at"] = "2026-11-01T00:00:00+08:00"
    a = e_run_e1_replay(base, f.T1, assets, observed_at="2026-11-25T03:00:00+08:00")
    b = e_run_e1_replay(base, f.T1, assets, observed_at="2026-11-25T03:01:00+08:00")
    da = a.get("source_closure_digest") or a.get("receipt", {}).get("source_closure_digest")
    db = b.get("source_closure_digest") or b.get("receipt", {}).get("source_closure_digest")
    assert da and db and da != db
    ev = a["source_closure"].get("consumption_evidence")
    assert ev and ev["actual_read_at"] == "2026-11-25T03:00:00+08:00"


def test_r4_unused_member_sealed_after_read_not_a_block(assets):
    """R4: an UNUSED member sealed after the actual read cannot block the day;
    it is named but does not refuse, unlike a USED member sealed late."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    req["source_members"][0]["knowledge_at"] = "2026-11-01T00:00:00+08:00"
    req["source_members"][0]["visible_at"] = "2026-11-01T00:00:00+08:00"
    req["source_members"].append(
        {
            "member_id": "unconsumed-december",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "dec/daily.parquet",
            "sha256": "sha256:" + "3" * 64,
            "date_from": "2026-12-01",
            "date_to": "2026-12-31",
            "knowledge_at": "2026-12-01T00:00:00+08:00",
            "visible_at": "2026-12-01T00:00:00+08:00",
        }
    )
    out = e_run_e1_replay(req, f.T1, assets, observed_at="2026-11-25T03:00:00+08:00")
    assert out.get("disposition") == "publishable"


def test_r5_per_security_member_isolates_that_security_only(assets):
    """R5: a member bound only through one security's depends_on_member_ids
    is a per-security dependency -- its late seal isolates that security
    (demoted held-only) while the neighbour keeps evaluating; the day is not
    blocked.  A shared member's failure still blocks the whole day."""
    from copy import deepcopy

    from tests.unit import test_p11_paper_replay as f

    days = tuple(v for v in f.SESSION_DAYS if v <= f.T1)
    secs = [f._security(c, days) for c in ("600000", "600001")]
    req = f._request(market_dates=(f.T1,), securities=secs)
    for m in req["source_members"]:
        m["knowledge_at"] = "2026-11-01T00:00:00+08:00"
        m["visible_at"] = "2026-11-01T00:00:00+08:00"
    sp = deepcopy(req["source_members"][0])
    sp.update(
        member_id="only-second-security",
        member_path="second-only.parquet",
        sha256="sha256:" + "9" * 64,
        knowledge_at="2026-12-01T00:00:00+08:00",
        visible_at="2026-12-01T00:00:00+08:00",
    )
    req["source_members"].append(sp)
    req["securities"][1]["identity"]["depends_on_member_ids"].append("only-second-security")
    out = e_run_e1_replay(req, f.T1, assets, observed_at="2026-11-25T03:00:00+08:00")
    assert out.get("disposition") == "publishable"
    use = out["as_observed"]["member_use"]
    assert use["isolated_by_security"] == {"600001": ["only-second-security"]}
    assert use["blocking_shared_members"] == []


def test_r5_p12_evidence_folded_into_closure(assets):
    """R5: p12_evidence (envelope bytes ref/SHA, selected member/row versions,
    actual first-read/verify instants, ledger digest) is folded into the
    signed closure under consumption_evidence.p12 -- bound, not asserted."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {
        "envelope_sha256": "59a323ff" + "0" * 56,
        "receipt_sha256": "ccacfcf4" + "0" * 56,
        "member_first_read_at": "2026-11-24T19:15:57+00:00",
        "member_verified_at": "2026-11-24T19:16:20+00:00",
        "ledger_digest": "sha256:" + "d" * 64,
    }
    out = e_run_e1_replay(req, f.T1, assets, p12_evidence=ev)
    cl = out["source_closure"].get("consumption_evidence", {})
    assert cl.get("p12", {}).get("envelope_sha256") == ev["envelope_sha256"]


def test_r6_calendar_schedule_evidence_bound(assets):
    """R6: the proven P10 #747 exchange-schedule evidence is bound into the
    signed closure under a named consumer rule -- schedule basis + named
    source digests + UNKNOWN publication instants, never a T-known claim."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {
        "consumer_rule_id": "p11.e1-october-calendar-consume",
        "consumer_rule_version": "1.0.0",
        "sessions": [
            {
                "venue_id": "SSE",
                "session_date": "2026-10-08",
                "schedule_basis": "EXPLICIT_EXCHANGE_SCHEDULE",
                "source_documents": [
                    {
                        "notice_id": "上证公告〔2025〕45号",
                        "rule_clause": "annual-closures",
                        "publication_date": "2025-12-22",
                        "sha256": "6414ab8f" + "4" * 56,
                        "source_receipt_sha256": "342164f9" + "5" * 56,
                        "published_at": None,
                        "published_at_grade": "UNKNOWN_EXACT_INSTANT",
                    }
                ],
                "source_published_at": None,
                "source_published_at_state": "UNKNOWN_EXACT_INSTANT",
                "historical_channel_available_at": None,
            },
            {
                "venue_id": "SSE",
                "session_date": "2026-09-30",
                "schedule_basis": "RULE_DERIVED_SCHEDULE",
                "source_documents": [],
                "source_published_at": None,
                "source_published_at_state": "UNKNOWN_EXACT_INSTANT",
                "historical_channel_available_at": None,
            },
        ],
    }
    out = e_run_e1_replay(req, f.T1, assets, calendar_schedule_evidence=ev)
    cl = out["source_closure"].get("consumption_evidence", {})
    block = cl.get("calendar_schedule_evidence", {})
    assert block["consumer_rule_id"] == "p11.e1-october-calendar-consume"
    # Only proven exchange publication writes announced_before_T; a bare
    # caller timestamp is not that, so it stays False.
    assert block["announced_before_T"] is False
    sessions = {s["session_date"]: s for s in block["sessions"]}
    assert sessions["2026-10-08"]["schedule_basis"] == "EXPLICIT_EXCHANGE_SCHEDULE"
    assert sessions["2026-10-08"]["source_published_at_state"] == "UNKNOWN_EXACT_INSTANT"
    assert sessions["2026-10-08"]["source_documents"][0]["notice_id"] == "上证公告〔2025〕45号"
    assert sessions["2026-09-30"]["schedule_basis"] == "RULE_DERIVED_SCHEDULE"


def test_r6_calendar_schedule_evidence_unbound_session_refused(assets):
    """R6 fail-first: schedule evidence for a session the bound calendar does
    not carry is refused, never silently adopted."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {
        "consumer_rule_id": "p11.e1-october-calendar-consume",
        "consumer_rule_version": "1.0.0",
        "sessions": [
            {
                "venue_id": "SSE",
                "session_date": "2031-01-01",  # not a bound calendar_row
                "schedule_basis": "EXPLICIT_EXCHANGE_SCHEDULE",
                "source_documents": [],
            }
        ],
    }
    with pytest.raises(ProductDailyError) as ei:
        e_run_e1_replay(req, f.T1, assets, calendar_schedule_evidence=ev)
    assert "calendar_schedule_evidence_unbound" in str(ei.value)


def test_r6_calendar_schedule_evidence_absent_declared_empty(assets):
    """R6: no evidence -> the block is present but declared-empty; the run
    still reads the calendar through the consumer rule, never a guessed label."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    out = e_run_e1_replay(req, f.T1, assets)
    block = out["source_closure"]["consumption_evidence"]["calendar_schedule_evidence"]
    assert block["schema_version"] == "p11.calendar-schedule-evidence.v1"
    assert block["announced_before_T"] is False
    assert block["sessions"] == []


def test_strict_still_rejects_sealed_member(assets):
    """The strict H1 path is unchanged: a post-cutoff member still fails the
    visibility gate when as_observed is not requested."""
    from tests.unit.test_p11_history_input import (
        _envelope,
        _security_entry,
        _trend_raw,
        load_raw_history,
    )

    env = _envelope([_security_entry(_trend_raw("600000"))])
    env["source_members"][0]["visible_at"] = "2027-01-01T00:00:00+08:00"
    env["source_members"][0]["knowledge_at"] = "2027-01-01T00:00:00+08:00"
    with pytest.raises(ProductDailyError):
        load_raw_history(env)  # strict default -> source_visibility_after_cutoff
