"""Contract tests for the E8 R5 fix-5 bound-session open (Issue #723, ruling
7153294321).  A buy confirm uses only the open the replay tape's versioned
source contract DECLARES by name; it never infers an open from a minute
label, the earliest visible tick or the first-minute bar."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from quanthus.p12_paper.quotes import IntradaySeries
from quanthus.p12_paper.replay import Replay

SH_TZ = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))


def _point(t, p, **kw):
    row = {"time": t, "price": p, "volume_lots": "5000"}
    row.update(kw)
    return row


def _instant(v):
    return datetime.fromisoformat(v + ":00+08:00")


MINUTE_START = {
    "version": "p12-replay-tape.v1",
    "time_label": "minute_start",
    "visible_at": "label_end",
    "open_field": "open",
}


def _tape(points, contract=MINUTE_START):
    tape = {
        "600000": {"name": "浦发银行", "previous_close": "50.00", "points": points}
    }
    if contract is not None:
        tape["_contract"] = contract
    return tape


def _open_at(replay, code, instant):
    replay.set_now(_instant(instant))
    fields, _ = replay.fetch_raw(code)
    return fields[5]


def test_named_open_declared_field_wins_over_labels():
    # The open is the row that DECLARES it via the contract's open_field -
    # regardless of its minute-label position.
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080928", "52.00"),          # pre-open imprint
            _point("202610080929", "49.80", open="49.80"),  # declared open
            _point("202610080931", "52.00"),          # later last price
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T09:31") == "49.80"
    # still 49.80 after the 09:32 tick becomes visible
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"


def test_only_preopen_imprint_supplies_no_open():
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_first_minute_bar_is_not_inferred_as_open():
    # A 09:30-label bar (the first-minute bar under minute_start) is NOT the
    # open unless the source declares it.
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080930", "52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_only_late_quote_supplies_no_open():
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080932", "52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_minute_end_contract_declares_its_own_label_semantics():
    contract = dict(MINUTE_START, time_label="minute_end", visible_at="label_time")
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080931", "49.80", open="49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=contract,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"


def test_declared_open_not_yet_visible_is_refused():
    # The open is declared only on a 15:00 row - not yet visible at 10:00.
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080932", "52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "49.80", open="49.80"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""
    # it IS visible once the 15:00 row completes on the clock
    assert _open_at(replay, "600000", "2026-10-08T15:01") == "49.80"


def test_conflicting_declared_opens_are_ambiguous():
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "49.80", open="49.80"),
            _point("202610080931", "52.00", open="52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_contract_level_open_declaration_is_visible_gated():
    # Source declares the open fact directly in the contract.opens list.
    contract = dict(MINUTE_START)
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"}
    ]
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080932", "52.00"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=contract,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"


def test_missing_contract_supplies_no_open():
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "49.80", open="49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=None,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_missing_contract_is_named_missing_not_unsupplied():
    # A tape with NO source contract carries a distinct named reason from
    # a legal contract that simply declares no opens (unsupplied).
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=None,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is None
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "missing_contract"


def test_wrong_version_contract_supplies_no_open():
    contract = dict(MINUTE_START, version="BOGUS")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"}
    ]
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=contract,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_contradictory_label_pair_is_invalid():
    contract = dict(MINUTE_START, time_label="minute_end", visible_at="label_end")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"}
    ]
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080931", "49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ],
        contract=contract,
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_open_field_alone_does_not_supply_open():
    # A contract that only names open_field but no row declares it supplies
    # no open - the field must actually be populated on a bound-day row.
    tape = _tape(
        [
            _point("202609301859", "49.80"),
            _point("202610080929", "49.80"),
            _point("202610080933", "49.85"),
            _point("202610081500", "51.50"),
        ]
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


# ---------------------------------------------------------------------------
# E8 R5 closeout matrix (合同收口补充单): named-open value domain, contract
# structure and Decimal equivalence.  Every illegal input is refused BY NAME
# via ReplayQuotes.open_error -> the confirm path's named reject; the ledger
# must keep cash, holdings, orders and fills untouched.
# ---------------------------------------------------------------------------


def _open_err(replay, code, instant):
    replay.set_now(_instant(instant))
    replay.fetch_raw(code)
    return replay._open_errors.get(code)


_POINTS_BASE = [
    _point("202609301859", "49.80"),
    _point("202610080932", "52.00"),
    _point("202610080933", "49.85"),
    _point("202610081500", "51.50"),
]


def _contract_opens(price, **extra):
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": price}
    ]
    contract.update(extra)
    return contract


@pytest.mark.parametrize(
    "bad",
    ["0", "-1.00", "NaN", "Infinity", "-Infinity", "", "abc", "  ", "49.8x", "1e2x"],
    ids=["zero", "negative", "nan", "inf", "neg_inf", "empty", "nonnum", "blank", "mixed", "exp_bad"],
)
def test_named_open_illegal_literals_are_refused_by_name(bad):
    tape = _tape(_POINTS_BASE, contract=_contract_opens(bad))
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_value"


def test_named_open_null_literal_is_refused():
    tape = _tape(_POINTS_BASE, contract=_contract_opens(None))
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_value"


def test_named_open_over_tick_precision_is_refused():
    # min_tick 0.01 allows at most 2 decimal places; 49.801 carries three.
    tape = _tape(_POINTS_BASE, contract=_contract_opens("49.801"))
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_value"


def test_named_open_at_tick_precision_is_accepted():
    tape = _tape(_POINTS_BASE, contract=_contract_opens("49.80"))
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"


def test_illegal_literal_poisons_a_valid_sibling():
    # One violating literal refuses the whole supply even beside a legal one;
    # it is never silently laundered into a pass.
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "0"},
        {"security": "600000", "time": "202610080930", "price": "49.80"},
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_value"
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_illegal_open_field_row_is_refused_by_name():
    tape = _tape(
        _POINTS_BASE + [_point("202610080929", "49.80", open="-1.00")],
        contract=dict(MINUTE_START, min_tick="0.01"),
    )
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_value"


# ---- contract structure ----------------------------------------------------


def test_non_object_contract_root_is_a_named_rejection():
    tape = _tape(_POINTS_BASE, contract="not-an-object")
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "non-object" in replay._contract_error
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_provided_null_contract_root_is_a_named_rejection():
    # An explicitly-provided ``_contract: null`` is a DECLARED wrong type -
    # a structural invalid contract (INVALID), never merged into the
    # missing-contract path (MISSING).  Missing means the key is absent.
    tape = _tape(_POINTS_BASE, contract=None)
    tape["_contract"] = None
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "non-object" in replay._contract_error
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_provided_non_dict_contract_roots_are_named_rejections():
    # Any provided non-dict root (list / scalar / bool) is structural INVALID.
    for raw in ([], 42, "x", True, 3.5):
        tape = _tape(_POINTS_BASE, contract=None)
        tape["_contract"] = raw
        replay = Replay(tape, day="2026-10-07", source="t")
        assert replay._contract_error is not None
        assert "non-object" in replay._contract_error
        assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_non_array_opens_is_a_named_rejection():
    contract = dict(MINUTE_START)
    contract["opens"] = {"security": "600000"}
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "not an array" in replay._contract_error


def test_non_object_opens_entry_is_a_named_rejection():
    contract = dict(MINUTE_START)
    contract["opens"] = ["600000@202610080929"]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "not an object" in replay._contract_error


@pytest.mark.parametrize("missing", ["security", "time", "price"])
def test_opens_entry_missing_required_field(missing):
    entry = {"security": "600000", "time": "202610080929", "price": "49.80"}
    del entry[missing]
    contract = dict(MINUTE_START)
    contract["opens"] = [entry]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "missing" in replay._contract_error


def test_opens_entry_wrong_field_type_is_a_named_rejection():
    contract = dict(MINUTE_START)
    contract["opens"] = [{"security": 600000, "time": "202610080929", "price": "49.80"}]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is not None
    assert "wrong type" in replay._contract_error


def test_legal_empty_opens_is_unsupplied_not_invalid():
    # Missing contract / legal empty supply / illegal structure are three
    # distinct audit categories.
    contract = dict(MINUTE_START)
    contract["opens"] = []
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert replay._contract_error is None
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "unsupplied"


def test_invalid_contract_carries_auditable_structural_detail():
    # Each illegal-structure case surfaces its specific reason via
    # contract_error() so the public receipt can record it.
    for raw in ("not a dict", [], 42):
        tape = _tape(_POINTS_BASE, contract=None)
        tape["_contract"] = raw
        replay = Replay(tape, day="2026-10-07", source="t")
        assert replay._contract_error is not None
        assert "non-object" in replay._contract_error
    # Non-array opens.
    contract = dict(MINUTE_START)
    contract["opens"] = {"x": 1}
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert "not an array" in replay._contract_error
    # Missing field.
    contract = dict(MINUTE_START)
    contract["opens"] = [{"security": "600000", "time": "202610080929"}]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert "missing" in replay._contract_error


def test_wrong_typed_scalar_fields_are_named_structural_rejections():
    # time_label / visible_at / open_field wrong types must NOT reach the
    # membership test (TypeError) - each is a named invalid_contract and
    # the service stays alive.
    for field, raw in (
        ("time_label", []),
        ("time_label", {}),
        ("visible_at", []),
        ("open_field", ["source_open"]),
        ("open_field", {"x": 1}),
    ):
        contract = dict(MINUTE_START, **{field: raw})
        tape = _tape(_POINTS_BASE, contract=contract)
        replay = Replay(tape, day="2026-10-07", source="t")
        assert replay._contract_error is not None
        assert field in replay._contract_error
        assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_provided_illegal_min_tick_is_named_contract_rejection():
    # A DECLARED malformed min_tick is an invalid contract - never silently
    # downgraded to undeclared so the precision gate drops.
    for raw in ("abc", "0", "NaN", "Infinity", None, {"x": 1}, [], True, -1):
        contract = dict(MINUTE_START)
        contract["min_tick"] = raw
        contract["opens"] = [
            {"security": "600000", "time": "202610080929", "price": "49.80"}
        ]
        tape = _tape(_POINTS_BASE, contract=contract)
        replay = Replay(tape, day="2026-10-07", source="t")
        assert replay._contract_error is not None
        assert "min_tick" in replay._contract_error
        assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_bad_tick_does_not_drop_precision_gate():
    # A malformed declared tick + an over-precision open must NOT fill -
    # it is an invalid contract, not an undeclared pass.
    contract = dict(MINUTE_START)
    contract["min_tick"] = "abc"
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.801"}
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert "min_tick" in replay._contract_error
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


# ---- field presence vs provided-null, and entry field paths ---------------


def test_provided_null_opens_is_structural_not_unavailable():
    # opens=null is a DECLARED wrong type - structural INVALID, never a
    # legal empty supply or a merely-missing one.
    contract = dict(MINUTE_START)
    contract["opens"] = None
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert "opens is not an array" in replay._contract_error
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_provided_null_open_field_is_structural():
    contract = dict(MINUTE_START)
    contract["open_field"] = None
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"}
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert "open_field" in replay._contract_error
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


def test_opens_entry_field_type_names_the_field_path():
    # security/time/price wrong types name opens[i].<field> in the detail.
    for field, raw in (
        ("security", ["x"]), ("security", {"x": 1}), ("security", True),
        ("time", []), ("price", ["49.80"]), ("price", {"x": 1}), ("price", True),
    ):
        contract = dict(MINUTE_START)
        contract["opens"] = [
            {"security": "600000", "time": "202610080929", "price": "49.80",
             field: raw}
        ]
        tape = _tape(_POINTS_BASE, contract=contract)
        replay = Replay(tape, day="2026-10-07", source="t")
        assert replay._contract_error is not None
        assert f"opens[0].{field}" in replay._contract_error
        assert _open_err(replay, "600000", "2026-10-08T10:00") == "invalid_contract"


# ---- Decimal equivalence vs real conflict ----------------------------------


def test_decimal_equivalent_literals_merge_to_one_open():
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"},
        {"security": "600000", "time": "202610080930", "price": "49.8"},
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    # Both literals are visible; Decimal-equivalent so one value surfaces.
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"
    assert replay._open_errors.get("600000") is None


def test_duplicate_identical_declaration_confirms_once():
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"},
        {"security": "600000", "time": "202610080930", "price": "49.80"},
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"


def test_real_conflict_remains_ambiguous():
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"},
        {"security": "600000", "time": "202610080930", "price": "52.00"},
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_err(replay, "600000", "2026-10-08T10:00") == "ambiguous"
    assert _open_at(replay, "600000", "2026-10-08T10:00") == ""


def test_conflict_entry_not_yet_visible_stays_clock_gated():
    # A second declaration whose label is not yet replay-visible does not
    # yet create ambiguity; the first visible value supplies the open.
    contract = dict(MINUTE_START, min_tick="0.01")
    contract["opens"] = [
        {"security": "600000", "time": "202610080929", "price": "49.80"},
        {"security": "600000", "time": "202610081500", "price": "52.00"},
    ]
    tape = _tape(_POINTS_BASE, contract=contract)
    replay = Replay(tape, day="2026-10-07", source="t")
    assert _open_at(replay, "600000", "2026-10-08T10:00") == "49.80"
