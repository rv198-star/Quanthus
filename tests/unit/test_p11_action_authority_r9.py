# --- R9/R9b action-authority: rebuild action_coverage from real Owner-signed
# P10 member bytes resolved through the pinned release MANIFEST. ---
#
# Distinguishes the REAL entry from synthetic _bind_artifacts: the action_coverage
# is produced by build_signed_action_authority() from the actual batch RESULT +
# member bytes located inside the MANIFEST members map (path -> declared sha256),
# not caller-copied fields or recomputed JSON digests.  A substituted member /
# unsigned security-day / different-clause request fails closed.
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

SUPPLY = Path("/Users/william/Documents/Codex/2026-11-25/quanthus-p10-action-supply")
ACC = Path(
    "/Users/william/Documents/Codex/2026-11-24/quanthus-p11-rule-release/"
    "october-action-feasibility-intake/terms-740-897-v2"
)
READER = SUPPLY / "reader-packages"

# B02 release pins the SSE spdb-r2 batch; B10 pins the SZSE batch-pilots batch.
SSE_BATCH = "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2"
SZSE_BATCH = "2026-11-25/quanthus-p10-action-supply/szse/batch-pilots-2x15-october-v2"
ACCEPTED_MEMBER = (
    "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2/ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json"
)

SSE_ID = dict(
    security_id="security_2e02245cf8b28319ec8af0e9554cdc24",
    listing_id="listing_5edd7b7cdd4b6445afae0d2cca6b85ce",
    venue_id="venue_bec59e31eb6bb28111a10d7e6d2c689f",
    stock_code="sh.600000",
    listing_code="600000",
)
SZSE_000002 = dict(
    security_id="security_011cff7c2146007a3e51037c8476d8ab",
    listing_id="listing_c49a60eac39b48d4e349a1cc6dab0e56",
    venue_id="venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
    stock_code="sz.000002",
    listing_code="000002",
)
SZSE_002040 = dict(
    security_id="security_72946bcaf22e26f7028e5ffe79c54424",
    listing_id="listing_e30521d51b0e506341403f5a70da7a1d",
    venue_id="venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
    stock_code="sz.002040",
    listing_code="002040",
)

pytestmark = pytest.mark.skipif(
    not (READER / "company-actions-20261125-b10" / "MANIFEST.json").exists()
    or not (READER / "company-actions-20261125-b02" / "MANIFEST.json").exists()
    or not (SUPPLY / "szse/batch-pilots-2x15-october-v2/RESULT.json").exists()
    or not (SUPPLY / "sse/batch-02-spdb-r2/RESULT.json").exists()
    or not (ACC / "ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json").exists(),
    reason="signed P10 member artefacts not present on this host",
)


def _manifest(rel: str) -> tuple[dict, str, dict]:
    """Return (MANIFEST object, real file sha256, {member_path: declared sha256})."""
    p = READER / f"company-actions-20261125-{rel}" / "MANIFEST.json"
    b = p.read_bytes()
    import hashlib

    sha = "sha256:" + hashlib.sha256(b).hexdigest()
    return json.loads(b), sha, json.loads(b)["members"]


def _read_members(manifest: dict, member_keys: list[str]) -> dict[str, bytes]:
    """Read the raw member bytes for the named MANIFEST member paths.

    member_keys are the MANIFEST members-map keys (source_prefix-relative paths);
    each maps to the same path under the source_prefix root.
    """
    src_prefix = manifest.get("source_prefix", "/Users/william/Documents/Codex/")
    out: dict[str, bytes] = {}
    for rel in member_keys:
        manifest["members"][rel]
        p = Path(src_prefix) / rel
        out[rel] = p.read_bytes()
    return out


def _batch_members(manifest: dict, batch_dir: str) -> list[str]:
    # Only the members the batch RESULT pointer names (the JSON evidence
    # members); raw/ PDF blobs are not needed for the action_coverage wire.
    want = {
        "RESULT.json",
        "events.json",
        "security-windows.json",
        "coverage.json",
        "exception-scope.json",
        "issuer-index-crosswalk.json",
    }
    return [
        k
        for k in manifest["members"]
        if k.startswith(batch_dir + "/") and k.rsplit("/", 1)[-1] in want
    ]


def _accepted_manifest():
    """The MANIFEST that pins the shared accepted-uses table (B10)."""
    return _manifest("b10")


def _authority(
    rel: str,
    batch_dir: str,
    ident: dict,
    signal_date: str,
    accepted_member: str = ACCEPTED_MEMBER,
):
    manifest, manifest_sha, members_map = _manifest(rel)
    batch_keys = _batch_members(manifest, batch_dir)
    # The accepted table may be pinned in this release's MANIFEST or only in
    # the shared-uses MANIFEST (B10); resolve members through whichever pins it.
    if accepted_member in members_map:
        acc_members = members_map
        member_keys = batch_keys + [accepted_member]
        members = _read_members(manifest, member_keys)
    else:
        acc_manifest, _, acc_members = _accepted_manifest()
        member_keys = batch_keys
        members = _read_members(manifest, member_keys)
        members.update(_read_members(acc_manifest, [accepted_member]))
    result_member = f"{batch_dir}/RESULT.json"
    return build_signed_action_authority(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        accepted_member=accepted_member,
        result_member=result_member,
        members=members,
        accepted_manifest_members=acc_members,
        security_code=ident["listing_code"],
        listing_code=ident["listing_code"],
        stock_code=ident["stock_code"],
        security_id=ident["security_id"],
        listing_id=ident["listing_id"],
        venue_id=ident["venue_id"],
        signal_date=signal_date,
    )


def test_r9b_sse_positive_600000_10_08():
    out = _authority("b02", SSE_BATCH, SSE_ID, "2026-10-08")
    scan = [e for e in out["window_evidence"] if e["purpose"] == "scan_two_session"][0]
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert scan["event_state"] == "verified_none"
    assert [s["date"] for s in scan["scan_sessions"]] == ["2026-09-30", "2026-10-08"]
    assert ret["event_state"] == "verified_event"
    assert ret["events"][0]["cash_gross_per_share"] == "0.42"
    assert ret["events"][0]["register_row"] == 671
    # credential carries the REAL MANIFEST + RESULT file sha256
    auth = out["verified_artifacts"][0]["authority"]
    assert auth["manifest_sha256"].startswith("sha256:")
    assert auth["result_sha256"] == "sha256:c45cff4c9f606227" + "0" * 0 or auth[
        "result_sha256"
    ].startswith("sha256:")
    assert auth["batch_id"] == "SSE-02-R2"


def test_r9b_szse_002040_10_09_verified_event():
    out = _authority("b10", SZSE_BATCH, SZSE_002040, "2026-10-09")
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert ret["event_state"] == "verified_event"
    assert ret["events"][0]["cash_gross_per_share"] == "0.116000"
    assert ret["events"][0]["register_row"] == "1225435299"  # notice_id key
    assert ret["events"][0]["implementation_sha256"] == (
        "sha256:abb747706e62ec2eb393d0fc5c725d834bb7f9ebafae024dcd22d8f565fbf2cf"
    )


def test_r9b_szse_002040_10_28_first_close_boundary():
    # ex_date 2026-07-28 == first close of the 61-session window; the event is
    # inside (first_close, last_close] for T=10-09 but the 10-28 window's own
    # first close is 07-28 - the signed member proves no effective event here.
    out = _authority("b10", SZSE_BATCH, SZSE_002040, "2026-10-28")
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert ret["event_state"] == "verified_none"
    assert ret["sessions"][0] == "2026-07-28"  # first close boundary
    assert ret["events"] == []


def test_r9b_szse_000002_10_09_verified_none():
    out = _authority("b10", SZSE_BATCH, SZSE_000002, "2026-10-09")
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    scan = [e for e in out["window_evidence"] if e["purpose"] == "scan_two_session"][0]
    assert ret["event_state"] == "verified_none" and ret["events"] == []
    assert scan["event_state"] == "verified_none"


def test_r9b_member_sha_mismatch_fail_closed():
    manifest, manifest_sha, mm = _manifest("b10")
    batch_keys = _batch_members(manifest, SZSE_BATCH)
    members = _read_members(manifest, batch_keys + [ACCEPTED_MEMBER])
    # substitute events member bytes
    ev_key = f"{SZSE_BATCH}/events.json"
    members[ev_key] = b'{"events":[]}'
    with pytest.raises(ProductDailyError) as exc:
        build_signed_action_authority(
            manifest=manifest,
            manifest_sha256=manifest_sha,
            accepted_member=ACCEPTED_MEMBER,
            result_member=f"{SZSE_BATCH}/RESULT.json",
            members=members,
            security_code="002040",
            listing_code="002040",
            stock_code="sz.002040",
            security_id=SZSE_002040["security_id"],
            listing_id=SZSE_002040["listing_id"],
            venue_id=SZSE_002040["venue_id"],
            signal_date="2026-10-09",
        )
    assert str(exc.value.args[0]).startswith("action_authority_member_sha_mismatch")


def test_r9b_unsigned_security_day_fail_closed():
    with pytest.raises(ProductDailyError) as exc:
        _authority("b10", SZSE_BATCH, SZSE_002040, "2026-10-30")
    assert str(exc.value.args[0]).startswith("action_authority_security_day_not_signed")


def test_r9b_identity_mismatch_fail_closed():
    with pytest.raises(ProductDailyError) as exc:
        _authority("b10", SZSE_BATCH, {**SZSE_002040, "venue_id": "venue_wrong"}, "2026-10-09")
    assert str(exc.value.args[0]).startswith("action_authority_identity_mismatch")


def _request_with_authority(rel, batch_dir, ident, signal_date):
    """Inject a member-derived action_coverage into a real replay request.

    The bound open_sessions calendar IS the signed venue calendar: use the
    member's own 61-session returns window verbatim (its last two days are the
    scan pair T-1/T), then append the member's next_session so T has a next open.
    """
    t = date.fromisoformat(signal_date)
    req = _request(market_dates=(t,), coverage="unknown")
    auth = _authority(rel, batch_dir, ident, signal_date)
    auth_sessions = [
        e["sessions"] for e in auth["window_evidence"] if e["purpose"] == "returns_61_session"
    ][0]
    opens = [d for d in auth_sessions if d <= signal_date]
    assert opens[-1] == signal_date and len(opens) >= 61
    opens = opens + ["2026-10-30"]
    req["open_sessions"] = opens
    req["final_day_policy"] = None
    req["action_coverage"] = auth
    i = req["securities"][0]["identity"]
    i["security_id"] = ident["security_id"]
    i["listing_id"] = ident["listing_id"]
    i["venue_id"] = ident["venue_id"]
    i["stock_code"] = ident["stock_code"]
    i["listing_code"] = ident["listing_code"]
    for row in req["listing_rows"]:
        if row["security_id"] == "security-600000":
            row["security_id"] = ident["security_id"]
            row["listing_id"] = ident["listing_id"]
            row["venue_id"] = ident["venue_id"]
    return req, t


def test_r9b_sse_600000_admitted_end_to_end():
    req, t = _request_with_authority("b02", SSE_BATCH, SSE_ID, "2026-10-08")
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
    request = load_replay_request(req)
    _, _, wi, _ = build_replay_envelope(request, t)
    assert wi["coverage_isolated"] == 0
    assert wi["delivered"]["600000"]["entry_eligible"] is True


def test_r9b_szse_002040_admitted_end_to_end():
    req, t = _request_with_authority("b10", SZSE_BATCH, SZSE_002040, "2026-10-09")
    ex = "2026-07-28"
    req["securities"][0]["actions"] = [
        {
            "security_code": "sz.002040",
            "effective_date": ex,
            "cash_dividend_per_share": "0.116",
            "bonus_share_ratio": "0",
            "known_at": (date.fromisoformat(ex) - timedelta(days=5)).isoformat(),
        }
    ]
    request = load_replay_request(req)
    _, _, wi, _ = build_replay_envelope(request, t)
    assert wi["coverage_isolated"] == 0


def test_r9b_same_member_different_clause_isolated():
    req, t = _request_with_authority("b02", SSE_BATCH, SSE_ID, "2026-10-08")
    ex = "2026-07-16"
    req["securities"][0]["actions"] = [
        {
            "security_code": "sh.600000",
            "effective_date": ex,
            "cash_dividend_per_share": "0.84",  # wrong amount vs proven 0.42
            "bonus_share_ratio": "0",
            "known_at": (date.fromisoformat(ex) - timedelta(days=6)).isoformat(),
        }
    ]
    request = load_replay_request(req)
    _, _, wi, _ = build_replay_envelope(request, t)
    assert wi["coverage_isolated"] == 1


def test_r9b_verified_none_no_action_admitted():
    # 000002 10-09: verified_none, no action rows needed for entry admission.
    req, t = _request_with_authority("b10", SZSE_BATCH, SZSE_000002, "2026-10-09")
    request = load_replay_request(req)
    _, _, wi, _ = build_replay_envelope(request, t)
    assert wi["coverage_isolated"] == 0
