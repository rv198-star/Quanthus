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


def test_package_clock_checks_original_receipt_against_bound_trading_day():
    from types import SimpleNamespace

    from quanthus.product_daily.recommendation.e1_runtime import _october_package_clock

    cutoff = datetime.fromisoformat("2026-10-08T21:30:00+08:00")
    read = datetime.fromisoformat("2026-11-24T22:31:16+00:00")
    verified = datetime.fromisoformat("2026-11-24T22:31:17+00:00")
    observed = datetime.fromisoformat("2026-11-24T22:40:53+00:00")
    digest = "sha256:" + "a" * 64
    member = SimpleNamespace(member_id="bar", digest=digest, available_at=verified)
    request = SimpleNamespace(
        facts_template={
            "intake_received_at": read,
            "intake_first_read_at": read,
            "intake_verified_at": verified,
        },
        member_evidence=[member],
        required_member_ids=["bar"],
    )
    row = dict(
        member_id="bar",
        sha_match=True,
        sha256=digest,
        verify_at=verified.isoformat(),
        relevant_for_T=True,
    )
    evidence = {
        "member_consumption": {"run_read_at": read.isoformat(), "members": [row]},
        "source_clocks": {"logical_decision_cutoff": cutoff.isoformat()},
        "envelope": {"sha256": digest},
    }
    args = (
        request, evidence, read, observed, cutoff, digest, {"consumer_rule_version": "1.0.0"},
        {"bar": row},
    )
    proof = _october_package_clock(*args)
    assert proof["logical_cutoff"] == cutoff.isoformat()
    assert proof["intake_verified_at"] == verified.isoformat()
    evidence["source_clocks"]["logical_decision_cutoff"] = "2026-10-09T21:30:00+08:00"
    with pytest.raises(ProductDailyError, match="replay_package_clock_mismatch"):
        _october_package_clock(*args)
    evidence["source_clocks"]["logical_decision_cutoff"] = cutoff.isoformat()
    row["verify_at"] = "2026-11-25T22:31:17+00:00"
    with pytest.raises(ProductDailyError, match="replay_package_clock_mismatch"):
        _october_package_clock(*args)


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
    # These sealed-archive fixtures model a reader that retained its own clock.
    evidence = dict(p12_evidence or {})
    if not evidence.get("read_at") and not evidence.get("member_consumption"):
        evidence["read_at"] = observed_at or "2026-11-25T02:00:00+08:00"
    return run_e1_replay_day(
        req,
        day,
        assets=assets,
        snapshot_id="s",
        snapshot_manifest_digest="sha256:" + "a" * 64,
        observed_clock=observed_clock,
        p12_evidence=evidence,
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
    """R6: a caller may only REQUEST sessions; the pinned consumer rule
    supplies the trusted basis/digests (a projection of P10 RESULT 722c75b2).
    The bound closure carries the rule's own identity, the proven per-session
    basis, named source-document digests and UNKNOWN instants -- never a
    caller-supplied basis or a T-known claim."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    # Caller supplies ONLY venue+session_date requests; a claimed basis or
    # source doc is ignored/proven by the pinned rule, not trusted.
    ev = {
        "sessions": [
            {"venue_id": "SSE", "session_date": "2026-10-08"},
            {"venue_id": "SSE", "session_date": "2026-09-30"},
        ],
    }
    out = e_run_e1_replay(req, f.T1, assets, calendar_schedule_evidence=ev)
    block = out["source_closure"]["consumption_evidence"]["calendar_schedule_evidence"]
    # The rule's own identity, never the caller's.
    assert block["consumer_rule_id"] == "p11.e1-october-calendar-consume"
    assert block["consumer_rule_version"] == "1.0.0"
    assert block["p10_result_ref"]["sha256"].startswith("sha256:722c75b2")
    assert block["announced_before_T"] is False
    sessions = {s["session_date"]: s for s in block["sessions"]}
    assert sessions["2026-10-08"]["schedule_basis"] == "EXPLICIT_EXCHANGE_SCHEDULE"
    assert sessions["2026-10-08"]["source_published_at_state"] == "UNKNOWN_EXACT_INSTANT"
    assert sessions["2026-10-08"]["source_documents"][0]["notice_id"] == "上证公告〔2025〕45号"
    assert sessions["2026-09-30"]["schedule_basis"] == "RULE_DERIVED_SCHEDULE"


def test_r6_calendar_schedule_evidence_conflict_refused(assets):
    """R6 fail-first: a caller-supplied basis that conflicts with the trusted
    rule is refused; so is a caller-named rule id/version (ignored in favour
    of the rule's own identity)."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {
        "consumer_rule_id": "invented-rule",
        "consumer_rule_version": "99",
        "sessions": [
            {
                "venue_id": "SSE",
                "session_date": "2026-10-08",
                "schedule_basis": "not_an_exchange_basis",
                "source_documents": [
                    {"notice_id": "invented", "sha256": "0" * 64, "publication_date": "2026-12-01"}
                ],
            }
        ],
    }
    with pytest.raises(ProductDailyError) as ei:
        e_run_e1_replay(req, f.T1, assets, calendar_schedule_evidence=ev)
    assert "calendar_schedule_evidence_conflict" in str(ei.value)


def test_r6_calendar_schedule_evidence_unproven_session_refused(assets):
    """R6 fail-first: a session the bound calendar carries but the trusted
    rule does NOT prove (e.g. a date outside the pinned P10 scope) is a named
    refusal, never a silently-adopted basis."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {"sessions": [{"venue_id": "SSE", "session_date": "2026-10-07"}]}
    with pytest.raises(ProductDailyError) as ei:
        e_run_e1_replay(req, f.T1, assets, calendar_schedule_evidence=ev)
    assert "calendar_schedule_evidence_unproven" in str(ei.value)


def test_r6_calendar_schedule_evidence_unbound_session_refused(assets):
    """R6 fail-first: schedule evidence for a session the bound calendar does
    not carry is refused, never silently adopted."""
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    ev = {
        "sessions": [
            {"venue_id": "SSE", "session_date": "2031-01-01"}  # not a bound calendar_row
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


def test_rerun_cannot_use_a_calendar_version_created_after_envelope_read(assets):
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    security = req["securities"][0]
    security["knowledge_clock_role"] = "calendar_source_knowledge_from"
    rows = {
        row["session_date"]: row
        for row in req["calendar_rows"]
        if row["venue_id"] == security["identity"]["venue_id"]
    }
    version = "2026-11-25T00:00:00+00:00"
    for bar in security["bars"][-61:]:
        day = bar["session_date"]
        rows[day]["knowledge_from"] = version
        security["knowledge_cutoff_by_date"][day] = version
    rerun = "2026-11-26T00:00:00+00:00"

    def run(source_read_at):
        return e_run_e1_replay(
            req,
            f.T1,
            assets,
            observed_at=rerun,
            p12_evidence={
                "read_at": source_read_at,
                "member_consumption": {"run_read_at": source_read_at},
            },
        )

    refused = run("2026-11-24T00:00:00Z")
    assert not refused.get("publishable") and not refused.get("candidates")
    allowed = run("2026-11-25T01:00:00Z")
    assert allowed["publishable"] and allowed["candidates"]
    assert (
        allowed["source_closure"]["consumption_evidence"]["actual_read_at"]
        == "2026-11-25T01:00:00+00:00"
    )
    assert allowed["replay_observed_at"] == "2026-11-26T00:00:00+00:00"


def test_shenzhen_preholiday_date_binds_weekly_rule_and_annual_notice(assets):
    from tests.unit import test_p11_paper_replay as f

    req = _sealed_member_request()
    day = "2026-09-30"
    original = next(
        r for r in req["calendar_rows"] if r["session_date"] == day and r["venue_id"] == "SSE"
    )
    req["calendar_rows"].append({**original, "venue_id": "SZSE"})
    day_result = e_run_e1_replay(
        req,
        f.T1,
        assets,
        calendar_schedule_evidence={"sessions": [{"venue_id": "SZSE", "session_date": day}]},
    )
    evidence = day_result["source_closure"]["consumption_evidence"]["calendar_schedule_evidence"]
    (record,) = evidence["sessions"]
    assert record["schedule_basis"] == "RULE_DERIVED_SCHEDULE"
    assert {doc["kind"] for doc in record["source_documents"]} == {"WEEKLY_RULE", "ANNUAL_CLOSURES"}


def test_missing_original_read_never_borrows_the_rerun():
    from quanthus.product_daily.recommendation.e1_runtime import _envelope_read_at

    t = datetime.fromisoformat("2026-10-08T21:30:00+08:00")
    physical_read = datetime.fromisoformat("2026-11-24T22:31:00+00:00")
    rerun = datetime.fromisoformat("2026-11-26T12:00:00+00:00")
    assert _envelope_read_at(None, rerun, t, t) == t
    with pytest.raises(ProductDailyError, match="missing_original_read_at"):
        _envelope_read_at(None, rerun, physical_read, t)


@pytest.mark.parametrize("unknown_implemented", [False, True])
def test_october_factor_ledger_survives_action_isolation(assets, unknown_implemented):
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    good, affected = (f._security(c, days) for c in ("600000", "600001"))
    row = {
        "ledger": "P1_factor_pit_exception",
        "stock_code": "600001",
        "exchange": "SH",
        "effective_date": days[-10].isoformat(),
        "knowledge_cutoff": days[-4].isoformat(),
        "exception_type": "factor_pit_visibility",
        "source_revision": "adj_factors_pit_exception_ledger_v1|bs_adj_v1",
        "reason_code": "no_proveable_announce_after_em_exact_link",
    }
    affected["exceptions"] = [row]
    if unknown_implemented:
        affected["actions"] = [
            {"action_status": "IMPLEMENTED", "ex_date": f.T1.isoformat(), "terms": {}}
        ]
    req = f._request(
        market_dates=(f.T1,), securities=[good, affected],
        coverage="verified" if unknown_implemented else "partial",
    )
    req["listing_rows"].append(f._listing("security-600001"))
    day = e_run_e1_replay(req, f.T1, assets)
    assert day["publishable"] and day["delivered"]["600000"]["entry_eligible"]
    assert not day["delivered"]["600001"]["entry_eligible"]
    assert day["coverage_isolated"] == 1
    closed = day["source_closure"]
    assert day["receipt"]["source_closure_digest"] == sha256_digest(closed)
    assert day["coverage_exclusions"][0]["source_exceptions"] == [row]
    assert day["coverage_exclusions"][0] in closed["exclusions"]
    assert closed["consumption_evidence"]["p1_factor_visibility"]["restrictions"] == [
        {"security_code": "600001", "source_row": row}
    ]


@pytest.mark.parametrize(
    "subject_basis,ledger_failure,expected",
    [
        ("qfq", "missing", "p1_factor_ledger_unverified"),
        ("hfq", "bad_sha", "p1_factor_ledger_unverified"),
        ("qfq", None, "replay_price_basis_not_admitted"),
        (None, "bad_sha", "p1_factor_ledger_unverified"),
        ("none", "missing", "native_prices_independent"),
        ("UNADJUSTED", "bad_sha", "native_prices_independent"),
    ],
)
def test_public_replay_checks_price_inputs_before_entry(
    assets, subject_basis, ledger_failure, expected
):
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    subject, neighbor = (f._security(c, days) for c in ("600000", "600001"))
    req = f._request(market_dates=(f.T1,), securities=[subject, neighbor], coverage="verified")
    req["listing_rows"].append(f._listing("security-600001"))
    req["facts_template"]["price_basis"] = "qfq"
    for sec in (subject, neighbor):
        for bar in sec["bars"]:
            bar["price_basis"] = "none"
    subject["bars"][-2]["price_basis"] = subject_basis
    if ledger_failure:
        subject["dependency_reasons"] = {
            "p1_factor_ledger": "unverified_or_missing",
            "factor_dependent_actions": "blocked",
        }
    stamp = "2026-11-25T00:34:06.373370Z"
    read = {
        "member_id": "ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet",
        "status": "SHA_MISMATCH" if ledger_failure else "READ",
        "actual_sha256": None if ledger_failure == "missing" else "sha256:" + "e" * 64,
        "verified_at": stamp,
    }
    day = e_run_e1_replay(req, f.T1, assets, p12_evidence={"p1_exceptions": {"reads": [read]}})
    admitted = expected == "native_prices_independent"
    assert day["publishable"] and day["delivered"]["600001"]["entry_eligible"]
    assert day["delivered"]["600000"]["entry_eligible"] is admitted
    entries = {d["security_code"] for d in day["entry_decisions"] if d["status"] == "entry"}
    assert entries == ({"600000", "600001"} if admitted else {"600001"})
    closure = day["source_closure"]
    assert closure["price_basis"] == "none"
    ce = closure["consumption_evidence"]
    special = ce["p12"]["p1_exceptions"]
    if ledger_failure:
        assert special["reads"] == [read]
    else:
        # The optional success claim is unpaired in this fixture.
        assert special["p1_unverified"] and not special["reads"]
        assert special["reported_evidence"]["reads"] == [read]
    (item,) = ce["p1_factor_visibility"]["price_inputs"]
    assert item["disposition"] == expected
    assert item["dependency_reasons"] == subject.get("dependency_reasons", {})
    assert day["receipt"]["source_closure_digest"] == sha256_digest(closure)
    if not admitted:
        (isolation,) = day["coverage_exclusions"]
        assert isolation["reason"] == expected and isolation in closure["exclusions"]


@pytest.mark.parametrize(
    "change",
    [
        None,
        "late",
        "early",
        "different_time_in_window",
        "digest",
        "source_digest",
        "different_package",
        "ordinary_package",
        "duplicate_read",
        "duplicate_ordinary",
    ],
)
def test_p1_read_pairs_identity_source_digest_and_original_clocks(change):
    from types import SimpleNamespace

    from quanthus.product_daily.recommendation.e1_runtime import _p1_read_evidence

    mid = "ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet"
    key = "base-ods::" + mid
    digest = "sha256:" + "d" * 64
    start, verified, observed = [
        datetime.fromisoformat("2026-11-25T00:" + hm + "+00:00")
        for hm in ("33:50", "34:06", "36:00")
    ]
    special = {
        "member_id": mid,
        "member_key": key,
        "status": "READ",
        "sha_match": True,
        "verified_at": verified.isoformat(),
        "sha256": digest,
    }
    row = {
        "member_id": mid,
        "package_key": key,
        "status": "READ",
        "sha_match": True,
        "verify_at": verified.isoformat(),
        "sha256": digest,
    }
    source = {"member_id": mid, "package_id": "base-ods", "sha256": digest}
    member = SimpleNamespace(member_id=mid, digest=digest, available_at=verified)
    request = SimpleNamespace(
        source_members=[source], member_evidence=[member], required_member_ids=[mid]
    )
    evidence = {
        "member_consumption": {"run_read_at": start.isoformat(), "members": [row]},
        "p1_exceptions": {"reads": [special]},
    }
    if change in ("late", "early", "different_time_in_window"):
        special["verified_at"] = {
            "late": "2026-11-25T00:38:00Z",
            "early": "2026-11-25T00:33:49Z",
            "different_time_in_window": "2026-11-25T00:34:05Z",
        }[change]
    elif change == "digest":
        special["sha256"] = "sha256:" + "c" * 64
    elif change == "source_digest":
        source["sha256"] = "sha256:" + "c" * 64
    elif change == "different_package":
        special["member_key"] = "other::" + mid
    elif change == "ordinary_package":
        row["package_key"] = "other::" + mid
    elif change == "duplicate_read":
        evidence["p1_exceptions"]["reads"].append(dict(special))
    elif change == "duplicate_ordinary":
        evidence["member_consumption"]["members"].append(dict(row))
    if change is None:
        assert _p1_read_evidence(request, evidence, start, observed) == evidence
        return
    with pytest.raises(ProductDailyError, match="replay_p1_read_mismatch"):
        _p1_read_evidence(request, evidence, start, observed)
    request.required_member_ids = []
    result = _p1_read_evidence(request, evidence, start, observed)["p1_exceptions"]
    assert result["p1_unverified"] and result["reads"] == []
    assert result["reported_evidence"] == evidence["p1_exceptions"]


@pytest.mark.parametrize("explicit_key", ["member_key", "package_key"])
@pytest.mark.parametrize("used", [True, False])
def test_ordinary_member_matches_its_own_registered_key(explicit_key, used):
    from types import SimpleNamespace

    from quanthus.product_daily.recommendation.e1_runtime import _ordinary_member_index

    p1 = "ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet"
    sources = [{"package_id": "base-ods", "member_id": mid} for mid in (p1, "other")]
    request = SimpleNamespace(source_members=sources)
    rows = [
        {**src, explicit_key: "base-ods::" + src["member_id"], "status": "READ"}
        for src in sources
    ]
    rows[1]["relevant_for_T"] = used
    assert set(_ordinary_member_index(request, {"members": rows})) == {p1, "other"}
    rows[1][explicit_key] = "base-ods::" + p1
    with pytest.raises(ProductDailyError, match="replay_member_identity_mismatch"):
        _ordinary_member_index(request, {"members": rows})


def test_ordinary_p1_failed_attempt_precedes_one_verified_read():
    from types import SimpleNamespace

    from quanthus.product_daily.recommendation.e1_runtime import _ordinary_member_index

    mid = "ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet"
    source = {"package_id": "base-ods", "member_id": mid}
    request = SimpleNamespace(source_members=[source])
    failed = {**source, "status": "SHA_MISMATCH", "sha_match": False}
    passed = {**source, "status": "READ", "sha_match": True}
    rows = {"members": [failed, passed]}
    assert _ordinary_member_index(request, rows) == {mid: passed}
    assert rows["members"] == [failed, passed]
    with pytest.raises(ProductDailyError, match="duplicate member receipt"):
        _ordinary_member_index(request, {"members": [failed, passed, passed]})
