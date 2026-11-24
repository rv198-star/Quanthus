# --- R9/R9b action-authority: rebuild action_coverage from real Owner-signed
# P10 member bytes resolved through the pinned release MANIFEST. ---
#
# Distinguishes the REAL entry from synthetic _bind_artifacts: the action_coverage
# is produced by build_signed_action_authority() from the actual batch RESULT +
# member bytes located inside the MANIFEST members map (path -> declared sha256),
# not caller-copied fields or recomputed JSON digests.  A substituted member /
# unsigned security-day / different-clause request fails closed.
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.paper_replay import (
    bind_signed_manifest,
    build_replay_envelope,
    build_signed_action_authority,
    load_replay_request,
    project_signed_actions,
)
from tests.unit.test_p11_paper_replay import (
    _request,
)

NODE_RELEASES = Path("/srv/quanthus-base22-data/delivery/p10-company-actions")
READER = Path(
    os.environ.get(
        "QUANTHUS_P11_ACTION_RELEASE_ROOT",
        NODE_RELEASES
        if NODE_RELEASES.is_dir()
        else Path.home() / "Documents/Codex/2026-11-25/quanthus-p10-action-supply/reader-packages",
    )
)

# B02 release pins the SSE spdb-r2 batch; B10 pins the SZSE batch-pilots batch.
SSE_BATCH = "2026-11-25/quanthus-p10-action-supply/sse/batch-02-spdb-r2"
SZSE_BATCH = "2026-11-25/quanthus-p10-action-supply/szse/batch-pilots-2x15-october-v2"
ACCEPTED_MEMBER = (
    "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2/ACCEPTED-OCTOBER-ACTION-COVERAGE.v2.json"
)
WINDOW_INDEX_MEMBER = (
    "2026-11-24/quanthus-p11-rule-release/october-action-feasibility-intake/"
    "terms-740-897-v2/WINDOW-USE.json"
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
    or not (READER / "company-actions-20261125-b02" / "MANIFEST.json").exists(),
    reason="signed P10 member artefacts not present on this host",
)


def _manifest(rel: str) -> tuple[dict, str, dict]:
    """Return (MANIFEST object, real file sha256, {member_path: declared sha256})."""
    p = READER / f"company-actions-20261125-{rel}" / "MANIFEST.json"
    b = p.read_bytes()
    sha = "sha256:" + hashlib.sha256(b).hexdigest()
    return json.loads(b), sha, json.loads(b)["members"]


def _read_members(manifest: dict, member_keys: list[str]) -> dict[str, bytes]:
    """Read the raw member bytes for the named MANIFEST member paths.

    member_keys are the MANIFEST members-map keys (source_prefix-relative paths);
    each maps to the same path under the source_prefix root.
    """
    root = READER / manifest["release"] / "source-tree"
    if not root.is_dir():
        root = Path(manifest["source_prefix"])
    out: dict[str, bytes] = {}
    for rel in member_keys:
        manifest["members"][rel]
        p = root / rel
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
    index_member: str | None = None,
    routes_sha: str | None = None,
    bound_manifest: bool = False,
):
    manifest, manifest_sha, members_map = _manifest(rel)
    acc_manifest = manifest
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
    if bound_manifest:
        # #749 once-verified path: recompute the MANIFEST bytes -> bound object.
        manifest_bytes = (READER / f"company-actions-20261125-{rel}" / "MANIFEST.json").read_bytes()
        manifest_sha = bind_signed_manifest(manifest_bytes, manifest_sha)
    result_member = f"{batch_dir}/RESULT.json"
    if index_member is not None:
        # read the signed windows index bytes through whichever manifest pins it
        idx_src = manifest if index_member in members_map else acc_manifest
        members.update(_read_members(idx_src, [index_member]))
    return build_signed_action_authority(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        accepted_member=accepted_member,
        result_member=result_member,
        members=members,
        accepted_manifest_members=acc_members,
        index_member=index_member,
        security_code=ident["listing_code"],
        listing_code=ident["listing_code"],
        stock_code=ident["stock_code"],
        security_id=ident["security_id"],
        listing_id=ident["listing_id"],
        venue_id=ident["venue_id"],
        signal_date=signal_date,
        routes_sha256=routes_sha,
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
    assert (
        auth["result_sha256"]
        == "sha256:c45cff4c9f606227bd52f0fd22e4643e36cca7d3735d1ca56a4b1dc18ad61da2"
    )
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


# --- action-signed-48-f3a9: the two index-only signed days + bound provenance --

WINDOW_INDEX_SHA = "sha256:0f98f60d69290f7d371994a0e744cced5f331ccd083a7eb1173a8d0d78e8ab13"
ROUTES_SHA = "sha256:418dc21d79d358a123df3c070cefac4d78562714d887b008960c71bdd793231c"
ACCEPTED_SHA = "sha256:dbb1da5c1c18e3b74dbf68293610a82832d76dc1cc99e6182c580068616bb13a"


def test_r9c_index_000002_10_08_verified_none():
    # 000002@10-08 is NOT in the SZSE batch security-windows member; the signed
    # WINDOW-USE index is its authoritative window record.  Both uses are
    # verified_none -> admitted with no action rows.
    out = _authority(
        "b10",
        SZSE_BATCH,
        SZSE_000002,
        "2026-10-08",
        index_member=WINDOW_INDEX_MEMBER,
    )
    scan = [e for e in out["window_evidence"] if e["purpose"] == "scan_two_session"][0]
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert scan["event_state"] == "verified_none"
    assert ret["event_state"] == "verified_none" and ret["events"] == []
    assert ret["sessions"][-1] == "2026-10-08"
    # bound provenance names the real file SHAs.
    prov = out["bound_provenance"]
    assert prov["accepted_sha256"] == ACCEPTED_SHA
    assert prov["windows_index_sha256"] == WINDOW_INDEX_SHA
    assert scan["member_sha256"] == ret["member_sha256"] == WINDOW_INDEX_SHA
    assert prov["manifest_sha256"].startswith("sha256:")
    assert prov["result_sha256"].startswith("sha256:")


def test_r9c_index_002040_10_08_first_day_event():
    # 002040@10-08: signed index proves ex_date 2026-07-28, cash 0.116 CNY in
    # the first day's 61-session return interval; 07-28 is inside (first_close,
    # last_close].
    out = _authority(
        "b10",
        SZSE_BATCH,
        SZSE_002040,
        "2026-10-08",
        index_member=WINDOW_INDEX_MEMBER,
    )
    ret = [e for e in out["window_evidence"] if e["purpose"] == "returns_61_session"][0]
    assert ret["event_state"] == "verified_event"
    assert ret["events"][0]["cash_gross_per_share"] == "0.116"
    assert ret["events"][0]["register_row"] == 302
    assert all(row["member_sha256"] == WINDOW_INDEX_SHA for row in out["window_evidence"])
    assert ret["events"][0]["implementation_sha256"] == (
        "sha256:abb747706e62ec2eb393d0fc5c725d834bb7f9ebafae024dcd22d8f565fbf2cf"
    )


def test_r9c_index_missing_without_index_fails_closed():
    # without the signed index the batch member alone cannot cover 10-08.
    with pytest.raises(ProductDailyError) as exc:
        _authority("b10", SZSE_BATCH, SZSE_000002, "2026-10-08")
    assert str(exc.value.args[0]).startswith("action_authority_window_missing")


def test_r9c_bound_manifest_object_and_routes():
    # #749 once-verified path: pass the bound-manifest object (proven file sha)
    # + the frozen ROUTES pin; the receipt carries real manifest/result/accepted
    # + routes SHAs.
    out = _authority(
        "b02",
        SSE_BATCH,
        SSE_ID,
        "2026-10-08",
        routes_sha=ROUTES_SHA,
        bound_manifest=True,
    )
    prov = out["bound_provenance"]
    assert prov["manifest_sha256"] == (
        "sha256:ad7212e95e8fffb0868c48707ab92dd55a6284d6f2d67f789b43cb237d3371ac"
    )
    assert prov["accepted_sha256"] == ACCEPTED_SHA
    assert prov["routes_sha256"] == ROUTES_SHA
    cred = out["window_evidence"][0]["credential"]
    assert cred["manifest_sha256"] == prov["manifest_sha256"]
    assert cred["accepted_sha256"] == ACCEPTED_SHA
    assert cred["routes_sha256"] == ROUTES_SHA


def test_r9c_manifest_pin_mismatch_fail_closed():
    # A MANIFEST byte whose recomputed sha differs from the frozen pin fails.
    with pytest.raises(ProductDailyError) as exc:
        bind_signed_manifest(b"{}", "sha256:" + "0" * 64)
    assert str(exc.value.args[0]).startswith("action_authority_manifest_pin_mismatch")


# --- oct08-action-projection-0043: raw-wire actions -> feature projection ------


def _raw_action(
    code, ex, cash, status="IMPLEMENTED", source="base22_f10/dividend_implemented", ann="2026-07-10"
):
    return {
        "corporate_action_id": f"ca_{code}_{ex}_{cash}",
        "security_id": None,
        "action_type": "CASH_DIVIDEND",
        "action_status": status,
        "ex_date": ex,
        "announced_at": ann,
        "record_source": source,
        "terms": {
            "CASH_AMOUNT_PER_SHARE": {"amount_per_share": cash, "currency": "CNY"},
            "BONUS_SHARES_PER_SHARE": {"ratio_numerator": "0", "ratio_denominator": "1"},
            "TRANSFER_SHARES_PER_SHARE": {"ratio_numerator": "0", "ratio_denominator": "1"},
        },
    }


def _index_002040_authority():
    return _authority(
        "b10", SZSE_BATCH, SZSE_002040, "2026-10-08", index_member=WINDOW_INDEX_MEMBER
    )


def test_r9d_projection_signed_terms_win_competing_units():
    # 002040@10-08: raw has IMPLEMENTED base22_f10 0.116 + dividend_history 1.160
    # (per-10) + PROPOSED.  The signed register proved 0.116 -> ONE feature row
    # with signed terms + competing versions kept as evidence.
    cov = _index_002040_authority()
    raw = [
        _raw_action("002040", "2026-07-28", "1.1600000000", source="dividend_history"),
        _raw_action("002040", "2026-07-28", "0.1160000000"),
        _raw_action("002040", None, "0", status="PROPOSED"),
    ]
    feats, aside = project_signed_actions(raw, SZSE_002040 | {"stock_code": "sz.002040"}, cov)
    assert len(feats) == 1
    assert feats[0]["effective_date"] == "2026-07-28"
    assert feats[0]["cash_dividend_per_share"] == "0.116"
    assert feats[0]["projection_provenance"] == "signed_register"
    assert feats[0]["security_code"] == "sz.002040"
    assert len(feats[0]["competing_terms"]) == 2
    # PROPOSED kept aside (not an adjustment; never bypasses negative check).
    assert any(a["disposition"] == "not_implemented_no_adjustment" for a in aside)


def test_r9d_projection_index_catalogue_row_binds_real_identity():
    # Index-derived catalogue rows must bind the real security/listing/venue,
    # not a literal "None" string (which broke _action_row_identity_matches).
    out = _index_002040_authority()
    rows = out["verified_artifacts"][0]["rows"]
    assert rows[0]["security_id"] == SZSE_002040["security_id"]
    assert rows[0]["listing_id"] == SZSE_002040["listing_id"]
    assert rows[0]["venue_id"] == SZSE_002040["venue_id"]


def test_r9d_projection_no_signed_match_uses_raw_terms_for_isolation():
    # No action_coverage: an IMPLEMENTED raw row still projects (its own terms)
    # so the verified_none negative check sees the unproven adjustment.
    raw = [_raw_action("600000", "2026-07-16", "0.4200000000")]
    feats, _ = project_signed_actions(raw, SSE_ID, None)
    assert len(feats) == 1 and feats[0]["projection_provenance"] == "raw_row_unsigned"
    assert feats[0]["cash_dividend_per_share"] == "0.4200000000"


# --- oct08-projection-r2-297b: owner binding + unresolvable IMPLEMENTED ------


def test_r2_projection_foreign_owner_not_consumed():
    # A raw IMPLEMENTED row naming a DIFFERENT security_id/listing_id is not
    # this security's action: it must not project terms nor pass silently - it
    # is kept-aside as an unexplained 'implemented_unknown'.
    cov = _index_002040_authority()
    raw = _raw_action("002040", "2026-07-28", "0.1160000000")
    raw["security_id"] = "security_other"
    raw["listing_id"] = "listing_other"
    feats, aside = project_signed_actions(
        raw if isinstance(raw, list) else [raw], SZSE_002040 | {"stock_code": "sz.002040"}, cov
    )
    assert feats == []
    kinds = {a.get("unknown_kind") for a in aside if a["disposition"] == "implemented_unknown"}
    assert "foreign_owner" in kinds


def test_r2_projection_implemented_no_terms_is_unknown_not_silenced():
    # IMPLEMENTED with terms={} and no signed ruling: unexplained (in-window
    # ex_date).  It must surface as implemented_unknown so the caller isolates,
    # not vanish into generic aside evidence.
    ident = SZSE_000002 | {"stock_code": "sz.000002"}
    raw = {
        "corporate_action_id": "ca_unknown",
        "security_id": ident["security_id"],
        "action_status": "IMPLEMENTED",
        "ex_date": "2026-09-15",
        "terms": {},
    }
    feats, aside = project_signed_actions([raw], ident, None)
    assert feats == []
    assert any(
        a["disposition"] == "implemented_unknown" and a.get("unknown_kind") == "no_terms"
        for a in aside
    )


def test_r2_projection_no_ex_date_is_unknown():
    ident = SZSE_000002 | {"stock_code": "sz.000002"}
    raw = {
        "corporate_action_id": "ca_noex",
        "security_id": ident["security_id"],
        "action_status": "IMPLEMENTED",
        "ex_date": None,
        "terms": {"CASH_AMOUNT_PER_SHARE": {"amount_per_share": "0.1", "currency": "CNY"}},
    }
    feats, aside = project_signed_actions([raw], ident, None)
    assert feats == []
    assert any(
        a["disposition"] == "implemented_unknown" and a.get("unknown_kind") == "no_ex_date"
        for a in aside
    )


def _wire_action(code, ident, ex, cash, owner=None):
    """A P12-style raw-wire action row (action_status/ex_date/terms.amount_per_share)."""
    own = dict(owner or {})
    return {
        "corporate_action_id": f"ca_{code}_{ex}",
        "action_type": "CASH_DIVIDEND",
        "action_status": "IMPLEMENTED",
        "ex_date": ex,
        "security_id": own.get("security_id", ident["security_id"]),
        "listing_id": own.get("listing_id", ident["listing_id"]),
        "venue_id": own.get("venue_id", ident["venue_id"]),
        "terms": {"CASH_AMOUNT_PER_SHARE": {"amount_per_share": cash, "currency": "CNY"}},
    }


def test_r2_envelope_foreign_owner_isolates_security():
    # wrong_raw_owner probe: the real signed authority stays valid, but the
    # consumed raw row names a different security_id -> the security is
    # coverage-isolated (entry_eligible False), not granted the signed terms.
    req, t = _request_with_authority("b02", SSE_BATCH, SSE_ID, "2026-10-08")
    req["securities"][0]["actions"] = [
        _wire_action(
            "600000",
            SSE_ID,
            "2026-07-16",
            "0.4200000000",
            owner={"security_id": "security_other", "listing_id": "listing_other"},
        )
    ]
    request = load_replay_request(req)
    _, _, wi, excl = build_replay_envelope(request, t)
    assert wi["coverage_isolated"] == 1
    assert wi["delivered"]["600000"]["entry_eligible"] is False
    assert any(e["security_code"] == "600000" for e in excl)


def test_r2_envelope_unknown_implemented_isolates_verified_none():
    # unknown_implemented probe: a verified_none security that nonetheless
    # carries an in-window IMPLEMENTED row with no terms -> isolated, the
    # verified_none label does not silence the unexplained event.
    req, t = _request_with_authority("b10", SZSE_BATCH, SZSE_000002, "2026-10-09")
    # 000002@10-08 is index-only; use the batch-covered 10-09 window (verified_none).
    auth = _authority("b10", SZSE_BATCH, SZSE_000002, "2026-10-09")
    opens = [
        e["sessions"] for e in auth["window_evidence"] if e["purpose"] == "returns_61_session"
    ][0]
    req["open_sessions"] = [d for d in opens if d <= "2026-10-09"] + ["2026-10-30"]
    req["market_dates"] = ["2026-10-09"]
    req["action_coverage"] = auth
    sec = req["securities"][0]
    sec["actions"] = [
        {
            "corporate_action_id": "ca_unknown",
            "action_status": "IMPLEMENTED",
            "ex_date": "2026-09-15",
            "security_id": SZSE_000002["security_id"],
            "terms": {},
        }
    ]
    request = load_replay_request(req)
    _, _, wi, excl = build_replay_envelope(request, t)
    assert wi["delivered"]["000002"]["entry_eligible"] is False
    assert any(e["security_code"] == "000002" for e in excl)
