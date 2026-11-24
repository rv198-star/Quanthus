"""E1 replay runner: #749-shaped members -> run_e1_replay_day -> RunnerOutput.

The runner consumes ONLY the member payload of a p11.raw-history-input.v1
envelope plus the injected 8792 replay context; P11's public run_e1_replay_day
builds the single-day business rows, H1 input, coverage and isolation.  The
test builds the members from the shared synthetic paper-replay fixture so the
chain is the REAL P11 one (never a P12 stand-in).
"""

from __future__ import annotations

from dataclasses import replace as _replace
from datetime import date

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.p12_daily.e1_replay import (
    E1ReplayContext,
    E1ReplayRunner,
    build_replay_request,
)
from quanthus.p12_daily.rule_runner import RunnerRequest
from quanthus.product_daily.recommendation.e1_runtime import load_e1_rule_assets
from quanthus.shared.fact_contracts import FactError
from tests.unit import test_p11_paper_replay as fx

ROOT = fx.ROOT if hasattr(fx, "ROOT") else None


def _raw_history(market_date: date, req_members: dict) -> dict:
    return {
        "schema_version": "p11.raw-history-input.v1",
        "market_date": market_date.isoformat(),
        "decision_cutoff": req_members.get("decision_cutoff") or "",
        "source_members": req_members["source_members"],
        "calendar_rows": req_members["calendar_rows"],
        "listing_rows": req_members["listing_rows"],
        "securities": req_members["securities"],
    }


@pytest.fixture(scope="module")
def assets():
    if ROOT is None:
        pytest.skip("repo root fixture unavailable")
    return load_e1_rule_assets(ROOT)


def _context(req: dict) -> E1ReplayContext:
    return E1ReplayContext(
        scenario_id=req["scenario_id"],
        market_dates=tuple(req["market_dates"]),
        open_sessions=tuple(req["open_sessions"]),
        package_id=req["package_id"],
        manifest_sha256=req["manifest_sha256"],
        account=req["account"],
        policy=req["policy"],
        facts_template=req["facts_template"],
        snapshot_id="snap-e1-test",
        snapshot_manifest_digest=sha256_digest({"e1": "ctx"}),
        exclusions=tuple(req.get("exclusions") or ()),
        member_evidence=tuple(req.get("member_evidence") or ()),
        required_member_ids=tuple(req.get("required_member_ids") or ()),
        signal_window=req.get("signal_window"),
        final_day_policy=req.get("final_day_policy"),
        action_coverage=req.get("action_coverage"),
    )


def _request(raw_history) -> RunnerRequest:
    return RunnerRequest(
        snapshot=None,
        next_session=None,
        holdings=(),
        pending_securities=(),
        run_id="e1-replay-test-r1",
        raw_history=raw_history,
    )


def test_build_replay_request_projects_members_and_context():
    req = fx._request(market_dates=(fx.T1,))
    raw = _raw_history(fx.T1, req)
    built = build_replay_request(
        raw,
        scenario_id=req["scenario_id"],
        market_dates=tuple(req["market_dates"]),
        open_sessions=tuple(req["open_sessions"]),
        package_id=req["package_id"],
        manifest_sha256=req["manifest_sha256"],
        account=req["account"],
        policy=req["policy"],
        facts_template=req["facts_template"],
        run_id="r1",
        member_evidence=tuple(req["member_evidence"]),
        required_member_ids=tuple(req["required_member_ids"]),
        signal_window=req["signal_window"],
        final_day_policy=req["final_day_policy"],
        action_coverage=req["action_coverage"],
    )
    assert built["schema_version"] == "p11.paper-replay-request.v1"
    assert built["purpose_mode"] == "historical_replay"
    assert built["permitted_account_use"] == "personal_simulation"
    assert built["securities"] == req["securities"]
    assert built["source_members"] == req["source_members"]
    assert built["account"]["account_id"] == req["account"]["account_id"]


def test_e1_replay_runner_runs_real_chain_to_runner_output(assets):
    days = tuple(d for d in fx.SESSION_DAYS if d <= fx.T1)
    req = fx._request(
        market_dates=(fx.T1,),
        securities=[fx._security("600000", days)],
    )
    raw = _raw_history(fx.T1, req)
    runner = E1ReplayRunner(
        runner_ref_id="p11.e1-replay-day",
        runner_version="1.0.0",
        assets=assets,
        context=_context(req),
    )
    out = runner.run(_request(raw))
    assert out.publishable is True
    assert out.decision_set is not None
    assert out.source_closure is not None


def test_e1_replay_runner_carries_p12_evidence_into_closure(assets):
    days = tuple(d for d in fx.SESSION_DAYS if d <= fx.T1)
    req = fx._request(
        market_dates=(fx.T1,),
        securities=[fx._security("600000", days)],
    )
    raw = _raw_history(fx.T1, req)
    evidence = {
        "envelope": {"uri": "mem://749-envelope", "sha256": "ab" * 32},
        "read_receipt": {"uri": "mem://749-receipt", "sha256": "cd" * 32},
        "member_consumption": {"used_for_t": 138, "opened_unread": 17},
        "scenario_account": {"ledger_digest": "ef" * 32, "as_of": "2026-10-08T21:30:00+08:00"},
        "action_use": {"b02": ["member:ca-600000"], "b10": []},
    }
    ctx = _replace(_context(req), p12_evidence=evidence)
    runner = E1ReplayRunner(
        runner_ref_id="p11.e1-replay-day",
        runner_version="1.0.0",
        assets=assets,
        context=ctx,
    )
    out = runner.run(_request(raw))
    assert out.publishable is True
    closure = dict(out.source_closure or {})
    consumption = dict(closure.get("consumption_evidence") or {})
    assert consumption.get("p12") == evidence


def test_e1_replay_runner_blocks_missing_envelope(assets):
    req = fx._request(market_dates=(fx.T1,))
    runner = E1ReplayRunner(
        runner_ref_id="p11.e1-replay-day",
        runner_version="1.0.0",
        assets=assets,
        context=_context(req),
    )
    with pytest.raises(FactError):
        runner.run(_request({"schema_version": "wrong"}))

