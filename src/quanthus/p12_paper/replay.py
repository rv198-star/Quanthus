"""Replay a completed session's quotes on one shared clock.

``Replay`` binds a saved intraday tape to a controllable clock so a replay
run serves the suggestion, the quotes and the simulated match from the same
point in time.  The tape is captured once by ``capture`` (real quotes +
minute series for one day) and frozen to JSON; the matcher then only ever
sees quotes already visible at the replay instant, never a live feed.

This is the quote entry point extracted from the R4 private replay driver
(``artifacts/p12/experience-20261120/preview/replay-preview.py``) so the
local server can wire suggestion + quotes + match to one replay clock.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace
from datetime import datetime
from threading import Lock
from typing import Any

from quanthus.p12_paper.quotes import IntradaySeries, QuoteService, SHANGHAI


class Replay:
    """Advance a frozen tape through a session, one completed minute at a time.

    ``tape`` maps security_code -> {"name", "previous_close", "points"}, the
    exact shape ``capture`` writes.  ``now()`` is the shared replay clock
    (also injected as the kernel ``clock``), ``fetch_raw`` feeds the admitted
    ``QuoteService`` fetcher, and ``fetch`` serves the intraday endpoint, so a
    resting limit order only ever fills on a quote already visible at the
    replay instant.
    """

    def __init__(
        self,
        tape: dict[str, Any],
        day: str,
        source: str,
        start_at: datetime | None = None,
    ) -> None:
        self._tape = tape
        self._day = day
        self._source = source
        raw_contract = tape.get("_contract")
        # A present but non-object contract root is itself a structural
        # violation (distinct from a missing contract); keep it for the
        # validator to reject by name rather than coercing or crashing.
        self._contract_raw = raw_contract
        self._contract: dict[str, Any] = (
            dict(raw_contract) if isinstance(raw_contract, dict) else {}
        )
        # Ruling 7153294321: the contract must be a LEGAL versioned source
        # contract before any of its declarations are honoured.  A wrong
        # version, an undefined time_label/visible_at, or a contradictory
        # label/visibility pair is an INVALID contract - the open is left
        # unsupplied so the confirm path refuses by name (contract error),
        # never silently falling back to inferring a price.
        self._contract_error: str | None = self._validate_contract()
        self._times = {
            code: [self._point_visible_at(row) for row in info["points"]]
            for code, info in tape.items()
            if code != "_contract"
        }
        close = datetime.fromisoformat(day + "T15:00:00+08:00")
        self._timeline = sorted({close, *(t for times in self._times.values() for t in times)})
        self._override: datetime | None = None
        self._lock = Lock()
        if start_at is None:
            self._index = 0
        elif start_at > self._timeline[-1]:
            self._index = len(self._timeline) - 1
            self._override = start_at
        else:
            self._index = self._timeline.index(start_at)

    def now(self) -> datetime:
        # An override carries the clock past the tape's last tick (e.g. into
        # the next session for the "nothing today / input not ready" day or a
        # fully-expired plan), while quotes still read only tape-visible data.
        if self._override is not None:
            return self._override
        return self._timeline[self._index]

    def clock(self, advance: bool) -> dict[str, Any]:
        with self._lock:
            if advance and self._index < len(self._timeline) - 1:
                self._index += 1
                self._override = None
            return {
                "time": self.now().isoformat(),
                "finished": self._index == len(self._timeline) - 1,
            }

    def set_now(self, instant: datetime) -> None:
        """Jump the replay clock to a timeline instant (or the latest <= it).

        An instant beyond the tape's close is held as an override: the shared
        clock keeps moving (so a published next-day package or an expired
        window reads correctly) while quotes keep serving the last tape tick.
        """
        with self._lock:
            if instant > self._timeline[-1]:
                self._override = instant
                return
            self._override = None
            idx = bisect_right(self._timeline, instant) - 1
            self._index = max(0, min(idx, len(self._timeline) - 1))

    def fetch_raw(self, code: str):
        now = self.now()
        info = self._tape[code]
        count = bisect_right(self._times[code], now)
        if not count:
            raise LookupError("本分钟前无可用历史行情")
        point = info["points"][count - 1]
        fields = [""] * 40
        fields[1:5] = [info["name"], code, point["price"], info["previous_close"]]
        # Field 5 is the bound session's NAMED OPEN - the opening price the
        # tape's versioned source contract explicitly declares, never a value
        # inferred from a minute label, the earliest visible tick or the
        # first-minute bar (Issue #723 E8 r5 fix-5, ruling 7153294321).  A
        # point row declares the open via the field the contract names as
        # ``open_field``; it must be on the same security and bound day AND
        # already visible at the replay clock.  No contract, no declared
        # field, or two conflicting declared opens all leave the field empty
        # so the order path refuses by name.
        fields[5] = self._named_open(code, info, count, now)
        fields[6] = point["volume_lots"]
        fields[30] = self._times[code][count - 1].strftime("%Y%m%d%H%M%S")
        return fields, now

    _CONTRACT_VERSION = "p12-replay-tape.v1"
    # Legal (time_label, visible_at) pairings: minute_start bars are visible
    # when their labelled minute ends; minute_end bars are visible at the
    # label itself.  Any other pairing is contradictory.
    _LABEL_VISIBLE = {"minute_start": "label_end", "minute_end": "label_time"}

    def _validate_contract(self) -> str | None:
        """Return a contract-error string, or None when the contract is legal.

        A tape with NO contract is legal-but-openless (the open is simply
        unsupplied).  A present contract must declare a supported version and
        a coherent label/visibility pair, else it is invalid.
        """
        # A missing contract (None) is legal-but-openless.  A present contract
        # must be an object declaring the supported version and a coherent
        # label/visibility pair; ``opens`` when present must be a list of
        # objects each carrying string security/time and a scalar price.
        if self._contract_raw is None:
            return None
        if not isinstance(self._contract_raw, dict):
            return f"non-object tape contract root {type(self._contract_raw).__name__}"
        version = self._contract.get("version")
        if version != self._CONTRACT_VERSION:
            return f"unsupported tape contract version {version!r}"
        time_label = self._contract.get("time_label")
        visible_at = self._contract.get("visible_at")
        if time_label not in self._LABEL_VISIBLE:
            return f"undefined tape time_label {time_label!r}"
        if self._LABEL_VISIBLE[time_label] != visible_at:
            return (
                f"contradictory tape label pair time_label={time_label!r} "
                f"visible_at={visible_at!r}"
            )
        opens = self._contract.get("opens")
        if opens is not None:
            if not isinstance(opens, list):
                return "tape contract opens is not an array"
            for decl in opens:
                if not isinstance(decl, dict):
                    return "tape contract opens entry is not an object"
                for key in ("security", "time", "price"):
                    if key not in decl:
                        return f"tape contract opens entry missing {key!r}"
                if not isinstance(decl["security"], str) or not isinstance(decl["time"], str):
                    return "tape contract opens entry field has the wrong type"
        return None

    def _point_visible_at(self, row: dict[str, Any]) -> datetime | None:
        """When a tape point becomes visible on the replay clock.

        ``time_label`` is the source contract's declared meaning of
        ``point.time``: ``minute_start`` (the Tencent m1 convention - the row
        covers the minute that OPENS at the label, visible when that minute
        ends, i.e. label + 1 minute) or ``minute_end`` (the row covers the
        minute that CLOSES at the label, visible at the label itself).
        """
        label = str(row.get("time", ""))
        if (
            self._contract_error is None
            and self._contract.get("time_label") == "minute_end"
        ):
            try:
                return datetime(
                    int(label[:4]), int(label[4:6]), int(label[6:8]),
                    int(label[8:10]), int(label[10:12]), tzinfo=SHANGHAI,
                )
            except (ValueError, IndexError):
                return None
        return IntradaySeries._minute_end(label)

    def _named_open(self, code: str, info: dict[str, Any], count: int, now: datetime) -> str:
        """The contract-declared bound-day open already visible at ``now``.

        Returns the declared open string, or "" when the contract names no
        open field, no visible point declares it, or the visible points
        declare conflicting opens (the confirm path then refuses by name).
        """
        self._open_errors: dict = getattr(self, "_open_errors", {})
        if self._contract_error is not None:
            self._open_errors[code] = "invalid_contract"
            return ""
        day_digits = now.strftime("%Y%m%d")
        # E8 r5 closeout (ruling): each declared open must be a FINITE,
        # POSITIVE decimal within the source-declared tick precision.  Raw
        # literals that fail the value domain are refused by name; Decimal-
        # equivalent literals merge to one value while every original
        # literal/label/source is preserved for the ledger.
        declared: list[str] = []
        seen_norm: set[str] = set()
        invalid_value = False
        min_tick = self._contract_min_tick()

        def _note(value) -> None:
            nonlocal invalid_value
            ok, norm = self._open_price_ok(value, min_tick)
            if not ok:
                invalid_value = True
                return
            if norm not in seen_norm:
                seen_norm.add(norm)
                declared.append(str(value).strip())

        # Declaration channel 1: a point row on the bound day declares the
        # session open via the contract's named ``open_field``.
        open_field = self._contract.get("open_field")
        if open_field:
            for row in info["points"][:count]:
                if not str(row.get("time", "")).startswith(day_digits):
                    continue
                # A row that does not carry the named field DECLARES NOTHING;
                # only a populated (but possibly illegal) literal is a
                # declared value that can poison the supply.
                if open_field in row:
                    _note(row.get(open_field))
        # Declaration channel 2: the source contract declares the open fact
        # directly as {security, time, price}; it becomes visible at the same
        # replay-visible moment as the label it names.
        opens = self._contract.get("opens")
        if isinstance(opens, list):
            for decl in opens:
                if not isinstance(decl, dict) or decl.get("security") != code:
                    continue
                label = str(decl.get("time", ""))
                if not label.startswith(day_digits):
                    continue
                visible = self._point_visible_at({"time": label})
                if visible is not None and visible <= now:
                    _note(decl.get("price"))
        # A declared open literal outside the value domain poisons the whole
        # supply for this confirm: it is refused BY NAME (PREOPEN_OPEN_PRICE_INVALID)
        # rather than silently merged away - the ledger must see the raw
        # violating literal, not a laundered pass.
        if invalid_value:
            self._open_errors[code] = "invalid_value"
        elif len(seen_norm) > 1:
            self._open_errors[code] = "ambiguous"
        elif not declared:
            # A tape with NO source contract at all is a different failure
            # from a legal contract that simply declares no opens - the
            # closeout matrix keeps the two reasons separate and named.
            self._open_errors[code] = (
                "missing_contract" if self._contract_raw is None else "unsupplied"
            )
        else:
            self._open_errors.pop(code, None)
        # The surfaced open is a single CLEAN value only; any invalid literal
        # or real conflict leaves the field empty so the confirm path refuses
        # by name against the recorded reason.
        if invalid_value or len(seen_norm) != 1:
            return ""
        return declared[0]

    def _contract_min_tick(self):
        """The source-declared minimum price increment, as a Decimal, or None."""
        from decimal import Decimal, InvalidOperation
        raw = self._contract.get("min_tick")
        if raw is None:
            return None
        try:
            tick = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None
        return tick if tick.is_finite() and tick > 0 else None

    @staticmethod
    def _open_price_ok(value, min_tick):
        """Validate a declared open literal: finite, positive Decimal within tick.

        Returns (ok, normalized).  ``normalized`` is the Decimal-normalized
        form used to merge Decimal-equivalent literals (49.80 == 49.8).
        """
        from decimal import Decimal, InvalidOperation
        text = str(value).strip() if value is not None else ""
        if not text:
            return False, None
        try:
            dec = Decimal(text)
        except (InvalidOperation, ValueError):
            return False, None
        if not dec.is_finite() or dec <= 0:
            return False, None
        if min_tick is not None:
            # Precision gate: the literal must not carry more fractional
            # digits than the source's declared minimum tick allows.
            if abs(dec.as_tuple().exponent) > abs(min_tick.as_tuple().exponent):
                return False, None
        return True, dec.normalize().to_eng_string()

    def fetch(self, code: str) -> dict[str, Any]:
        now = self.now()
        count = bisect_right(self._times.get(code, ()), now)
        points = self._tape[code]["points"][:count] if count else []
        return {
            "security_code": code,
            "trade_date": self._day,
            "source": self._source,
            "as_of": now.isoformat(),
            "status": "live" if points else "unavailable",
            "contract_version": "p12-paper-intraday-v1",
            "points": points,
        }

    def fetch_daily_kline(self, code: str) -> dict[str, Any]:
        """Safe offline fallback in replay mode: never reaches out to live feeds."""
        return {
            "security_code": code,
            "source": self._source,
            "basis": "qfq",
            "price_basis": "tencent_qfq_reference",
            "disclaimer": "历史回放模式未附带看盘参考日 K 线",
            "as_of": self.now().isoformat(),
            "status": "unavailable",
            "reason": "历史回放模式仅提供当日分时回放，未加载外网日 K 线",
            "bars": [],
            "contract_version": "p12-paper-daily-kline-v1",
        }


class ReplayQuotes(QuoteService):
    """A QuoteService over the replay fetcher; quotes carry the replay source."""

    def __init__(self, replay: Replay, source: str) -> None:
        super().__init__(fetcher=replay, now=replay.now)
        self._replay = replay
        self._source = source

    def quote(self, code: str):
        quote = super().quote(code)
        return replace(
            quote,
            source=self._source,
            reason=quote.reason if quote.last_price else "该股票未纳入本次历史回放",
        )

    def open_error(self, code: str):
        return getattr(self._replay, "_open_errors", {}).get(code)

    def contract_error(self):
        """The specific structural reason the source contract is illegal.

        Exposed so the public confirm receipt can carry an auditable
        named structural cause (non-object root / non-array opens /
        missing or mistyped field), not only the class-level code.
        """
        return getattr(self._replay, "_contract_error", None)
