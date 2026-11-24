# --- R9 action-authority: real Owner-signed members rebuild action_coverage ---
#
# Distinguishes the REAL entry from synthetic _bind_artifacts: the action_coverage
# is produced by build_signed_action_authority() from the delivered B02 member
# bytes + the versioned accepted-uses table, not caller-copied fields.  A forged
# member SHA / unsigned security-day / different-clause request is isolated.
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.paper_replay import (
    build_replay_envelope,
    build_signed_action_authority,
    load_replay_request,
)
from tests.unit.test_p11_paper_replay import (
    _request,
)

B02 = Path(
    "/Users/william/Documents/Codex/2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2"
)
ACC = Path(
    "/Users/william/Documents/Codex/2026-11-24/"
    "quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2"
)

B02_ID = dict(
    security_code="600000",
    listing_code="600000",
    stock_code="sh.600000",
    security_id="security_2e02245cf8b28319ec8af0e9554cdc24",
    listing_id="listing_5edd7b7cdd4b6445afae0d2cca6b85ce",
    venue_id="venue_bec59e31eb6bb28111a10d7e6d2c689f",
)
SIG = "2026-10-08"

pytestmark = pytest.mark.skipif(
    not (B02 / "RESULT.json").exists()
    or not (ACC / "ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json").exists(),
    reason="signed P10 member artefacts not present on this host",
)


def _real():
    pointer = json.loads((B02 / "RESULT.json").read_text())
    accepted = json.loads((ACC / "ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json").read_text())
    members = {
        pointer[n]["path"]: (B02 / pointer[n]["path"]).read_bytes()
        for n in ("events", "windows", "coverage")
    }
    return pointer, accepted, members


def _authority(**over):
    args = dict(
        security_code="600000",
        listing_code="600000",
        stock_code="sh.600000",
        security_id=B02_ID["security_id"],
        listing_id=B02_ID["listing_id"],
        venue_id=B02_ID["venue_id"],
        signal_date=SIG,
    )
    args.update(over)
    pointer, accepted, members = _real()
    return build_signed_action_authority(
        accepted=accepted, pointer_root=pointer, members=members, **args
    )


def test_r9_real_b02_derives_action_coverage():
    out = _authority()
    scan = [e for e in out["window_evidence"] if e["purpose"] == "scan_two_session"][0]
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert scan["event_state"] == "verified_none"
    assert [s["date"] for s in scan["scan_sessions"]] == ["2026-09-30", "2026-10-08"]
    assert ret["event_state"] == "verified_event"
    assert len(ret["sessions"]) == 61
    assert ret["events"] == [
        {
            "ex_date": "2026-07-16",
            "cash_gross_per_share": "0.42",
            "bonus_share_ratio": "0",
            "transfer_share_ratio": "0",
            "currency": "CNY",
            "implementation_sha256": (
                "sha256:afe3ee2cf61dcd1e9e76d2b8d4a93a2cc16fd7ea27eb89da815b8a808f8de386"
            ),
            "register_row": 671,
            "notice": "1225416710",
        }
    ]
    cat = out["verified_artifacts"][0]
    assert cat["producer"] == "p10.signed-action-members.v1"
    assert cat["authority"]["batch_id"] == "SSE-02-R2"
    assert cat["rows"][0]["stock_code"] == "sh.600000"
    assert out["registers"][0]["source_member_id"] == "coverage.json"


def test_r9_member_sha_mismatch_fail_closed():
    pointer, accepted, members = _real()
    members[pointer["events"]["path"]] = b"{}"  # substituted bytes
    with pytest.raises(ProductDailyError) as exc:
        build_signed_action_authority(
            accepted=accepted,
            pointer_root=pointer,
            members=members,
            security_code="600000",
            listing_code="600000",
            stock_code="sh.600000",
            security_id=B02_ID["security_id"],
            listing_id=B02_ID["listing_id"],
            venue_id=B02_ID["venue_id"],
            signal_date=SIG,
        )
    assert str(exc.value.args[0]).startswith("action_authority_member_sha_mismatch")


def test_r9_unsigned_security_day_fail_closed():
    with pytest.raises(ProductDailyError) as exc:
        _authority(signal_date="2026-10-30")  # not a signed date
    assert str(exc.value.args[0]).startswith("action_authority_security_day_not_signed")
    with pytest.raises(ProductDailyError) as exc2:
        _authority(listing_code="601398")  # signed date, unsigned code
    assert str(exc2.value.args[0]).startswith("action_authority_security_day_not_signed")


def test_r9_identity_mismatch_fail_closed():
    with pytest.raises(ProductDailyError) as exc:
        _authority(venue_id="venue_wrong")
    assert str(exc.value.args[0]).startswith("action_authority_identity_mismatch")


def _request_with_authority(signal_date="2026-10-08"):
    """Inject the derived action_coverage into a real replay request for 600000.

    The synthetic _security/_request helpers build bars on the bound SESSION_DAYS
    calendar; we replace only the action_coverage with the member-derived wire
    and set the security identity to the real B02 triple + sh.600000 stock_code
    so the bound record proves this exact security.  The projected action row
    maps the proven 0.42 gross dividend (ex_date inside (first_close, last_close]).
    """
    t = date.fromisoformat(signal_date)
    req = _request(market_dates=(t,), coverage="unknown")
    auth = _authority(signal_date=signal_date)
    # The signed member's window sessions are the REAL venue sessions (XSHG);
    # the bound open_sessions calendar must contain them verbatim.  Rebuild the
    # open calendar from the signed window sessions (the scan pair is a subset
    # of the returns 61); SESSION_DAYS already covers them contiguously except
    # real XSHG holidays, so use the member's own session list for the window.
    auth_sessions = [
        e["sessions"] for e in auth["window_evidence"] if e["purpose"] == "returns_61_session"
    ][0]
    # The bound open_sessions calendar IS the signed venue calendar: use the
    # member's own 61-session returns window verbatim (its last two days are the
    # scan pair T-1/T), then append the next real session so T has a next open.
    opens = list(dict.fromkeys(auth_sessions))  # preserve member order
    opens = [d for d in opens if d <= t.isoformat()]
    assert opens[-1] == t.isoformat() and len(opens) >= 61
    opens = opens + ["2026-10-09"]
    req["open_sessions"] = opens
    req["final_day_policy"] = None
    req["action_coverage"] = auth
    ident = req["securities"][0]["identity"]
    ident["security_id"] = B02_ID["security_id"]
    ident["listing_id"] = B02_ID["listing_id"]
    ident["venue_id"] = B02_ID["venue_id"]
    ident["stock_code"] = B02_ID["stock_code"]
    ident["listing_code"] = B02_ID["listing_code"]
    for row in req["listing_rows"]:
        if row["security_id"] == "security-600000":
            row["security_id"] = B02_ID["security_id"]
            row["listing_id"] = B02_ID["listing_id"]
            row["venue_id"] = B02_ID["venue_id"]
    ex = "2026-07-16"
    req["securities"][0]["actions"] = [
        {
            "security_code": "sh.600000",
            "effective_date": ex,
            "cash_dividend_per_share": "0.42",
            "bonus_share_ratio": "0",
            "known_at": (date.fromisoformat(ex) - timedelta(days=6)).isoformat(),
        }
    ]
    return req, t


def test_r9_real_authority_admitted_end_to_end():
    req, t = _request_with_authority()
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, t)
    assert window_info["coverage_isolated"] == 0


def test_r9_same_member_different_clause_isolated():
    # Same trusted member bytes but the request declares a different clause
    # (0.84 instead of the proven 0.42) - the action does not match the bound
    # original terms, so the security isolates rather than being admitted on
    # the strength of the trusted source.
    req, t = _request_with_authority()
    ex = "2026-07-16"
    req["securities"][0]["actions"] = [
        {
            "security_code": "sh.600000",
            "effective_date": ex,
            "cash_dividend_per_share": "0.84",
            "bonus_share_ratio": "0",
            "known_at": (date.fromisoformat(ex) - timedelta(days=6)).isoformat(),
        }
    ]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, t)
    assert window_info["coverage_isolated"] == 1


def test_r9_unsigned_member_copy_not_admitted():
    # A caller that copies the DERIVED fields into a hand-built request (rather
    # than calling the real assembly) still must carry the bound provenance;
    # stripping the artifact catalogue leaves the verified_event unprovable.
    req, t = _request_with_authority()
    req["action_coverage"]["verified_artifacts"] = []
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, t)
    assert window_info["coverage_isolated"] == 1
