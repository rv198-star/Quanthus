"""Top-level bounded history reader (issue #749).

``read_history(market_date, decision_cutoff, holdings, pending)`` reads
the pinned sources once per Job, derives the per-venue open-session
windows, and hands the P11 adapter a ``p11.raw-history-input.v1``
envelope plus a coverage receipt.  The reader computes no listing age,
no event/feature gate and no candidate - it reports what the sources
carried, with UNKNOWN where coverage is unproven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from quanthus.p12_history import bars as bars_mod
from quanthus.p12_history import calendar_listing as cal_mod
from quanthus.p12_history import envelope as env_mod
from quanthus.p12_history import identity as id_mod
from quanthus.p12_history import supplements as sup_mod
from quanthus.p12_history.clocks import REPLAY_AS_OBSERVED, iso, parse_ts, utcnow
from quanthus.p12_history.source_pins import BASE_BUSINESS_THROUGH, SourceMap, sha256_file

BAR_SESSIONS = bars_mod.BAR_SESSIONS
LISTING_PROOF_SESSIONS = cal_mod.LISTING_PROOF_SESSIONS


@dataclass
class HistoryResult:
    """The reader's hand-off: a raw_history envelope + coverage receipt."""

    raw_history: dict
    coverage: dict
    read_at: str
    status: str = "READ"


def _window_sessions(calendar_rows, venue_id, market_date, count):
    sessions = sorted(
        r.session_date for r in calendar_rows
        if r.venue_id == venue_id and r.is_open and r.session_date <= market_date
    )
    return sessions[-count:]


def _date_iso(value):
    """bar session_date ISO string -> date, for the tradability join."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def read_history(
    market_date: date,
    decision_cutoff: datetime,
    *,
    holdings=(),
    pending=(),
    source_map: SourceMap | None = None,
    observed_at: datetime | None = None,
    replay_exclusion: dict | None = None,
    purpose: str = REPLAY_AS_OBSERVED,
) -> HistoryResult:
    """Read the bounded history for one T and assemble the raw_history."""
    smap = source_map or SourceMap()
    observed_at = observed_at or utcnow()
    read_at = iso(observed_at)

    # #737 replay exclusion (audit 749-09): bind the frozen exclusion list
    # by path AND declared sha256 before any security is admitted.  A wrong
    # or unreadable list is a named whole-day block (INPUT_BLOCKED) rather
    # than a guessed empty set - the exclusion is a hard pre-read gate.
    exclusion_block = None
    excluded_codes = set()
    excluded_dates = set()
    if replay_exclusion:
        excl_path = replay_exclusion.get("path")
        excl_sha = replay_exclusion.get("sha256")
        # A missing pin is itself a block (audit 749-09): the exclusion gate
        # is bound by path AND declared sha256 - no pin, no read.  We still
        # must NOT read members before the gate resolves, so compute the
        # list's digest lazily but gate everything on a declared pin.
        if not excl_sha:
            exclusion_block = {
                "reason_code": "REPLAY_EXCLUSION_UNPINNED",
                "detail": "#737 exclusion list has no pinned sha256; refusing to read",
                "affected_scope": "whole_day",
                "disposition": "block_new_entries",
            }
        actual = None
        if exclusion_block is None:
            try:
                actual = sha256_file(excl_path) if excl_path else None
            except Exception as exc:  # noqa: BLE001
                exclusion_block = {
                    "reason_code": "REPLAY_EXCLUSION_UNREADABLE",
                    "detail": f"#737 exclusion list could not be opened: {type(exc).__name__}: {exc}",
                    "affected_scope": "whole_day",
                    "disposition": "block_new_entries",
                }
        if exclusion_block is None and actual != excl_sha:
            exclusion_block = {
                "reason_code": "REPLAY_EXCLUSION_SHA_MISMATCH",
                "detail": f"#737 exclusion list sha256 {actual} != pinned {excl_sha}",
                "affected_scope": "whole_day",
                "disposition": "block_new_entries",
            }
        if exclusion_block is None:
            import json as _j
            try:
                doc = _j.loads(__import__("pathlib").Path(excl_path).read_text())
                excluded_codes = {str(c) for c in doc.get("securities") or []}
                excluded_dates = {str(d) for d in doc.get("excluded_dates") or []}
            except Exception as exc:  # noqa: BLE001
                exclusion_block = {
                    "reason_code": "REPLAY_EXCLUSION_UNREADABLE",
                    "detail": f"#737 exclusion list parse failed: {exc}",
                    "affected_scope": "whole_day",
                    "disposition": "block_new_entries",
                }

    # Gate before any member read (audit 749-09): a failed exclusion pin /
    # sha / read must leave ZERO market members opened - the exclusion is a
    # hard pre-read gate, not a post-hoc annotation.  Return the blocked
    # envelope immediately without touching a single source member.
    if exclusion_block is not None:
        empty_cov = {
            "read_at": read_at,
            "visible_at_label": purpose,
            "market_date": market_date.isoformat(),
            "calendar": {"reads": []},
            "listing": {"reads": []},
            "universe": {"reads": [], "pool_size": 0},
            "bars": {"reads": []},
            "actions": {},
            "exceptions": {"reads": []},
            "limits": {"reads": []},
            "security_count": 0,
            "replay_exclusion": {
                "bound": True,
                "path": str(replay_exclusion.get("path")),
                "declared_sha256": replay_exclusion.get("sha256"),
                "excluded_security_hits": [],
                "excluded_count": 0,
            },
            "day_block": exclusion_block,
            "member_consumption": {
                "consumed_member_count": 0, "consumed_for_T_count": 0,
                "base_member_count": 0, "daily_member_count": 0,
                "sha_all_match": True, "members": [],
                "registered_member_count": 0, "registered_not_read": [],
                "november_partitions_not_used": [], "run_read_at": read_at,
                "members_opened": 0,
            },
        }
        return HistoryResult(
            raw_history={
                "schema_version": "p11.raw-history-input.v1",
                "market_date": market_date.isoformat(),
                "decision_cutoff": iso(decision_cutoff),
                "source_members": [], "calendar_rows": [],
                "listing_rows": [], "securities": [],
            },
            coverage=empty_cov, read_at=read_at, status="INPUT_BLOCKED",
        )

    # Verify the fixed summary chain once per job (audit 749-01): the
    # Base manifest, the DATES index and each day's standard/core
    # descriptor are re-hashed against their frozen declared sha before
    # member reads are trusted.  A failed hard link is folded into the
    # day_block receipt below.
    manifest_chain = []
    chain_fn = getattr(smap, "manifest_chain", None)
    if callable(chain_fn):
        try:
            manifest_chain = chain_fn()
        except Exception as exc:  # noqa: BLE001
            # A map that cannot expose the chain (e.g. a stub without
            # manifest fields) degrades to a named note, never a block -
            # the members' own sha verification still governs reads.
            manifest_chain = [{"artifact": "manifest_chain", "status": "UNAVAILABLE",
                               "error": f"{type(exc).__name__}: {exc}"}]

    calendar_rows, cal_cov = cal_mod.read_calendar_rows(smap, market_date)
    listing_rows, list_cov = cal_mod.read_listing_rows(smap, market_date)
    # The venue's real native open instant for T (audit 749-03): the
    # half-open listing interval is tested against this instant, not the
    # bare calendar date.  A venue with no native open falls back to the
    # session date inside build_universe.
    open_at_by_venue = {
        c.venue_id: c.open_at
        for c in calendar_rows
        if c.session_date == market_date and c.is_open
    }
    identities, security_by_listing, uni_cov = id_mod.build_universe(
        smap, market_date, holdings, pending, {}, decision_cutoff,
        open_at_by_venue,
        # October replay keeps an in-force listing whose archive
        # knowledge_from post-dates T and marks it late_visible; a formal
        # production read drops it on real availability (audit 749-03).
        late_archive_as_observed=(purpose == REPLAY_AS_OBSERVED),
    )
    actions, act_cov = sup_mod.read_actions(smap, market_date)
    exceptions, exc_cov = sup_mod.read_exceptions(smap, market_date)
    limits, lim_cov = sup_mod.read_limits(smap, market_date)

    sessions_needed = set()
    for venue in {c.venue_id for c in calendar_rows}:
        for s in _window_sessions(calendar_rows, venue, market_date, BAR_SESSIONS):
            sessions_needed.add(s)
    sessions_sorted = sorted(sessions_needed)

    bars_by_listing, bar_cov = bars_mod.read_bars(
        smap, sessions_sorted, security_by_listing
    )

    # ---- assemble source_members (unique members actually opened) ----
    member_entries = {}
    def _member_key(member):
        # Package-qualified member key (audit 749-01): two daily packages
        # can ship the same relative member path (same-member-path.parquet
        # under daily-2026-11-23 and daily-2026-11-24).  The bare
        # member_id would collide and keep only the first; the composite
        # package_id::member_id keeps both registered and resolvable.
        return f"{member.package_id}::{member.member_id}"
    def _reg(member, knowledge_at, date_from=None, date_to=None, visible_at=None):
        key = _member_key(member)
        if key not in member_entries:
            e = env_mod.member_entry(
                member, knowledge_at,
                visible_at if visible_at is not None else knowledge_at,
                date_from, date_to,
            )
            e["package_key"] = key
            member_entries[key] = e
    # P11 裁定 (OCTOBER-REPLAY-TIME-v1): the original member clock is
    # carried verbatim - visible_at equals the member's own knowledge_at
    # and is NOT bounded by the decision_cutoff.  The logical replay
    # cutoff is declared separately on the envelope; consumers consume
    # through the versioned October-replay entry which reads the real
    # clock.  Members whose source clock post-dates the cutoff are still
    # named on the receipt (late_visible_members: renamed for byte-accuracy - their visible_at is the real source clock verbatim, never bounded by the cutoff).
    base_knowledge = datetime.fromisoformat(BASE_BUSINESS_THROUGH + "T16:00:00+00:00")
    late_visible_members = []
    for table in (
        "dwd_trading_calendar", "dwd_listing", "dwd_security",
        "dwd_security_identifier", "dwd_trading_venue", "dwd_listed_company",
        "dwd_company_industry_membership", "dwd_industry_node",
        "dwd_industry_scheme", "dwd_corporate_action",
        "dwd_corporate_action_term", "dwd_quality_exception",
        "dwd_observation_gap", "dwd_price_limit_regime",
        "dwd_tradability_state", "dwd_security_bar",
    ):
        for m in smap.base_members(table):
            if base_knowledge > decision_cutoff:
                late_visible_members.append(_member_key(m))
            _reg(m, base_knowledge, visible_at=base_knowledge)
    for day in smap.daily_open_days():
        if day > market_date.isoformat():
            continue
        pkg = smap.daily_core(day)
        if pkg is None:
            continue
        # Real member clocks (audit 749-02): use the producer's actual
        # index-completion / warehouse-observed instant, never a fabricated
        # day+16:00Z.  A package with no proven clock is a named gap and is
        # still registered (its knowledge_at stays None) so the consumer
        # sees the gap rather than a guessed cutoff-pass.
        day_knowledge = smap.daily_knowledge_at(day)
        day_visible = smap.daily_visible_at(day)
        for m in pkg.members:
            if day_knowledge is not None and day_knowledge > decision_cutoff:
                late_visible_members.append(_member_key(m))
            _reg(
                m, day_knowledge,
                date_from=day, date_to=day,
                visible_at=day_visible if day_visible is not None else day_knowledge,
            )
    source_member_entries = list(member_entries.values())

    # ---- per-security entries ----
    securities = []
    # Native session-state joins (P11 裁定): close_at and the source
    # knowledge clock come from the venue calendar row for that session;
    # is_tradable comes from dwd_tradability_state joined on
    # calendar_session_id; price-limit regimes join on venue + listing.
    # A fact the sources do not carry stays null - the reader never
    # derives tradability from the presence of a bar.
    venue_close = {}
    venue_knowledge = {}
    for c in calendar_rows:
        if c.close_at is not None:
            venue_close.setdefault(c.venue_id, {})[c.session_date.isoformat()] = iso(c.close_at)
        venue_knowledge.setdefault(c.venue_id, {})[c.session_date.isoformat()] = iso(c.knowledge_from)
    session_ids = cal_cov.get("session_ids") or {}
    tradability_by_key = {}
    for t in limits.get("tradability_state") or []:
        csid = str(t.get("calendar_session_id") or "")
        vd = session_ids.get(csid)
        if vd is None:
            continue
        venue_t, sess_t = vd
        tradability_by_key[(str(t.get("listing_id") or ""), sess_t)] = {
            "is_tradable": t.get("is_tradable"),
            "can_buy": t.get("can_buy"),
            "can_sell": t.get("can_sell"),
            "reason_code": str(t.get("reason_code") or ""),
            "observation_start": iso(parse_ts(t.get("observation_start"))),
            "upper_price": t.get("upper_price"),
            "lower_price": t.get("lower_price"),
        }
    limit_by_listing = {}
    for rg in limits.get("price_limit_regimes") or []:
        for lid in (rg.get("listing_id"),):
            if lid:
                limit_by_listing.setdefault(str(lid), []).append(
                    {
                        "upper_limit_ratio": rg.get("upper_limit_ratio"),
                        "lower_limit_ratio": rg.get("lower_limit_ratio"),
                        "security_status": rg.get("security_status"),
                        "ipo_phase": rg.get("ipo_phase"),
                        "effective_from": iso(parse_ts(rg.get("effective_from"))),
                        "effective_to": iso(parse_ts(rg.get("effective_to"))),
                    }
                )

    # Per-security original exception keys (audit 749-08): join each
    # dwd_quality_exception row to the security whose listing_id the
    # exception's affected_business_key names, within that security's
    # dependency window.  The original business key, authority,
    # disposition and resolution_state pass through verbatim - the
    # reader never reinterprets an exception, it only locates it.
    import json as _json
    exc_by_listing = {}
    for qe in (exceptions.get("quality_exceptions") or []):
        try:
            key = _json.loads(qe.get("affected_business_key_json") or "{}")
        except Exception:  # noqa: BLE001
            key = {}
        lid = str(key.get("listing_id") or qe.get("listing_id") or "")
        if not lid:
            continue
        exc_by_listing.setdefault(lid, []).append({
            "affected_table": str(qe.get("affected_table") or ""),
            "affected_business_key_json": qe.get("affected_business_key_json"),
            "affected_effective_at": iso(parse_ts(qe.get("affected_effective_at"))),
            "authority": str(qe.get("authority") or ""),
            "disposition": str(qe.get("disposition") or ""),
            "resolution_state": str(qe.get("resolution_state") or ""),
            "knowledge_from": iso(parse_ts(qe.get("knowledge_from"))),
        })

    # #737: a (stock_code, market_date) hit on the frozen exclusion list is
    # dropped BEFORE the envelope is assembled - the security never enters
    # this replay day.  The October window does not intersect the listed
    # dates, so the list is inert for phase 1; it is bound and counted here
    # so a later phase replays the same gate byte-for-byte.
    # Normalize the security's code to the bare 6-digit form before testing
    # the exclusion list (audit 749-09): the real warehouse carries
    # exchange-prefixed codes like "sz.300308" / "sh.600000", and the list
    # pins bare codes.  A hit is a (bare_code, date) pair; both the bare
    # form and any listed prefix form are honoured.
    def _bare(code):
        c = str(code or "")
        return c.split(".", 1)[1] if "." in c else c
    iso_date = market_date.isoformat()
    exclusion_hits = [
        i.security_id for i in identities
        if _bare(i.stock_code) in excluded_codes and iso_date in excluded_dates
    ]
    if exclusion_hits:
        identities = [i for i in identities if i.security_id not in set(exclusion_hits)]
        security_by_listing = {i.listing_id: i.security_id for i in identities if i.listing_id}
    # Account facts onto the wire (audit 749-04): the P11 contract reads
    # holdings and pending from securities[], so a held or pending
    # security carries its real quantity/kind on its own entry - an empty
    # account and a populated one must not produce the same envelope.
    holdings_by_sid = {}
    for h in holdings or ():
        hsid = str(h.get("security_id") or h.get("security_code") or "")
        if hsid:
            holdings_by_sid[hsid] = h
    pending_by_sid = {}
    for pd in pending or ():
        psid = str(pd.get("security_id") or pd.get("security_code") or "")
        if psid:
            pending_by_sid.setdefault(psid, []).append(pd)
    for ident in identities:
        raw_revisions = [
            bars_by_listing[ident.listing_id][s]
            for s in sessions_sorted
            if ident.listing_id in bars_by_listing and s in bars_by_listing[ident.listing_id]
        ]
        # PIT (audit 749-02): one session can carry several physical rows -
        # a late correction or a withdrawn re-post.  bars[] exposes the
        # last-known row for the H1 shape; the complete ordered revision
        # chain, each row with its own knowledge_from / source_revision /
        # record_source, travels alongside so a withdrawn late row is
        # preserved verbatim, never silently overwritten.
        sec_bars = []
        bar_revision_chain = {}
        for sess_revs in raw_revisions:
            ordered = sorted(
                sess_revs,
                key=lambda b: (
                    parse_ts(b.get("knowledge_from"))
                    or datetime.min.replace(tzinfo=timezone.utc)
                ),
            )
            # PIT select (audit 749-02): the formal cutoff picks the last
            # revision KNOWN by the decision cutoff - never a later row.
            # A withdrawn row (knowledge_to set before the cutoff) is kept
            # in the chain verbatim but does not win the select; a row
            # whose source clock post-dates the cutoff is recorded in the
            # chain and flagged late, never selected for a formal read.
            eligible = [
                b for b in ordered
                if (parse_ts(b.get("knowledge_from")) is None
                    or parse_ts(b.get("knowledge_from")) <= decision_cutoff)
                and (parse_ts(b.get("knowledge_to")) is None
                    or parse_ts(b.get("knowledge_to")) > decision_cutoff)
            ]
            chosen = (eligible or ordered)[-1]
            chosen["late_for_cutoff"] = bool(
                parse_ts(chosen.get("knowledge_from"))
                and parse_ts(chosen.get("knowledge_from")) > decision_cutoff
            )
            sec_bars.append(chosen)
            if len(ordered) > 1:
                bar_revision_chain[chosen["session_date"]] = [
                    {
                        "source_record_id": b.get("source_record_id"),
                        "source_revision": b.get("source_revision"),
                        "record_source": b.get("record_source"),
                        "knowledge_from": b.get("knowledge_from"),
                        "knowledge_to": b.get("knowledge_to"),
                        "close_price": b.get("close_price"),
                        "member_id": b.get("member_id"),
                        "package_id": b.get("package_id"),
                        "selected": b is chosen,
                        "late_for_cutoff": bool(
                            parse_ts(b.get("knowledge_from"))
                            and parse_ts(b.get("knowledge_from")) > decision_cutoff
                        ),
                    }
                    for b in ordered
                ]
        for b in sec_bars:
            sd = _date_iso(b.get("session_date"))
            t = tradability_by_key.get((ident.listing_id, sd))
            if t is not None:
                b["is_tradable"] = t.get("is_tradable")
                b["can_buy"] = t.get("can_buy")
                b["can_sell"] = t.get("can_sell")
                b["tradability_reason"] = t.get("reason_code") or None
        sec_actions = [
            a for a in actions.values() if a["security_id"] == ident.security_id
        ]
        bar_dates = {b.get("session_date") for b in sec_bars}
        all_close = venue_close.get(ident.venue_id) or {}
        all_cut = venue_knowledge.get(ident.venue_id) or {}
        close_map = {d: v for d, v in all_close.items() if d in bar_dates}
        cutoff_map = {d: v for d, v in all_cut.items() if d in bar_dates}
        sec_limits = limit_by_listing.get(ident.listing_id) or []
        # Exception window intersection (audit 749-05): an exception row is
        # projected onto this security only when the date in its affected
        # business key falls inside this security's actual 61-session bar
        # window - a same-listing exception whose session_date lies outside
        # the window is not this T's dependency and is not delivered.
        sec_exceptions = []
        for qe in exc_by_listing.get(ident.listing_id) or []:
            try:
                key = _json.loads(qe.get("affected_business_key_json") or "{}")
            except Exception:  # noqa: BLE001
                key = {}
            exc_date = key.get("session_date") or key.get("date") or key.get("effective_date")
            exc_d = _date_iso(exc_date)
            if exc_d is None or exc_d.isoformat() in bar_dates:
                # No date on the key -> keep it (the dependency is the
                # listing itself); a dated key must land in the window.
                sec_exceptions.append(qe)
        # Per-security dependency reasons (audit 749-04): name exactly why a
        # fact is missing instead of leaving an unexplained null - the P11
        # adapter distinguishes "no source rows" from "rows present but
        # empty", never a silent gap.
        dep_reasons = {}
        if not sec_bars:
            dep_reasons["bars"] = "no_qualified_1d_rows"
        if not any(k[0] == ident.listing_id for k in tradability_by_key):
            dep_reasons["tradability_state"] = "no_tradability_state_rows"
        if not sec_limits:
            dep_reasons["price_limit_regime"] = "no_price_limit_regime_rows"
        if not any(k[0] == ident.listing_id for k in exc_by_listing):
            dep_reasons["quality_exceptions"] = "no_exception_rows"
        # Account facts for this security (audit 749-04): held quantity and
        # typed pending travel on the wire so the rule can issue an
        # independent hold_alert / qualified exit - never left to coverage
        # counters alone.
        hold = holdings_by_sid.get(ident.security_id)
        pends = pending_by_sid.get(ident.security_id) or []
        account = {
            "account_role": (
                "held" if hold is not None else ("pending" if pends else "candidate")
            ),
            "held_quantity": (hold.get("quantity") if hold else None),
            "pending": [
                {
                    "pending_kind": p.get("pending_kind") or p.get("kind"),
                    "pending_quantity": p.get("pending_quantity") or p.get("quantity"),
                }
                for p in pends
            ],
        }
        securities.append(
            env_mod.security_entry(
                ident, sec_bars, sec_actions,
                sec_exceptions,
                sec_limits,
                close_map, cutoff_map,
                bar_revisions=bar_revision_chain,
                dependency_reasons=dep_reasons,
                account=account,
            )
        )

    # Out-of-pool account members (audit 749-04): a held or pending
    # security that failed the candidate listing test still gets a
    # securities[] entry carrying its account facts so the P11 contract
    # sees it.  A security with no resolvable pool identity is emitted with
    # UNKNOWN identity fields and an explicit account role - the rule
    # evaluates it as an independent holding (hold_alert) or pending
    # order, never dropped silently.
    pool_sids = {i.security_id for i in identities}
    extra_account_sids = sorted(
        set(holdings_by_sid) | set(pending_by_sid) - pool_sids
    )
    for sid in extra_account_sids:
        if sid in pool_sids:
            continue
        hold = holdings_by_sid.get(sid)
        pends = pending_by_sid.get(sid) or []
        pseudo = id_mod.SecurityIdentity(
            security_id=sid,
            stock_code=sid,
            listing_code=sid,
            security_name=id_mod.UNKNOWN,
            industry_id=id_mod.UNKNOWN,
            venue_id=id_mod.UNKNOWN,
            instrument_type=id_mod.COMMON_EQUITY,
            listing_id="",
            company_id="",
            depends_on_member_ids=(),
            invalidations=(),
            price_bound=None,
            source_evidence_digest="",
            late_visible=False,
        )
        securities.append(
            env_mod.security_entry(
                pseudo, [], [], [], [], {}, {},
                bar_revisions={},
                dependency_reasons={"identity": "out_of_pool_account_member"},
                account={
                    "account_role": ("held" if hold is not None else "pending"),
                    "held_quantity": (hold.get("quantity") if hold else None),
                    "pending": [
                        {
                            "pending_kind": p.get("pending_kind") or p.get("kind"),
                            "pending_quantity": p.get("pending_quantity") or p.get("quantity"),
                        }
                        for p in pends
                    ],
                },
            )
        )

    raw_history = env_mod.build_envelope(
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_member_entries=source_member_entries,
        calendar_rows=calendar_rows,
        listing_rows=listing_rows,
        securities=securities,
    )

    # Actual consumption closure (P11 裁定): only members actually opened
    # this run count for formal use.  Each reads[] entry already carries
    # sha_match from verify_member; here we re-resolve the member_id to
    # pin its package_kind (base / daily-core), absolute path, version,
    # composite key and the rows this T used.  November daily partitions
    # registered in the envelope are named "not consumed for this T" -
    # their sealed October evidence and original clocks stay verbatim.
    consumed = {}

    def _collect(section, key):
        seg = section.get(key) if isinstance(section, dict) else section
        for rec in seg or []:
            mid = rec.get("member_id")
            if not mid or rec.get("status") != "READ":
                continue
            # Package-qualified key when the read emitted one (same-path
            # members across two daily packages stay distinct).
            key = rec.get("member_key") or mid
            if key in consumed:
                consumed[key]["uses"].append(key)
                continue
            resolver = getattr(smap, "resolve", None)
            resolved = (resolver(key) or resolver(mid)) if callable(resolver) else None
            consumed[key] = {
                "member_id": mid,
                "member_key": key,
                "package_id": (resolved.package_id if resolved else rec.get("package_id")),
                "package_key": (
                    f"{(resolved.package_id if resolved else rec.get('package_id'))}::{mid}"
                ),
                "package_kind": (resolved.package_kind if resolved else "unresolved"),
                "version": (resolved.version if resolved else None),
                "abs_path": (str(resolved.abs_path) if resolved else rec.get("path")),
                "sha256": rec.get("sha256"),
                "sha_match": bool(rec.get("sha_match")),
                "rows": rec.get("rows"),
                "kept": rec.get("kept"),
                "admitted": rec.get("admitted"),
                "status": rec.get("status"),
                "verify_at": rec.get("verified_at") or read_at,
                "uses": [key],
            }

    _collect(cal_cov, "reads")
    _collect(list_cov, "reads")
    _collect(uni_cov, "reads")
    _collect(bar_cov, "reads")
    _collect(act_cov, "action_reads")
    _collect(act_cov, "term_reads")
    _collect(exc_cov, "reads")
    _collect(lim_cov, "reads")

    # A member is "relevant for this T" unless it is a November daily-core
    # partition - those post-date T and are registered for sealed-October
    # evidence only, never consumed as this T's fact.
    def _november(mid, pkg):
        return "market_date=2026-11" in mid or "dwd-daily-2026-11" in str(pkg or "")

    for mid, v in consumed.items():
        v["relevant_for_T"] = not _november(mid, v.get("package_id"))
    relevant = [v for v in consumed.values() if v["relevant_for_T"]]
    november_not_used = sorted(
        v["member_id"] for v in consumed.values() if not v["relevant_for_T"]
    )
    registered_ids = {m.get("package_key") or m["member_id"] for m in source_member_entries}
    not_read = sorted(registered_ids - set(consumed))

    member_consumption = {
        "consumed_member_count": len(consumed),
        "consumed_for_T_count": len(relevant),
        "base_member_count": sum(1 for v in consumed.values() if v["package_kind"] == "base"),
        "daily_member_count": sum(
            1 for v in consumed.values() if v["package_kind"] == "daily-core"
        ),
        "sha_all_match": all(v["sha_match"] for v in consumed.values()),
        "members": sorted(consumed.values(), key=lambda v: v["member_id"]),
        "registered_member_count": len(source_member_entries),
        "registered_not_read": not_read,
        "november_partitions_not_used": november_not_used,
        "run_read_at": read_at,
        "clock_role": (
            "verify_at = the wall-clock instant this member's sha256 "
            "finished re-hashing (its own verification clock); members "
            "registered but never hash-checked carry verify_at=None. "
            "run_read_at is the job-start clock, recorded separately."
        ),
        "note": (
            "Members actually opened and sha-verified this run. Registered "
            "envelope source_members may exceed consumed members; formal use "
            "counts only the consumed set. November daily partitions are "
            "named not-used for this T; their sealed evidence and clocks "
            "stay verbatim in the envelope."
        ),
    }

    coverage = {
        "read_at": read_at,
        "visible_at_label": purpose,
        "market_date": market_date.isoformat(),
        "replay_exclusion": {
            "bound": bool(replay_exclusion),
            "path": str(replay_exclusion.get("path")) if replay_exclusion else None,
            "declared_sha256": replay_exclusion.get("sha256") if replay_exclusion else None,
            "excluded_security_hits": exclusion_hits,
            "excluded_count": len(exclusion_hits),
        },
        "calendar": cal_cov,
        "listing": list_cov,
        "universe": uni_cov,
        "bars": bar_cov,
        "actions": act_cov,
        "exceptions": exc_cov,
        "limits": lim_cov,
        "security_count": len(securities),
        "bar_sessions": BAR_SESSIONS,
        "listing_proof_sessions": LISTING_PROOF_SESSIONS,
        "late_visible_members": late_visible_members,
        "member_consumption": member_consumption,
        "note": (
            "Corporate-action/exception completeness for the covered window is "
            "UNKNOWN (0 securities proven; 2026-11 deltas SOURCE_NOT_REFRESHED). "
            "Native rows present are delivered verbatim; the P11 rule decides "
            "which securities drop."
        ),
    }
    # Whole-day block: a shared hard dependency failed (the venue
    # calendar / listing / identity chain every security derives from).
    # Emit a named day_block receipt and mark the run INPUT_BLOCKED -
    # distinct from a clean zero-candidate read.  Per-security member
    # faults stay isolated on their own reads and do NOT block the day.
    day_block = None
    # A shared hard dependency missing entirely (no members declared) is a
    # whole-day block too (audit 749-05): "not registered" and "all
    # unreadable" are equally unusable - the day cannot be read, distinct
    # from a clean zero-candidate read.
    cal_sources = list(smap.base_members("dwd_trading_calendar"))
    if not cal_sources:
        day_block = {
            "reason_code": "SHARED_CALENDAR_UNAVAILABLE",
            "detail": "no venue calendar member is declared in the pinned map",
            "affected_scope": "whole_day",
            "failed_members": [],
            "disposition": "block_new_entries",
        }
    elif cal_cov.get("shared_failure"):
        day_block = {
            "reason_code": "SHARED_CALENDAR_UNAVAILABLE",
            "detail": (
                "no usable venue calendar rows: every shared calendar "
                "member failed hash or was unreadable"
            ),
            "affected_scope": "whole_day",
            "failed_members": cal_cov.get("failed_members") or [],
            "disposition": "block_new_entries",
        }
    coverage["manifest_chain"] = manifest_chain
    chain_faults = [c for c in manifest_chain if c.get("status") not in ("MATCH", "NO_PATH", "UNAVAILABLE")]
    if chain_faults and day_block is None:
        day_block = {
            "reason_code": "SUMMARY_CHAIN_FAULT",
            "detail": (
                "a declared manifest/dates/descriptor link failed "
                "hash or was unreadable"
            ),
            "affected_scope": "whole_day",
            "failed_members": [c.get("artifact") for c in chain_faults],
            "disposition": "block_new_entries",
        }
    # Listing or all-qualified-price collapse is also a whole-day block:
    # "zero listings" (nothing can be a candidate) and "every bar member
    # failed" are input failures, distinct from a real zero-signal day.
    if day_block is None and not listing_rows and list(smap.base_members("dwd_listing")):
        day_block = {
            "reason_code": "SHARED_LISTING_UNAVAILABLE",
            "detail": "no usable listing rows though listing members are declared",
            "affected_scope": "whole_day",
            "failed_members": [
                r.get("member_id") for r in (list_cov.get("reads") or [])
                if r.get("status") != "READ"
            ],
            "disposition": "block_new_entries",
        }
    if day_block is None and not list(smap.base_members("dwd_listing")):
        day_block = {
            "reason_code": "SHARED_LISTING_UNAVAILABLE",
            "detail": "no listing member is declared in the pinned map",
            "affected_scope": "whole_day",
            "failed_members": [],
            "disposition": "block_new_entries",
        }
    # All-qualified-price collapse (audit 749-05): every declared bar
    # member failed hash or was unreadable -> the day cannot be priced.
    # Distinct from a real zero-bar day (members READ, just no rows).
    if day_block is None:
        bar_reads = bar_cov.get("reads") or []
        bar_members = [
            m for m in smap.base_members("dwd_security_bar")
            if "interval_size=1d" in m.member_id
        ]
        bar_members += [
            m for d in smap.daily_open_days()
            for m in ([smap.daily_member(d, "dwd_security_bar")] or [])
            if m is not None
        ]
        if bar_members and bar_reads and all(
            r.get("status") in ("SHA_MISMATCH", "UNREADABLE")
            for r in bar_reads if r.get("member_id")
        ):
            day_block = {
                "reason_code": "SHARED_PRICE_UNAVAILABLE",
                "detail": "every declared bar member failed hash or was unreadable",
                "affected_scope": "whole_day",
                "failed_members": [r.get("member_id") for r in bar_reads],
                "disposition": "block_new_entries",
            }
    if exclusion_block is not None:
        day_block = exclusion_block
    coverage["day_block"] = day_block
    status = "INPUT_BLOCKED" if day_block is not None else "READ"
    return HistoryResult(
        raw_history=raw_history, coverage=coverage,
        read_at=read_at, status=status,
    )
