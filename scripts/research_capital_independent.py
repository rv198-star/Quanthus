"""Independent cold pandas features and Decimal cash referee; no producer/P2 imports."""

from __future__ import annotations

import collections
import json
import re
import subprocess
import time
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from research_r1_v2_independent_audit import reproduce

from quanthus.hf_dwd_research import digest, file_digest, read, verify_members, write

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Shanghai")
D = Decimal


def admit_author(q, author_job):
    """A result file is adoptable only under its successful formal Guardian."""
    guardian = read(author_job / "guardian.json")
    assert guardian["mode"] == "formal"
    assert type(guardian["exit_code"]) is int and guardian["exit_code"] == 0
    assert guardian["failure"] is None
    author = read(author_job / "result.json")
    assert guardian["identity"] == author["identity"]
    assert author["identity"]["request_digest"] == digest(q)
    assert author["digest"] == digest({k: v for k, v in author.items() if k != "digest"})
    if q["schema"].endswith(".v2") or "output_digest" in guardian:
        assert guardian["output_digest"] == author["digest"]
    return author


def reference_purpose(q, root):
    """Independent package/unit interpretation; no formal effective tables or loader."""
    descriptor = q["purpose"]
    names = set()
    for entry in descriptor["members"]:
        name = entry["path"]
        assert name == Path(name).name and name not in names
        names.add(name)
        path = root / name
        assert not path.is_symlink() and path.is_file()
        assert path.stat().st_size == entry["bytes"] and file_digest(path) == entry["sha256"]
    assert {p.name for p in root.iterdir()} == names
    assert sum(x["bytes"] for x in descriptor["members"]) <= 4194304
    assert file_digest(root / descriptor["purpose_path"]) == descriptor["purpose_sha256"]
    m = read(root / descriptor["purpose_path"])
    assert m["schema"] == "quanthus.p10-capital-purpose.v1"
    assert (
        m["status"] == "qualified_scoped_retrospective" and m["native_authority"] == q["authority"]
    )
    assert m["review_roles"]["review_status"] == "pass_after_package_completion"
    assert digest(m["rule"]) == m["rule_digest"]
    assert all(entry in descriptor["members"] for entry in m["members"])
    assert set(m["documents"].values()) == {entry["path"] for entry in m["members"]}
    docs = {key: read(root / path) for key, path in m["documents"].items()}
    assert docs["source-receipts"]["source_hash_probe"] == m["sources"]
    scope, event = m["scope"], m["effective_event"]
    assert scope["use"] == "guarded_retrospective_capital_only"
    assert scope["uncovered_policy"] == "original_native_guard"
    assert date(2020, 1, 2) <= date.fromisoformat(scope["ex_date"]) <= date(2021, 12, 31)
    assert event["ex_date"] == scope["ex_date"]
    times = [
        datetime.fromisoformat(m[k]) for k in ("observed_at", "knowledge_from", "qualified_at")
    ]
    assert all(t.tzinfo is not None for t in times)
    assert times[0] <= times[1] == times[2] <= datetime.fromisoformat(q["source_knowledge_cutoff"])
    assert m["knowledge_to"] is None and m["qualified_at"] == descriptor["qualified_at"]
    em = docs["em-parser-proof"]["raw"]
    f10 = docs["f10-parser-proof"]["matched_record"]
    plan = em["IMPL_PLAN_PROFILE"]
    assert plan.startswith("10派") and "元(含税,扣税后" in plan
    amount = D(plan.split("派", 1)[1].split("元", 1)[0]) / D(10)
    f10_tokens = re.findall(r"10派([0-9.]+)元\(含税\)", docs["f10-exact-block"]["raw_block"])
    assert len(f10_tokens) == 1
    assert amount == D(f10_tokens[0]) / 10 == D(em["PRETAX_BONUS_RMB"]) / 10
    assert amount == D(f10["cash_per_share"]) == D(event["cash_per_share"])
    assert event["currency"] == "CNY" and event["action_type"] == "CASH_DIVIDEND"
    assert event["bonus_shares"] is None and event["transfer_shares"] is None
    assert em["SECURITY_CODE"] == f10["stock_code"] == scope["stock_code"]
    assert em["EX_DIVIDEND_DATE"][:10] == f10["ex_date"] == scope["ex_date"]
    assert em["EQUITY_RECORD_DATE"][:10] == f10["record_date"] == event["record_date"]
    assert em["REPORT_DATE"][:10] == event["report_date"]
    assert em["NOTICE_DATE"][:10] == event["implementation_notice_date"]
    assert f10["payment_date"] == event["payment_date"]
    h = docs["history-identity-proof"]
    assert h["exact_matches"] == 1 and h["raw_row"]["progress"] == "实施"
    assert h["raw_row"]["stock_code"] == scope["stock_code"]
    assert h["raw_row"]["ex_dividend_date"][:10] == scope["ex_date"]
    assert h["raw_row"]["announce_date"][:10] == event["implementation_notice_date"]
    assert docs["independent-review"]["verdict"] == "pass"
    return m, docs, amount


def cash(v):
    return v.quantize(D(".01"), rounding=ROUND_HALF_UP)


def query(db, sql, params=()):
    result = db.execute(sql, params)
    return [
        dict(zip([c[0] for c in result.description], r, strict=True)) for r in result.fetchall()
    ]


class ReferenceHold(Exception):
    def __init__(self, reason, day, listing, ids=()):
        self.detail = {
            "reason": reason,
            "date": str(day),
            "listing_id": listing,
            "action_ids": list(ids),
            "owner": "P10 #556",
        }


class IndependentFacts:
    def __init__(self, source, members, choices, q):
        import duckdb

        purpose = reference_purpose(q, source.parent.parent / "purpose") if "purpose" in q else None
        self.purpose_events = {}
        self.purpose_application = None
        self.db = duckdb.connect()
        self.db.execute("SET TimeZone='Asia/Shanghai'")
        self.db.execute("SET threads=2")
        self.db.execute("SET memory_limit='2GB'")
        scratch = source.parent.parent / "reference-scratch"
        scratch.mkdir(exist_ok=True)
        self.db.execute("SET temp_directory=?", [str(scratch)])
        self.cutoff = datetime.fromisoformat(q["source_knowledge_cutoff"])
        for name in [
            "dwd_listing",
            "dwd_security",
            "dwd_security_bar",
            "dwd_tradability_state",
            "dwd_trading_calendar",
            "dwd_corporate_action",
            "dwd_corporate_action_term",
            "dwd_quality_exception",
        ]:
            paths = [
                str(source / x["path"])
                for x in members
                if x["path"].startswith("payload/" + name + "/")
            ]
            self.db.read_parquet(paths, hive_partitioning=True).create_view("raw")
            self.db.execute(f"CREATE VIEW {name} AS SELECT * FROM raw WHERE knowledge_to IS NULL")
            # Each view must bind its relation now, not resolve a subsequently replaced alias.
            self.db.execute(f"CREATE TEMP TABLE keep_{name} AS SELECT * FROM {name}")
            self.db.execute(f"DROP VIEW {name}")
            self.db.execute(f"CREATE VIEW {name} AS SELECT * FROM keep_{name}")
            k = self.db.execute(f"SELECT max(knowledge_from) FROM {name}").fetchone()[0]
            assert k is not None and k <= datetime.fromisoformat(
                q.get("native_knowledge_cutoff", q["source_knowledge_cutoff"])
            )
        self.db.execute("CREATE TABLE selected_listings(listing_id VARCHAR PRIMARY KEY)")
        self.db.executemany(
            "INSERT INTO selected_listings VALUES(?)",
            [[s] for s in sorted({r["listing_id"] for r in choices})],
        )
        self.db.execute("""CREATE TEMP TABLE execution AS
          SELECT l.listing_id,l.security_id,l.venue_id,l.listing_code,l.listing_currency,l.lot_size,
            CAST(c.trade_date AS DATE) AS day,c.open_at,c.close_at,b.open,b.close,
            b.knowledge_from AS bar_known,t.knowledge_from AS state_known,
            l.knowledge_from AS listing_known,s.knowledge_from AS security_known,
            t.is_tradable,t.can_buy,t.can_sell,t.reason_code,t.upper_price,t.lower_price
          FROM dwd_listing l JOIN selected_listings z USING(listing_id)
          JOIN dwd_trading_calendar c USING(venue_id)
          JOIN dwd_security s USING(security_id)
          LEFT JOIN dwd_security_bar b ON b.listing_id=l.listing_id
            AND b.calendar_session_id=c.calendar_session_id
            AND b.interval_size='1d' AND b.price_basis='UNADJUSTED'
          LEFT JOIN dwd_tradability_state t ON t.listing_id=l.listing_id
            AND t.calendar_session_id=c.calendar_session_id
          WHERE c.is_open AND c.trade_date BETWEEN DATE '2020-01-02' AND DATE '2021-12-31'
            AND CAST(c.trade_date AS DATE)>=CAST(l.effective_from AS DATE)
            AND (l.effective_to IS NULL OR CAST(c.trade_date AS DATE)<CAST(l.effective_to AS DATE))
            AND CAST(c.trade_date AS DATE)>=CAST(s.effective_from AS DATE)
            AND (s.effective_to IS NULL OR CAST(c.trade_date AS DATE)<CAST(s.effective_to AS DATE));
          CREATE INDEX execution_key ON execution(listing_id,day)
        """)
        self.cache = {}
        self.terms = collections.defaultdict(list)
        for t in query(
            self.db, "SELECT * FROM dwd_corporate_action_term ORDER BY corporate_action_term_id"
        ):
            self.terms[t["corporate_action_id"]].append(t)
        self.actions = collections.defaultdict(list)
        self.unknown_action_dates = collections.defaultdict(list)
        for a in query(
            self.db,
            "SELECT * FROM dwd_corporate_action WHERE action_status='IMPLEMENTED' "
            "ORDER BY corporate_action_id",
        ):
            day = a["ex_date"] or a["effective_at"].astimezone(ZONE).date()
            if date(2020, 1, 2) <= day <= date(2021, 12, 31):
                self.actions[a["security_id"], day].append(a)
                if a["ex_date"] is None:
                    self.unknown_action_dates[a["security_id"]].append(a["corporate_action_id"])
        if purpose is not None:
            m, docs, amount = purpose
            scope, event = m["scope"], m["effective_event"]
            key = scope["security_id"], date.fromisoformat(scope["ex_date"])
            group = self.actions.get(key, [])
            expected = docs["source-records"]
            headers = [a for a in expected["dwd_corporate_action"] if a["knowledge_to"] is None]
            atoms = [t for t in expected["dwd_corporate_action_term"] if t["knowledge_to"] is None]

            def plain(row):
                return {
                    k: str(v) if isinstance(v, (date, datetime, D)) else v for k, v in row.items()
                }

            assert sorted(
                (plain(a) for a in group), key=lambda a: a["corporate_action_id"]
            ) == sorted(headers, key=lambda a: a["corporate_action_id"])
            actual_terms = [t for a in group for t in self.terms[a["corporate_action_id"]]]
            assert sorted(
                (plain(t) for t in actual_terms), key=lambda t: t["corporate_action_term_id"]
            ) == sorted(atoms, key=lambda t: t["corporate_action_term_id"])
            assert (
                sorted(a["corporate_action_id"] for a in group) == scope["covered_latest_actions"]
            )
            assert (
                sorted(t["corporate_action_term_id"] for t in actual_terms)
                == scope["covered_latest_terms"]
            )
            assert {r["action_id"] for r in m["source_relations"]} == set(
                scope["covered_latest_actions"]
            )
            assert {r["term_id"] for r in m["term_relations"]} == set(scope["covered_latest_terms"])
            assert len(m["source_relations"]) == len(group) and len(m["term_relations"]) == len(
                actual_terms
            )
            by_term = {t["corporate_action_term_id"]: t for t in atoms}
            for relation in m["term_relations"]:
                t = by_term[relation["term_id"]]
                assert (
                    relation["action_id"],
                    relation["source_record_id"],
                    relation["effective_event_id"],
                ) == (t["corporate_action_id"], t["source_record_id"], event["economic_event_id"])
                assert (
                    relation["term_type"],
                    relation["original_amount_per_share"],
                    relation["original_ratio_numerator"],
                    relation["original_ratio_denominator"],
                ) == (
                    t["term_type"],
                    t["amount_per_share"],
                    t["ratio_numerator"],
                    t["ratio_denominator"],
                )
            assert all(
                a["source_artifact_sha256"]
                in {"sha256:" + s["sha256"].removeprefix("sha256:") for s in m["sources"]}
                for a in group
            )
            listing = self.db.execute(
                "SELECT security_id,listing_code FROM dwd_listing WHERE listing_id=?",
                [scope["listing_id"]],
            ).fetchall()
            assert listing == [(scope["security_id"], scope["stock_code"])]
            assert any(
                a["source_record_id"]
                == docs["f10-parser-proof"]["matched_record"]["source_row_key"]
                for a in group
            )
            assert any(
                a["corporate_action_id"]
                == docs["history-identity-proof"]["reconstructed_action_id"]
                and a["source_record_id"]
                == docs["history-identity-proof"]["reconstructed_source_record_id"]
                for a in group
            )
            self.purpose_events[key] = amount
            self.purpose_application = {
                "economic_event_id": event["economic_event_id"],
                "source_action_ids": scope["covered_latest_actions"],
                "source_term_ids": scope["covered_latest_terms"],
                "purpose_sha256": q["purpose"]["purpose_sha256"],
                "purpose_known_at": str(datetime.fromisoformat(m["knowledge_from"])),
                "source_relations": m["source_relations"],
                "cash_per_share": str(amount),
                "scope": scope,
                "native_rows_preserved": True,
            }
        self.exceptions = collections.defaultdict(list)
        for e in query(self.db, "SELECT * FROM dwd_quality_exception"):
            if e["affected_table"] not in ("dwd_corporate_action", "dwd_corporate_action_term"):
                continue
            if e["resolution_state"] in ("RESOLVED", "CLOSED"):
                continue
            key = json.loads(e["affected_business_key_json"])
            if key.get("stock_code"):
                self.exceptions[
                    key["stock_code"], e["affected_effective_at"].astimezone(ZONE).date()
                ].append(e["quality_exception_id"])
        venues = read(ROOT / q["r2_request"]["path"])["analysis"]["required_venues"]
        assert len(venues) == 2
        calendars = query(
            self.db,
            "SELECT venue_id,list(CAST(trade_date AS DATE) ORDER BY trade_date) "
            "AS calendar_days FROM dwd_trading_calendar WHERE is_open "
            "AND venue_id IN (?,?) AND trade_date BETWEEN DATE '2020-03-02' "
            "AND DATE '2021-12-31' GROUP BY venue_id",
            venues,
        )
        assert (
            len(calendars) == 2 and calendars[0]["calendar_days"] == calendars[1]["calendar_days"]
        )
        self.days = calendars[0]["calendar_days"]

    def row(self, listing, day):
        key = listing, day
        if key not in self.cache:
            found = query(
                self.db, "SELECT * FROM execution WHERE listing_id=? AND day=?", [listing, day]
            )
            assert len(found) <= 1
            self.cache[key] = found[0] if found else None
        return self.cache[key]

    def availability(self, listing, day):
        r = self.row(listing, day)
        if r is None:
            return "missing", None, None, False, False
        if r["listing_currency"] != "CNY" or r["lot_size"] != 100:
            raise ReferenceHold("CURRENCY_OR_LOT_UNQUALIFIED", day, listing)
        if any(
            r[k] is None for k in ("bar_known", "state_known", "listing_known", "security_known")
        ):
            raise ReferenceHold("SOURCE_KNOWLEDGE_UNKNOWN", day, listing)
        directions = [r["can_buy"], r["can_sell"]]
        if not all(type(v) is bool for v in directions):
            directions = [None, None]
        if (
            r["reason_code"] == "SUSPENDED"
            and [r[k] for k in ("is_tradable", "can_buy", "can_sell")] == [False] * 3
        ):
            return "suspended", None, None, False, False
        good = (
            all(type(r[k]) is bool for k in ("is_tradable", "can_buy", "can_sell"))
            and r["is_tradable"]
            and r["reason_code"] == "TRADABLE_OBSERVED"
        )
        p = [r[k] for k in ("open", "close", "upper_price", "lower_price")]
        good = good and all(isinstance(v, D) and v.is_finite() and v > 0 for v in p)
        if not good:
            return "missing", None, None, *directions
        op, cl, up, lo = p
        if not lo < up or not lo <= op <= up:
            return "missing", None, None, *directions
        kind = "limit_up" if op == up else "limit_down" if op == lo else "ordinary"
        return kind, op, cl, r["can_buy"], r["can_sell"]

    def action(self, listing, day):
        r = self.row(listing, day)
        if r is None:
            raise ReferenceHold("HELD_IDENTITY_UNKNOWN", day, listing)
        missing = self.unknown_action_dates.get(r["security_id"], [])
        if missing:
            raise ReferenceHold("ACTION_BUSINESS_DATE_UNKNOWN", day, listing, missing)
        errors = self.exceptions.get((r["listing_code"], day), [])
        if errors:
            raise ReferenceHold("UNRESOLVED_SOURCE_ACTION_EXCEPTION", day, listing, errors)
        actions = self.actions.get((r["security_id"], day), [])
        if (r["security_id"], day) in self.purpose_events:
            if self.availability(listing, day)[1] is None:
                raise ReferenceHold("ACTION_OBSERVED_OPEN_MARK_UNAVAILABLE", day, listing)
            return self.purpose_events[r["security_id"], day], D(0)
        ids = [a["corporate_action_id"] for a in actions]
        if len(actions) > 1:
            raise ReferenceHold("ACTION_EVENT_AMBIGUOUS", day, listing, ids)
        if not actions:
            return D(0), D(0)
        a = actions[0]
        reason = None
        if a["ex_date"] is None:
            reason = "ACTION_BUSINESS_DATE_UNKNOWN"
        elif a["effective_at"].astimezone(ZONE).date() != a["ex_date"]:
            reason = "ACTION_EFFECTIVE_EX_DATE_MISMATCH"
        if reason:
            raise ReferenceHold(reason, day, listing, ids)
        terms = self.terms[a["corporate_action_id"]]
        values = {}
        for t in terms:
            k = t["term_type"]
            if k not in (
                "CASH_AMOUNT_PER_SHARE",
                "BONUS_SHARES_PER_SHARE",
                "TRANSFER_SHARES_PER_SHARE",
            ):
                raise ReferenceHold("ACTION_TERM", day, listing, ids)
            if k in values or t["option_id"] is not None:
                raise ReferenceHold("ACTION_TERM_AMBIGUOUS", day, listing, ids)
            if t["effective_at"] != a["effective_at"]:
                raise ReferenceHold("ACTION_TERM_DATE_MISMATCH", day, listing, ids)
            if k == "CASH_AMOUNT_PER_SHARE":
                if t["currency"] != "CNY":
                    raise ReferenceHold("ACTION_CURRENCY", day, listing, ids)
                v = t["amount_per_share"]
            else:
                n, den = t["ratio_numerator"], t["ratio_denominator"]
                if (
                    not isinstance(n, D)
                    or not isinstance(den, D)
                    or not n.is_finite()
                    or n < 0
                    or not den.is_finite()
                    or den <= 0
                ):
                    raise ReferenceHold("ACTION_RATIO", day, listing, ids)
                v = n / den
            if not isinstance(v, D) or not v.is_finite() or v < 0:
                raise ReferenceHold("ACTION_VALUE", day, listing, ids)
            values[k] = v
        c = values.get("CASH_AMOUNT_PER_SHARE", D(0))
        s = values.get("BONUS_SHARES_PER_SHARE", D(0)) + values.get(
            "TRANSFER_SHARES_PER_SHARE", D(0)
        )
        if (
            not terms
            or (a["action_type"] in ("CASH_DIVIDEND", "MIXED_DISTRIBUTION") and not c)
            or (a["action_type"] in ("CAPITAL_RESERVE_TRANSFER", "MIXED_DISTRIBUTION") and not s)
        ):
            raise ReferenceHold("ACTION_TERM_MISSING", day, listing, ids)
        if self.availability(listing, day)[1] is None:
            raise ReferenceHold("ACTION_OBSERVED_OPEN_MARK_UNAVAILABLE", day, listing, ids)
        return c, s


def referee(facts, nominal, arm, policy):
    choices = {(r["arm"], r["date"]): r for r in nominal}
    balance, pool, qty, held, last_mark = D("100000.00"), D("0.00"), 0, None, None
    attempts = []
    receipts = []
    equity = {str(facts.days[0]): D("100000.00")}
    trades = []
    deployed = dividends = D(0)
    entry_day = None
    i = 0
    days = facts.days
    try:
        while i < len(days) - 1:
            t, nxt = days[i], days[i + 1]
            if not qty:
                choice = choices.get((arm, str(t)))
                if choice is None:
                    equity[str(nxt)] = cash(balance + pool)
                    i += 1
                    continue
                attempt = {
                    "date": str(t),
                    "listing_id": choice["listing_id"],
                    "ranking_vol12": choice["ranking_vol12"],
                }
                attempts.append(attempt)
                if choice["decision_hold"]:
                    attempt["decision"] = "blocked_at_T"
                    equity[str(nxt)] = cash(balance + pool)
                    i += 1
                    continue
                amount = (
                    int(balance * D(".30") / (D(choice["decision_close"]) * D("1.21")) / 100) * 100
                )
                attempt["quantity"] = amount
                if amount <= 0:
                    attempt["decision"] = "below_one_lot"
                    equity[str(nxt)] = cash(balance + pool)
                    i += 1
                    continue
                attempt["decision"] = "original_nominal_order"
                listing = choice["listing_id"]
                kind, op, cl, can_buy, _ = facts.availability(listing, nxt)
                fills = []
                if kind not in ("missing", "suspended", "limit_up") and can_buy:
                    cost = policy["costs"]["buy"]
                    price = cash(op * (1 + D(cost["slippage_bps"]) / 10000))
                    gross = cash(price * amount)
                    commission = cash(
                        max(gross * D(cost["commission_rate"]), D(cost["minimum_commission"]))
                    )
                    if gross + commission <= balance:
                        balance = cash(balance - gross - commission)
                        deployed = gross + commission
                        dividends = D(0)
                        entry_day = str(nxt)
                        qty = amount
                        held = listing
                        last_mark = cl
                        fills = [
                            {
                                "filled_at": facts.row(listing, nxt)["open_at"].isoformat(),
                                "side": "buy",
                                "price": str(price),
                                "quantity": amount,
                                "gross_amount": str(gross),
                                "commission": str(commission),
                                "stamp_duty": "0.00",
                                "transfer_fee": "0.00",
                                "total_fees": str(commission),
                            }
                        ]
                wealth = cash(balance + pool + (last_mark * qty if qty else 0))
                equity[str(nxt)] = wealth
                receipts.append(
                    {
                        "side": "buy",
                        "decision_date": str(t),
                        "listing_id": listing,
                        "quantity": amount,
                        "fills": fills,
                        "ending_cash": str(balance),
                        "external_pool": str(pool),
                        "position_quantity": qty,
                        "system_wealth": str(wealth),
                        "order_count": 1,
                        "carried": 0,
                    }
                )
                i += 1
                continue
            # Preflight the complete carried order before valuing its partial outcome.
            end = i
            sequence = []
            while end < len(days) - 1:
                day = days[end + 1]
                actions = facts.action(held, day)
                state = facts.availability(held, day)
                sequence.append((day, actions, state))
                kind, op, cl, _, can_sell = state
                if kind not in ("missing", "suspended", "limit_down") and can_sell:
                    break
                end += 1
            order_qty = qty
            carried = 0
            fills = []
            for day, (c, s), (kind, op, cl, _, can_sell) in sequence:
                if c or s:
                    paid = cash(c * qty)
                    balance = cash(balance + paid)
                    dividends += paid
                    new = D(qty) * (1 + s)
                    if new != new.to_integral_value():
                        affected = facts.actions.get((facts.row(held, day)["security_id"], day), [])
                        hold = ReferenceHold(
                            "corporate action creates a fractional share outside the first P2 rule",
                            day,
                            held,
                            [a["corporate_action_id"] for a in affected],
                        )
                        hold.detail["owner"] = "P2 consumer"
                        raise hold
                    qty = int(new)
                if kind in ("missing", "suspended", "limit_down") or not can_sell:
                    carried += 1
                    if cl is not None:
                        last_mark = cl
                    equity[str(day)] = cash(balance + pool + last_mark * qty)
                    continue
                cost = policy["costs"]["sell"]
                price = cash(op * (1 - D(cost["slippage_bps"]) / 10000))
                gross = cash(price * qty)
                commission = cash(
                    max(gross * D(cost["commission_rate"]), D(cost["minimum_commission"]))
                )
                stamp = cash(gross * D(cost["stamp_duty_rate"]))
                transfer = cash(gross * D(cost["transfer_fee_rate"]))
                fees = cash(commission + stamp + transfer)
                balance = cash(balance + gross - fees)
                trades.append(
                    {
                        "entry_date": entry_day,
                        "exit_date": str(day),
                        "pnl": str(cash(gross - fees + dividends - deployed)),
                    }
                )
                gap = cash(balance - D("100000.00"))
                sweep = max(gap, D(0))
                replenish = min(max(-gap, D(0)), pool)
                balance = cash(balance - sweep + replenish)
                pool = cash(pool + sweep - replenish)
                fills = [
                    {
                        "filled_at": facts.row(held, day)["open_at"].isoformat(),
                        "side": "sell",
                        "price": str(price),
                        "quantity": qty,
                        "gross_amount": str(gross),
                        "commission": str(commission),
                        "stamp_duty": str(stamp),
                        "transfer_fee": str(transfer),
                        "total_fees": str(fees),
                    }
                ]
                qty = 0
                last_mark = None
                equity[str(day)] = cash(balance + pool)
            wealth = cash(balance + pool + (last_mark * qty if qty else 0))
            receipts.append(
                {
                    "side": "sell",
                    "decision_date": str(t),
                    "listing_id": held,
                    "quantity": order_qty,
                    "fills": fills,
                    "ending_cash": str(balance),
                    "external_pool": str(pool),
                    "position_quantity": qty,
                    "system_wealth": str(wealth),
                    "order_count": 1,
                    "carried": carried,
                }
            )
            i = min(end + 1, len(days) - 1)
        if qty and facts.availability(held, days[-1])[2] is None:
            raise ReferenceHold("TERMINAL_MARK_UNQUALIFIED", days[-1], held)
        return {
            "status": "completed",
            "nominal_attempts": attempts,
            "p2_step_receipts": receipts,
            "ending_wealth": str(cash(balance + pool + (last_mark * qty if qty else 0))),
            "equity_curve": {k: str(v) for k, v in equity.items()},
            "trades": trades,
        }
    except ReferenceHold as exc:
        return {
            "status": "blocked",
            "hold": exc.detail,
            "nominal_attempts": attempts,
            "p2_step_receipts": receipts,
        }


def independent_decision(arms):
    if any(a["status"] != "completed" for a in arms.values()):
        return {"branch": "blocked", "verdict": "not_evaluated", "primary_increment": None}
    n = D("100000.00")
    metrics = {}
    for name, a in arms.items():
        curve = {k: D(v) for k, v in a["equity_curve"].items()}
        peak = n
        dd = D(0)
        for day in sorted(curve):
            peak = max(peak, curve[day])
            dd = max(dd, (peak - curve[day]) / peak)
        y = {}
        prior = n
        for year in ("2020", "2021"):
            ending = curve[max(d for d in curve if d.startswith(year))]
            y[year] = {
                "return": str((ending - prior) / n),
                "closed_trades": sum(t["exit_date"].startswith(year) for t in a["trades"]),
            }
            prior = ending
        metrics[name] = {
            "return": (D(a["ending_wealth"]) - n) / n,
            "dd": dd.quantize(D(".00000001"), rounding=ROUND_HALF_UP),
            "years": y,
        }
    delta = metrics["candidate"]["return"] - metrics["control"]["return"]
    yearly = {
        y: D(metrics["candidate"]["years"][y]["return"])
        - D(metrics["control"]["years"][y]["return"])
        for y in ("2020", "2021")
    }
    enough = all(len(a["trades"]) >= 100 for a in arms.values()) and all(
        y["closed_trades"] >= 20 for m in metrics.values() for y in m["years"].values()
    )
    profit = sum(
        sorted([max(D(t["pnl"]), D(0)) for t in arms["candidate"]["trades"]], reverse=True)[:3],
        D(0),
    )
    robust = delta - profit / n
    advance = (
        metrics["candidate"]["return"] > 0
        and delta >= D(".01")
        and all(v > 0 for v in yearly.values())
        and metrics["candidate"]["dd"] <= metrics["control"]["dd"] + D(".02")
        and robust > 0
    )
    branch = (
        "insufficient"
        if not enough
        else "advance"
        if advance
        else "not_met"
        if (metrics["candidate"]["return"] <= 0 or delta <= 0)
        else "uncertain"
    )
    return {
        "branch": branch,
        "verdict": "supported"
        if advance and enough
        else "not_met"
        if branch == "not_met"
        else "inconclusive",
        "primary_increment": str(delta),
        "by_year": {k: m["years"] for k, m in metrics.items()},
        "year_increment": {k: str(v) for k, v in yearly.items()},
        "after_static_top3_increment": str(robust),
        "sample_sufficient": enough,
        "maximum_drawdown": {k: str(m["dd"]) for k, m in metrics.items()},
    }


def independent(q, members, identity, job, author_job):
    began = time.monotonic()
    author = admit_author(q, author_job)
    assert author["identity"]["request_digest"] == digest(q)
    if author["identity"]["git"] != identity["git"]:
        recovery = read(ROOT / "config/research/research-capital/independent-recovery.v1.json")
        assert recovery["request_digest"] == digest(q)
        assert recovery["producer_git"] == author["identity"]["git"]
        assert recovery["producer_result_digest"] == author["digest"]
        assert job.name == recovery["allowed_job_name"]
        failed = read(author_job.parent / recovery["failed_job_name"] / "guardian.json")
        assert failed["exit_code"] != 0 and failed["identity"]["request_digest"] == digest(q)
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", recovery["producer_git"], identity["git"]],
            cwd=ROOT,
            text=True,
        ).splitlines()
        assert set(changed) <= set(recovery["allowed_changed_paths"])

    assert author["digest"] == digest({k: v for k, v in author.items() if k != "digest"})
    verify_members(job / "source", q, members)
    for item in members:
        one = job / "source/DWD" / item["path"]
        two = author_job / "source/DWD" / item["path"]
        assert one.stat().st_ino != two.stat().st_ino or one.stat().st_dev != two.stat().st_dev
    for item in q.get("purpose", {}).get("members", []):
        one, two = job / "purpose" / item["path"], author_job / "purpose" / item["path"]
        assert one.stat().st_ino != two.stat().st_ino or one.stat().st_dev != two.stat().st_dev
    r2 = read(ROOT / q["r2_request"]["path"])
    r2_members = read(ROOT / r2["closure"]["path"])["members"]

    def resolver(*_):
        return r2, r2_members, job / "source/DWD", author

    def evaluate(_q, labelled, _author, _out, _began):
        base = labelled.loc[labelled.universe & labelled.base_selected].copy()
        base["rankvol"] = base.return_vol20.map(
            lambda v: D(str(v)).quantize(D(".000000000001"), rounding=ROUND_HALF_UP)
        )
        base = base.sort_values(["day", "selected", "rankvol", "listing_id"])
        selected = base.drop_duplicates(["day", "selected"], keep="first")
        skeleton = [
            {
                "date": r.day.date().isoformat(),
                "arm": "candidate" if r.selected else "control",
                "listing_id": r.listing_id,
                "ranking_vol12": format(r.rankvol, ".12f"),
            }
            for r in selected.itertuples()
        ]
        facts = IndependentFacts(job / "source/DWD", members, skeleton, q)
        assert facts.purpose_application == author.get("purpose_application")
        for r in skeleton:
            native = facts.row(r["listing_id"], date.fromisoformat(r["date"]))
            r["decision_hold"] = (
                native is None
                or native["reason_code"] != "TRADABLE_OBSERVED"
                or native["is_tradable"] is not True
                or native["bar_known"] is None
                or native["state_known"] is None
                or bool(facts.unknown_action_dates.get(native["security_id"], []))
            )
            r["decision_close"] = str(native["close"]) if native and native["close"] else "0"
        skeleton.sort(key=lambda r: (r["date"], r["arm"]))
        write(job / "nominal-reference.json", {"nominal_choices": skeleton})
        assert skeleton == author["nominal_choices"], "independent T nominal choices differ"
        arms = {}
        policy = read(ROOT / q["policy"]["path"])
        for name in ("candidate", "control"):
            arms[name] = referee(facts, skeleton, name, policy)
            write(job / ("reference-arm-" + name + ".json"), arms[name])
            formal = author["arms"][name]
            assert arms[name]["status"] == formal["status"], name + " outcome differs"
            assert arms[name]["nominal_attempts"] == formal["nominal_attempts"], (
                name + " T attempts differ"
            )
            assert arms[name]["p2_step_receipts"] == formal["p2_step_receipts"], (
                name + " cash-cent/fill/order receipts differ"
            )
            if arms[name]["status"] == "blocked":
                assert arms[name]["hold"] == formal["hold"], name + " exact source hold differs"
            else:
                assert D(arms[name]["ending_wealth"]) == D(
                    formal["result"]["performance"]["ending_equity"]
                )
                assert {k: D(v) for k, v in arms[name]["equity_curve"].items()} == {
                    r["market_date"]: D(r["system_equity"])
                    for r in formal["result"]["equity_curve"]
                }
                assert arms[name]["trades"] == [
                    {k: t[k] for k in ("entry_date", "exit_date", "pnl")}
                    for t in formal["result"]["trades"]
                ]

        decision = independent_decision(arms)
        assert decision["branch"] == author["decision"]["branch"]
        assert decision["verdict"] == author["decision"]["verdict"]
        if decision["primary_increment"] is not None:
            for key in ("primary_increment", "after_static_top3_increment"):
                assert D(decision[key]) == D(author["decision"][key])
            for year in ("2020", "2021"):
                assert D(decision["year_increment"][year]) == D(
                    author["decision"]["year_increment"][year]
                )
            for name in ("candidate", "control"):
                assert D(decision["maximum_drawdown"][name]) == D(
                    author["arms"][name]["result"]["performance"]["maximum_drawdown"]
                )
        facts.db.close()
        value = {
            "decision": decision,
            "schema": "quanthus.native-capital-independent.v1",
            "identity": identity,
            "source_cold_rebuilt": True,
            "purpose_application": facts.purpose_application,
            "physical_members": len(members),
            "member_bytes": sum(x["size_bytes"] for x in members),
            "nominal_choices_identical": True,
            "cash_cent_fill_order_receipts_identical": True,
            "arms": arms,
            "formal_result_digest": author["digest"],
            "capital_adoption": author["decision"]["branch"],
            "wall_seconds": time.monotonic() - began,
        }
        value["digest"] = digest(value)
        write(job / "independent.json", value)
        print(json.dumps({k: v for k, v in value.items() if k not in ("identity", "arms")}))
        return value

    return reproduce(author_job, job, {}, evaluator=evaluate, source_resolver=resolver)
