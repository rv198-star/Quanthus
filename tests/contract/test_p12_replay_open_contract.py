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
