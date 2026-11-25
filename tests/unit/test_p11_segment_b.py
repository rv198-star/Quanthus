"""#749 Segment-B: consumer projection for held/pending/unresolved accounts.

The B segment adapts a non-empty replay account into the consumer view:
per-order typed pending facts, a versioned serializable position_facts,
pool-external unresolved account records, nested-account conflict refusal
and sell-order explicit disposition.  All run through the public
run_e1_replay_day entry on the same fixed replay request.
"""

from __future__ import annotations

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.paper_replay import (
    build_replay_envelope,
    load_replay_request,
)
from tests.unit import test_p11_paper_replay as f
from tests.unit.test_p11_e1_runtime import (
    ROOT,
    e_run_e1_replay,
    load_e1_rule_assets,
)


@pytest.fixture(scope="module")
def assets():
    return load_e1_rule_assets(ROOT)


_DAYS = tuple(d for d in f.SESSION_DAYS if d <= f.T1)


def _req(securities, **extra):
    req = f._request(market_dates=(f.T1,), securities=securities)
    req["listing_rows"].append(f._listing("security-600001"))
    req.update(extra)
    return req


def _decisions(out):
    return [
        (x.get("security_code"), x.get("stage"))
        for ds in (out.get("package") or {}).get("decision_sets", [])
        for x in ds["decisions"]
    ]


# ---- held + position_facts dict ----------------------------------------------


def test_b_held_with_serializable_position_facts(assets):
    """A serializable position_facts dict is adapted into HeldPositionFacts;
    the held decision still fires and the neighbour keeps entry_plan."""
    sec = f._security("600000", _DAYS)
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts={
            "holding_sessions": 10,
            "marked_return": "-0.003",
            "prior_strategy_reduction_count": 0,
            "evidence": {"unit_id": "ledger-snap-74b96148"},
        },
    )
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    assert ("600000", "holding") in _decisions(out)
    assert ("600001", "entry_plan") in _decisions(out)


def test_b_held_unknown_quantity_and_no_bars(assets):
    """held_quantity=None keeps the held review with an unavailable reason;
    held-no-bars also stays held (facts decide membership, not bars)."""
    for kwargs in (
        {"held_quantity": None},
        {"held_quantity": 1200, "bars": []},
    ):
        sec = f._security("600000", _DAYS)
        sec.update(held_membership=True, **kwargs)
        out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
        assert ("600000", "holding") in _decisions(out)


# ---- per-order pending --------------------------------------------------------


def test_b_pending_only_no_entry_no_held(assets):
    """pending_only isolates the security's new entry without a held review;
    the neighbour keeps entry_plan."""
    sec = f._security("600000", _DAYS)
    sec.update(pending_kind="open_buy_order", pending_quantity=1200)
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    assert ("600001", "entry_plan") in _decisions(out)
    assert not any(c == "600000" for c, s in _decisions(out))
    assert not out.get("held_signals")


def test_b_multi_pending_records_verbatim(assets):
    """Multiple per-order pending records stay verbatim (never summed or
    last-one-wins); sell orders are NOT folded into buy-side pending."""
    sec = f._security("600000", _DAYS)
    sec.update(held_membership=True, held_quantity=1200)
    sec["pending"] = [
        {"kind": "open_buy_order", "quantity": 1200, "order_key": "ord-b1"},
        {"kind": "unconfirmed_fill", "quantity": 300, "order_key": "ord-b2"},
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    sc = out["source_closure"]
    assert "600000" in sc["pending_security_codes"]
    # Held + pending both land in account_membership for the same code.
    memb = [m for m in sc["account_membership"] if m["security_code"] == "600000"]
    assert memb and memb[0]["held"] is True and memb[0]["pending"] is True


def test_b_sell_order_goes_to_unresolved_not_pending(assets):
    """A sell order keeps direction+quantity verbatim but requires explicit
    consumer disposition: it lands on account_unresolved, never in the
    buy-side pending set."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [
        {"kind": "sell", "quantity": 5, "order_key": "ord-s1", "direction": "sell"},
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    sc = out["source_closure"]
    sell = [r for r in sc["account_unresolved"] if r.get("direction") == "sell"]
    assert sell and sell[0]["quantity"] == 5 and sell[0]["order_key"] == "ord-s1"
    assert "600000" not in sc["pending_security_codes"]


# ---- pool-external unresolved account records ----------------------------------


def test_b_pool_external_unresolved_preserved(assets):
    """Pool-external unresolved records are named verbatim inside the issued
    closure with their own role/quantity/missing_reason; a neighbour still
    evaluates and they never admit an entry or exit for that key."""
    sec = f._security("600000", _DAYS)
    req = _req([sec, f._security("600001", _DAYS)])
    req["account"]["unresolved"] = [
        {
            "account_key": "outside-held-x",
            "role": "held",
            "quantity": None,
            "missing_reason": "no_listing_identity",
            "source": "ledger-snapshot",
        },
        {
            "account_key": "outside-held-y",
            "role": "held",
            "quantity": 50,
            "missing_reason": "no_history_bars",
            "source": "ledger-snapshot",
        },
        {
            "account_key": "outside-pend",
            "role": "pending",
            "quantity": 5,
            "missing_reason": "sell_order_requires_explicit_disposition",
            "direction": "sell",
            "source": "ledger-snapshot",
        },
    ]
    out = e_run_e1_replay(req, f.T1, assets)
    sc = out["source_closure"]
    keys = {r["account_key"] for r in sc["account_unresolved"]}
    assert {"outside-held-x", "outside-held-y", "outside-pend"} <= keys
    # A pool-external key never receives a NEW entry; a held role may surface
    # its own hold_alert (named, not a fabricated market identity), while a
    # pending/sell role never produces any decision at all.
    decs = {c: s for c, s in _decisions(out)}
    assert decs.get("outside-pend") is None
    assert decs.get("outside-held-x") in (None, "holding")
    assert decs.get("outside-held-y") in (None, "holding")


# ---- nested account conflict ----------------------------------------------------


def test_b_nested_account_alone_is_unresolved_not_entry(assets):
    """A bare securities[].account block (no row-level markers) is NOT fill
    evidence: it lands on account_unresolved and never produces an entry."""
    sec = f._security("600000", _DAYS)
    sec["account"] = {"account_role": "held", "held_quantity": 1200, "pending": []}
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    sc = out["source_closure"]
    assert ("600001", "entry_plan") in _decisions(out)
    # The bare nested block is NOT fill evidence: 600000 gets no entry; its
    # held-role unresolved record surfaces the named hold_alert only.
    decs = dict(_decisions(out))
    assert decs.get("600000") in (None, "holding")
    unres = [r for r in sc["account_unresolved"] if r.get("security_code") == "600000"]
    assert unres and unres[0]["missing_reason"] == "nested_account_block_only_no_row_markers"


def test_b_nested_account_conflict_refused(assets):
    """A nested account block whose held_quantity diverges from the row-level
    markers is a projection conflict: the raw row is named and refused."""
    sec = f._security("600000", _DAYS)
    sec.update(held_membership=True, held_quantity=1200)
    sec["account"] = {"account_role": "held", "held_quantity": 999}
    with pytest.raises(ProductDailyError, match="account_projection_conflict"):
        e_run_e1_replay(_req([sec]), f.T1, assets)


# ---- envelope-level projection ---------------------------------------------------


def test_b_envelope_carries_pending_and_unresolved():
    """build_replay_envelope binds both B-segment lists into the raw-history
    envelope the public entry consumes."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [{"kind": "open_buy_order", "quantity": 7}]
    req = _req([sec])
    req["account"]["unresolved"] = [{"account_key": "ext-1", "role": "held", "quantity": 1}]
    env, _, _, _ = build_replay_envelope(load_replay_request(req), f.T1)
    assert env["pending_securities"][0]["kind"] == "open_buy_order"
    assert env["pending_securities"][0]["quantity"] == 7
    assert env["account_unresolved"][0]["account_key"] == "ext-1"


# ---- R1 fixes ----------------------------------------------------------------


def test_b_r1_sell_pending_blocks_new_entry(assets):
    """A sell order on a security blocks its NEW entry (the pool-external
    direction is an unresolved disposition, not a silently-admitted buy).
    The sell record stays verbatim in account_unresolved."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [
        {
            "kind": "sell",
            "quantity": 5,
            "order_key": "s1",
            "direction": "sell",
            "source_receipt": "src.s1",
        },
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    assert ("600001", "entry_plan") in _decisions(out)
    assert not any(c == "600000" for c, s in _decisions(out))
    sell = [r for r in out["source_closure"]["account_unresolved"] if r.get("direction") == "sell"]
    assert sell and sell[0]["quantity"] == 5 and sell[0]["order_key"] == "s1"


def test_b_r1_fractional_quantity_named_refusal(assets):
    """fractional_quantity=1.2 is a named invalid_pending_quantity, never
    silently truncated to 1."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [{"kind": "open_buy_order", "quantity": 1.2}]
    with pytest.raises(ProductDailyError, match="invalid_pending_quantity"):
        e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)


def test_b_r1_pool_external_held_surfaces_hold_alert(assets):
    """A pool-external held unresolved record produces a named hold_alert
    (never a silent drop, never a fabricated market identity)."""
    req = _req([f._security("600000", _DAYS), f._security("600001", _DAYS)])
    req["account"]["unresolved"] = [
        {
            "account_key": "outside-held",
            "role": "held",
            "quantity": None,
            "missing_reason": "missing_listing",
            "source": "src.o",
        },
    ]
    out = e_run_e1_replay(req, f.T1, assets)
    held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("outside-held", "hold_alert") in held
    assert any(
        r.get("account_key") == "outside-held" for r in out["source_closure"]["account_unresolved"]
    )


def test_b_r1_position_facts_without_source_refused(assets):
    """A serializable position_facts with no evidence.unit_id cannot invent
    a same-read source; it is a named invalid_raw_history."""
    sec = f._security("600000", _DAYS)
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts={
            "holding_sessions": 10,
            "marked_return": "-0.003",
            "prior_strategy_reduction_count": 0,
        },
    )
    out = e_run_e1_replay(_req([sec]), f.T1, assets)
    block = out.get("day_block") or {}
    assert block.get("reason") == "invalid_raw_history"
    assert "unit_id" in block.get("detail", "")


def test_b_r1_buy_pending_evidence_in_closure(assets):
    """order_key/source_receipt on a buy pending record are bound verbatim
    into the issued source_closure.pending_evidence."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [
        {
            "kind": "open_buy_order",
            "quantity": 12,
            "order_key": "ord-b1",
            "source_receipt": "src.b1",
        },
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    ev = [
        r
        for r in out["source_closure"].get("pending_evidence", [])
        if r.get("order_key") == "ord-b1"
    ]
    assert ev and ev[0]["source_receipt"] == "src.b1" and ev[0]["quantity"] == 12


# ---- #751/#752 cross-fix: held/pending coexist with per-security isolation -----


def _bad_digest_sec(code: str) -> dict:
    sec = f._security(code, _DAYS)
    sec["identity"] = dict(sec["identity"])
    sec["identity"]["source_evidence_digest"] = "malformed-digest"
    return sec


def _missing_digest_sec(code: str) -> dict:
    sec = f._security(code, _DAYS)
    sec["identity"] = dict(sec["identity"])
    sec["identity"]["source_evidence_digest"] = ""
    return sec


def test_b_752_held_member_survives_malformed_neighbour(assets):
    """A held member (751 protection) keeps its independent hold review even
    when a sibling's raw row carries a malformed digest (752 isolation): the
    bad member is a named per-security exclusion and the held outcome is not
    dropped or converted into an exit."""
    held = f._security("600000", _DAYS, held=True)
    bad = _bad_digest_sec("600001")
    out = e_run_e1_replay(_req([held, bad]), f.T1, assets)
    held_actions = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("600000", "holding") in held_actions or any(
        c == "600000" and s == "holding" for c, s in _decisions(out)
    )
    excl = [
        e for e in out["source_closure"].get("exclusions", []) if e.get("security_code") == "600001"
    ]
    assert excl and "EventContractError" in excl[0]["member_or_field"]
    assert excl[0].get("source_evidence_digest") == "malformed-digest"


def test_b_752_pending_member_survives_malformed_neighbour(assets):
    """A typed pending member stays isolated (no new entry) while the
    malformed neighbour is a named exclusion; pending never masquerades as a
    real fill and the neighbour's isolation does not leak into pending."""
    pend = f._security("600000", _DAYS)
    pend["pending"] = [
        {
            "kind": "open_buy_order",
            "quantity": 12,
            "order_key": "ord-x1",
            "source_receipt": "src.x1",
        },
    ]
    bad = _bad_digest_sec("600001")
    out = e_run_e1_replay(_req([pend, bad]), f.T1, assets)
    # pending blocks 600000's new entry without a held review; 600001 is
    # excluded alone, never evaluated.
    assert not any(c == "600000" for c, s in _decisions(out))
    excl = [
        e for e in out["source_closure"].get("exclusions", []) if e.get("security_code") == "600001"
    ]
    assert excl and "EventContractError" in excl[0]["member_or_field"]
    assert "600000" in out["source_closure"]["pending_security_codes"]


def test_b_752_mixed_bad_digest_keeps_clean_neighbour(assets):
    """Reliable-neighbour equivalence under B + 751/752: the legal security's
    complete published business fields are identical run alone vs mixed with
    a malformed-digest neighbour (not only the code set)."""
    solo = e_run_e1_replay(_req([f._security("600000", _DAYS)]), f.T1, assets)
    mixed = e_run_e1_replay(
        _req([f._security("600000", _DAYS), _bad_digest_sec("600001")]),
        f.T1,
        assets,
    )

    def entry(out):
        return [
            x
            for ds in (out.get("package") or {}).get("decision_sets", [])
            for x in ds["decisions"]
            if x.get("security_code") == "600000"
        ]

    se, me = entry(solo), entry(mixed)
    assert len(se) == len(me) == 1
    for field in (
        "opportunity_id",
        "stage",
        "action",
        "fraction",
        "price_bound",
        "mark_price",
        "reason_codes",
        "entry",
    ):
        assert se[0].get(field) == me[0].get(field), field
    # The mixed closure additionally names the excluded neighbour.
    assert any(
        e.get("security_code") == "600001" for e in mixed["source_closure"].get("exclusions", [])
    )
    assert not any(
        e.get("security_code") == "600001" for e in solo["source_closure"].get("exclusions", [])
    )


def test_b_752_missing_digest_mixed_also_isolated(assets):
    """An empty-digest sibling in a mixed replay run is isolated at the same
    per-security boundary: the raw_history_security_invalid receipt names
    the identity_parse breach verbatim (never silently dropped), while the
    legal neighbour still evaluates and gets its real entry.  The exclusion
    keys on the securities[] index because the malformed row never reached a
    usable identity -- the same named isolation semantics as the malformed
    EventContractError path, at the earlier identity-parse boundary."""
    out = e_run_e1_replay(
        _req([f._security("600000", _DAYS), _missing_digest_sec("600001")]),
        f.T1,
        assets,
    )
    excl = [
        e
        for e in out["source_closure"].get("exclusions", [])
        if e.get("security_code") == "securities[1]"
    ]
    assert excl and excl[0]["reason"] == "raw_history_security_invalid"
    assert "source_evidence_digest" in excl[0]["member_or_field"]
    assert excl[0]["scope"] == "per_security"
    # Legal neighbour still evaluates and publishes its real entry.
    assert ("600000", "entry_plan") in _decisions(out)
