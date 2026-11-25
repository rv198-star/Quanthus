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
from tests.unit.test_p11_daily_runner import (
    _raw_security,
)
from tests.unit.test_p11_daily_runner import (
    resources as resources,  # noqa: F401
)
from tests.unit.test_p11_daily_runner import (
    runner as runner,  # noqa: F401  (module-scoped fixture reuse)
)
from tests.unit.test_p11_e1_runtime import (
    ROOT,
    e_run_e1_replay,
    load_e1_rule_assets,
)
from tests.unit.test_p11_history_input import (
    _envelope,
    _listing,
    _run_kwargs,
    _security_entry,
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
    """A serializable position_facts dict carries no same-read verified
    account snapshot, so it can never qualify an exit -- the member degrades
    to a named hold_alert and the neighbour keeps entry_plan."""
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
    held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("600000", "hold_alert") in held


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
    """A serializable position_facts whose evidence cannot bind a same-read
    source cannot drive an exit -- but it must not abort the day either: the
    member degrades to a named hold_alert with the original reason, never a
    fabricated exit or a whole-day invalid_raw_history block."""
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
    # No whole-day block: the member stays an isolated hold_alert.
    assert not (out.get("day_block") or {}).get("reason")
    held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("600000", "hold_alert") in held
    memb = [
        m for m in out["source_closure"]["account_membership"] if m.get("security_code") == "600000"
    ]
    assert memb and memb[0].get("unavailable_reason") is not None


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


# ---- C8 fix R1/R2/R3: serializable position_facts can never qualify an exit --
#
# A JSON-serializable position_facts dict carries NO same-read verified
# account snapshot: a caller-declared account_id, a bare security field, a
# pool-external unresolved key, a neighbour's member or a generic member id
# are all self-reported strings, never an independently-read account fact.
# Only an already-typed HeldPositionFacts (the P12 verified snapshot
# projection) may drive the legal held exit.  Every wire dict - whatever its
# evidence block claims - degrades to a named hold_alert, and the held
# member/account facts are preserved verbatim.


def _typed_held_entry(code="600000", holding=40, marked="0.05", reductions=0):
    """A raw-history securities[] entry carrying an already-typed
    HeldPositionFacts (the P12 verified snapshot projection)."""
    from decimal import Decimal

    from quanthus.expert_strategy.position_risk import HeldPositionFacts
    from quanthus.expert_strategy.runner import position_facts_evidence

    sec = _security_entry(_raw_security(code))
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts=HeldPositionFacts(
            holding_sessions=holding,
            marked_return=Decimal(marked) if marked is not None else None,
            prior_strategy_reduction_count=reductions,
            evidence=position_facts_evidence(
                unit_id=f"acct-snap-{code}",
                holding_sessions=holding,
                marked_return=Decimal(marked) if marked is not None else None,
                prior_strategy_reduction_count=reductions,
            ),
        ),
    )
    return sec


def _serializable_pf(evidence=None, holding=40, marked="0.05", reductions=0):
    return {
        "holding_sessions": holding,
        "marked_return": marked,
        "prior_strategy_reduction_count": reductions,
        "evidence": evidence or {"unit_id": "ledger-snap-74b96148"},
    }


def _serializable_held(code="600000", evidence=None, holding=40, account=None):
    sec = f._security(code, _DAYS)
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts=_serializable_pf(evidence=evidence, holding=holding),
    )
    if account is not None:
        sec["account"] = account
    return sec


def test_c8_r3_typed_held_position_facts_still_exits(runner, resources):
    """Trusted internal path: an already-typed HeldPositionFacts (verified P12
    snapshot projection) drives the legal held exit - real action, not a bare
    hold_alert.  The replay wire dict is never this path; only the typed
    internal object is."""
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history,
    )

    env = _envelope(
        [_typed_held_entry("600000", holding=40), _security_entry(_raw_security("600001"))],
        calendar=None,
        listing=[
            _listing("security-600000"),
            _listing("security-600001"),
        ],
    )
    out = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    held = [d for d in out.seam.held_decisions if d.security_code == "600000"]
    assert held and held[0].action == "exit"
    stages = [d.stage for ds in out.result.package.decision_sets for d in ds.decisions]
    assert "exit_plan" in stages


def test_c8_r3_serializable_dict_same_account_id_alerts(assets):
    """R2 hole now closed: a serializable dict whose evidence.unit_id matches
    the caller-declared account.account_id is still untrusted - the string
    name is not a verified snapshot.  hold_alert, never an exit."""
    sec = _serializable_held(
        evidence={"unit_id": "acct-spdb-20261008-snap"},
        account={"account_id": "acct-spdb-20261008-snap"},
    )
    req = _req([sec, f._security("600001", _DAYS)])
    out = e_run_e1_replay(req, f.T1, assets)
    held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("600000", "hold_alert") in held
    assert not any(
        d.get("stage") == "exit_plan"
        for ds in out["package"]["decision_sets"]
        for d in ds.get("decisions", [])
        if d.get("security_code") == "600000"
    )


def test_c8_r3_serializable_dict_single_field_alerts(assets):
    """A serializable dict whose evidence names only ONE security field
    (security_id / venue_id) is still untrusted: the field is a self-reported
    identity, not a verified account snapshot."""
    for evidence in (
        {"unit_id": "security-600000"},
        {"unit_id": "SSE"},
        {"unit_id": "600000"},
    ):
        sec = _serializable_held(evidence=evidence)
        req = _req([sec, f._security("600001", _DAYS)])
        out = e_run_e1_replay(req, f.T1, assets)
        held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
        assert ("600000", "hold_alert") in held


def test_c8_r3_serializable_dict_pool_external_alerts(assets):
    """Pool-external unresolved source, neighbour member and generic member
    ids stay untrusted even when the dict declares a matching evidence id."""
    for evidence in (
        {"unit_id": "ledger-snapshot-x"},
        {"unit_id": "only-600001-x"},
        {"unit_id": "synthetic-daily-1d"},
    ):
        sec = _serializable_held(evidence=evidence)
        req = _req([sec, f._security("600001", _DAYS)])
        req["account"]["unresolved"] = [
            {"account_key": "outside-x", "role": "held", "quantity": 1, "source": "ledger-snapshot"}
        ]
        out = e_run_e1_replay(req, f.T1, assets)
        held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
        assert ("600000", "hold_alert") in held


def test_c8_r3_serializable_dict_changed_value_alerts(assets):
    """A changed declared value (or a recomputed self-consistent digest) still
    cannot qualify: the wire shape itself is unverifiable, so it always
    degrades to hold_alert."""
    sec = _serializable_held(evidence={"unit_id": "acct-spdb"}, holding=41)
    req = _req([sec, f._security("600001", _DAYS)])
    out = e_run_e1_replay(req, f.T1, assets)
    held = [(h.get("security_code"), h.get("action")) for h in out.get("held_signals", [])]
    assert ("600000", "hold_alert") in held


def test_c8_r3_serializable_dict_preserves_account_facts(assets):
    """The held member and its original account facts are preserved verbatim:
    membership stays a real held row, the degrade reason is named, the
    account_member does not disappear."""
    sec = _serializable_held(
        evidence={"unit_id": "acct-spdb"},
        account={"account_id": "acct-spdb"},
    )
    req = _req([sec, f._security("600001", _DAYS)])
    out = e_run_e1_replay(req, f.T1, assets)
    memb = [
        m for m in out["source_closure"]["account_membership"] if m.get("security_code") == "600000"
    ]
    assert memb and memb[0].get("unavailable_reason") is not None
    assert "hold_alert" in [
        h.get("action") for h in out.get("held_signals", []) if h.get("security_code") == "600000"
    ]


# ---- C8 fix R3: pending kind/direction is never silently rewritten -----------


def test_c8_r3_sell_direction_mismatch_keeps_original_fields(assets):
    """kind=sell + direction=buy is a named conflict that lands on
    account_unresolved with BOTH original fields verbatim (never rewritten
    to a clean sell); the security still cannot receive a new entry."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [
        {
            "kind": "sell",
            "direction": "buy",
            "quantity": 5,
            "order_key": "s-mismatch",
            "source_receipt": "src.s",
        },
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    assert not any(c == "600000" for c, s in _decisions(out))
    rec = [
        r for r in out["source_closure"]["account_unresolved"] if r.get("order_key") == "s-mismatch"
    ]
    assert rec and rec[0]["kind"] == "sell" and rec[0]["direction"] == "buy"
    assert rec[0]["missing_reason"] == "pending_kind_direction_conflict"


def test_c8_r3_buy_direction_mismatch_also_named(assets):
    """kind=open_buy_order + direction=sell is the symmetric named conflict;
    both original fields are kept verbatim."""
    sec = f._security("600000", _DAYS)
    sec["pending"] = [
        {
            "kind": "open_buy_order",
            "direction": "sell",
            "quantity": 5,
            "order_key": "b-mismatch",
            "source_receipt": "src.b",
        },
    ]
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    rec = [
        r for r in out["source_closure"]["account_unresolved"] if r.get("order_key") == "b-mismatch"
    ]
    assert rec and rec[0]["kind"] == "open_buy_order" and rec[0]["direction"] == "sell"
    assert rec[0]["missing_reason"] == "pending_kind_direction_conflict"


def test_c8_r3_consistent_directions_keep_working(assets):
    """sell+sell, sell+missing, buy+buy, buy+missing all keep their original
    contract behaviour; only an actual divergence is a named conflict."""
    for pending, expected_reason in (
        (
            {"kind": "sell", "direction": "sell", "quantity": 5, "order_key": "k1"},
            "sell_order_requires_explicit_disposition",
        ),
        (
            {"kind": "sell", "quantity": 5, "order_key": "k2"},
            "sell_order_requires_explicit_disposition",
        ),
        ({"kind": "open_buy_order", "direction": "buy", "quantity": 5, "order_key": "k3"}, None),
    ):
        sec = f._security("600000", _DAYS)
        sec["pending"] = [pending]
        out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
        if expected_reason:
            rec = [
                r
                for r in out["source_closure"]["account_unresolved"]
                if r.get("order_key") == pending["order_key"]
            ]
            assert rec and rec[0]["missing_reason"] == expected_reason
        else:
            assert "600000" in out["source_closure"]["pending_security_codes"]


# ---- C8 fix R4: held-existence is preserved when risk facts unverified -------
#
# A member that only declares a serializable position_facts dict (no explicit
# held_membership / held_quantity) is still a HELD record: its risk values are
# dropped (never a fabricated exit), but the held EXISTENCE must block a new
# entry and surface a named hold_alert, with quantity kept unknown.  The same
# declared-held record plus an explicit not-held marker (held_membership=False
# or held_quantity=0) restores the contradictory_account_membership refusal.
# Verified held fills (typed HeldPositionFacts) keep their legal exit path.


def _dict_only_held(code="600000", holding=40, marked="0.05", reductions=0):
    """A securities entry that declares ONLY a position_facts dict - no
    held_membership and no held_quantity markers."""
    sec = f._security(code, _DAYS)
    sec["position_facts"] = _serializable_pf(holding=holding, marked=marked, reductions=reductions)
    return sec


def _stages(out, code="600000"):
    return [
        d.get("stage")
        for ds in (out.get("package") or {}).get("decision_sets", [])
        for d in ds.get("decisions", [])
        if d.get("security_code") == code
    ]


def _held_action(out, code="600000"):
    return [h.get("action") for h in out.get("held_signals", []) if h.get("security_code") == code]


def _membership(out, code="600000"):
    return [
        m
        for m in out["source_closure"].get("account_membership", [])
        if m.get("security_code") == code
    ]


def test_c8_r4_position_facts_only_blocks_entry_holds(assets):
    """Core P1: a member with ONLY a position_facts dict (existence declared,
    risk unverified, quantity omitted) must NOT earn a new entry - it is a
    declared-held record that yields a named hold_alert and holds, with no
    new entry / reduce / exit opportunity and quantity kept unknown."""
    out = e_run_e1_replay(_req([_dict_only_held(), f._security("600001", _DAYS)]), f.T1, assets)
    # No new entry, reduce or exit for 600000; the held review surfaces.
    assert _stages(out) == ["holding"]
    assert _held_action(out) == ["hold_alert"]
    assert not any(
        d.get("exit_kind") or d.get("entry")
        for ds in (out.get("package") or {}).get("decision_sets", [])
        for d in ds.get("decisions", [])
        if d.get("security_code") == "600000"
    )
    # The account record is auditable: held existence preserved, quantity stays
    # explicitly unknown (None, never a fabricated 0), source marks unverified.
    memb = _membership(out)
    assert memb and memb[0].get("held") is True
    assert memb[0].get("held_quantity") is None
    assert memb[0].get("held_membership_unverified") is True
    # The dict is the SOLE evidence of a position: membership_source is the
    # unverified-held record, not a verified fill.
    assert memb[0].get("membership_source") == "held_membership_unverified"
    assert memb[0].get("unavailable_reason")
    # Qualified neighbour keeps its real entry.
    assert "entry_plan" in _stages(out, "600001")


def test_c8_r4_position_facts_only_bad_market_digest_still_alerts(assets):
    """Adjacent negative: the same dict-only held member still reaches the
    hold_alert path when its own market digest is empty or malformed."""
    for digest in ("", "malformed-digest"):
        sec = _dict_only_held()
        sec["identity"]["source_evidence_digest"] = digest
        out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
        assert _held_action(out) == ["hold_alert"], digest
        assert _stages(out) == ["holding"]
        memb = _membership(out)
        assert memb and memb[0].get("held") is True


def test_c8_r4_dict_with_held_membership_false_is_contradiction(assets):
    """Declared-held dict + explicit held_membership=False refuses the day
    with the exact contradictory_account_membership reason; the detail
    locates the target security."""
    sec = _dict_only_held()
    sec["held_membership"] = False
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    receipt = out.get("receipt") or out
    block = receipt.get("day_block") or {}
    assert block.get("reason") == "contradictory_account_membership", receipt
    assert "600000" in str(block.get("detail") or block)


def test_c8_r4_dict_with_zero_quantity_is_contradiction(assets):
    """Declared-held dict + explicit held_quantity=0 refuses with the exact
    contradictory_account_membership reason - existence and a zero quantity
    cannot coexist."""
    sec = _dict_only_held()
    sec["held_quantity"] = 0
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    receipt = out.get("receipt") or out
    block = receipt.get("day_block") or {}
    assert block.get("reason") == "contradictory_account_membership", receipt
    assert "600000" in str(block.get("detail") or block)


def test_c8_r4_legitimate_empty_and_not_held_pass(assets):
    """Control: a security with no position_facts at all, or an explicit
    not-held marker (held_membership=False / held_quantity=0 with no dict),
    keeps its ordinary path - no held existence is invented."""
    # No position_facts at all -> a clean not-held member.
    out = e_run_e1_replay(
        _req([f._security("600000", _DAYS), f._security("600001", _DAYS)]), f.T1, assets
    )
    memb = _membership(out)
    assert memb and memb[0].get("held") is False and memb[0].get("membership_source") == "not_held"
    # Explicit not-held markers without a dict stay legal.
    sec = f._security("600000", _DAYS)
    sec["held_membership"] = False
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    memb = _membership(out)
    assert memb and memb[0].get("held") is False


def test_c8_r4_dict_plus_positive_quantity_keeps_verified_member(assets):
    """A confirmed fill (positive held_quantity) alongside an unqualified risk
    dict keeps the original membership source and quantity - the membership is
    verified by the fill evidence, only the risk ACTION degrades to hold_alert.
    Existence does not conjure a legal exit."""
    sec = _dict_only_held()
    sec["held_quantity"] = 1200
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    assert _held_action(out) == ["hold_alert"]
    memb = _membership(out)
    assert memb and memb[0].get("held") is True and memb[0].get("held_quantity") == 1200
    # Membership confirmed by the positive quantity: source stays the verified
    # fill evidence even though the risk dict is unverified.
    assert memb[0].get("membership_source") == "filled_position_evidence"
    assert memb[0].get("held_membership_unverified") is True


def test_c8_r4_unverifiable_non_dict_position_facts_alerts(assets):
    """An unverifiable non-dict position_facts (list/string/number/bool) is
    degraded to the declared-held alert - never a pass-through that crashes
    the closure or leaks into a fabricated disposition."""
    for bogus in ([], "bad", 12, True):
        sec = f._security("600000", _DAYS)
        sec.update(held_membership=True, held_quantity=1200, position_facts=bogus)
        out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
        assert _held_action(out) == ["hold_alert"], repr(bogus)
        assert _stages(out) == ["holding"]


def test_c8_r4_source_closure_digest_recomputes(assets):
    """The issued source_closure digest recomputes from the closure content -
    existence, fields, unverified reason and the held-membership_unverified
    flag are all bound into the identity."""
    from quanthus.event_library.contracts import sha256_digest

    out = e_run_e1_replay(_req([_dict_only_held(), f._security("600001", _DAYS)]), f.T1, assets)
    receipt = out.get("receipt") or {}
    assert receipt.get("source_closure_digest") == sha256_digest(out["source_closure"])


def test_c8_r4_typed_held_position_facts_keeps_verified_provenance(runner, resources):
    """An already-typed HeldPositionFacts is the SAME-READ verified snapshot:
    it produces the legal exit_plan AND keeps membership_source=
    filled_position_evidence with held_membership_unverified False - never
    mislabelled as an unverified record even though an exit results."""
    from decimal import Decimal

    from quanthus.expert_strategy.position_risk import HeldPositionFacts
    from quanthus.expert_strategy.runner import position_facts_evidence
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history,
    )

    sec = _security_entry(_raw_security("600000"))
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts=HeldPositionFacts(
            holding_sessions=40,
            marked_return=Decimal("0.05"),
            prior_strategy_reduction_count=0,
            evidence=position_facts_evidence(
                unit_id="acct-snap-600000",
                holding_sessions=40,
                marked_return=Decimal("0.05"),
                prior_strategy_reduction_count=0,
            ),
        ),
    )
    env = _envelope(
        [sec, _security_entry(_raw_security("600001"))],
        calendar=None,
        listing=[_listing("security-600000"), _listing("security-600001")],
    )
    out = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    held = [d for d in out.seam.held_decisions if d.security_code == "600000"]
    assert held and held[0].action == "exit"
    memb = [
        m for m in out.source_closure["account_membership"] if m.get("security_code") == "600000"
    ]
    assert memb
    assert memb[0].get("membership_source") == "filled_position_evidence"
    assert memb[0].get("held_membership_unverified") is False


def test_c8_held_type_structurally_similar_object_alerts(assets):
    """A structurally-similar object that carries the same four attribute
    names but is NOT a real HeldPositionFacts is NOT a verified snapshot -
    it stays on the named unverified-held alert path, never the trusted
    exit/filled_position_evidence path."""
    from dataclasses import dataclass
    from decimal import Decimal

    @dataclass
    class _AlmostFacts:
        holding_sessions: int = 40
        marked_return: object = Decimal("0.05")
        prior_strategy_reduction_count: int = 0
        evidence: object = None

    sec = f._security("600000", _DAYS)
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts=_AlmostFacts(),
    )
    out = e_run_e1_replay(_req([sec, f._security("600001", _DAYS)]), f.T1, assets)
    # Not the trusted type -> declared-held, risk unverified -> hold_alert.
    assert _held_action(out) == ["hold_alert"]
    memb = _membership(out)
    assert memb and memb[0].get("held") is True
    assert memb[0].get("membership_source") == "filled_position_evidence"
    assert memb[0].get("held_membership_unverified") is True
    assert not any(
        d.get("exit_kind")
        for ds in (out.get("package") or {}).get("decision_sets", [])
        for d in ds.get("decisions", [])
        if d.get("security_code") == "600000"
    )


def test_c8_held_type_real_type_keeps_verified_exit(runner, resources):
    """The actual HeldPositionFacts type is admitted to the verified path:
    legal exit_plan + filled_position_evidence, unverified False."""
    from decimal import Decimal

    from quanthus.expert_strategy.position_risk import HeldPositionFacts
    from quanthus.expert_strategy.runner import position_facts_evidence
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history,
    )

    sec = _security_entry(_raw_security("600000"))
    sec.update(
        held_membership=True,
        held_quantity=1200,
        position_facts=HeldPositionFacts(
            holding_sessions=40,
            marked_return=Decimal("0.05"),
            prior_strategy_reduction_count=0,
            evidence=position_facts_evidence(
                unit_id="acct-snap-600000",
                holding_sessions=40,
                marked_return=Decimal("0.05"),
                prior_strategy_reduction_count=0,
            ),
        ),
    )
    env = _envelope(
        [sec, _security_entry(_raw_security("600001"))],
        calendar=None,
        listing=[_listing("security-600000"), _listing("security-600001")],
    )
    out = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    held = [d for d in out.seam.held_decisions if d.security_code == "600000"]
    assert held and held[0].action == "exit"
    memb = [
        m for m in out.source_closure["account_membership"] if m.get("security_code") == "600000"
    ]
    assert memb
    assert memb[0].get("membership_source") == "filled_position_evidence"
    assert memb[0].get("held_membership_unverified") is False
