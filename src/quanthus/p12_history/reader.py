"""Top-level bounded history reader (issue #749).

``read_history(market_date, decision_cutoff, holdings, pending)`` reads
the pinned sources once per Job, derives the per-venue open-session
windows, and hands the P11 adapter a ``p11.raw-history-input.v1``
envelope plus a coverage receipt.  The reader computes no listing age,
no event/feature gate and no candidate - it reports what the sources
carried, with UNKNOWN where coverage is unproven.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from quanthus.p12_history import bars as bars_mod
from quanthus.p12_history import calendar_listing as cal_mod
from quanthus.p12_history import envelope as env_mod
from quanthus.p12_history import identity as id_mod
from quanthus.p12_history import supplements as sup_mod
from quanthus.p12_history.clocks import FORMAL_DAILY_UPDATE, REPLAY_AS_OBSERVED, iso, parse_ts, utcnow
from quanthus.p12_history.source_pins import BASE_BUSINESS_THROUGH, SourceMap, sha256_file

BAR_SESSIONS = bars_mod.BAR_SESSIONS
LISTING_PROOF_SESSIONS = cal_mod.LISTING_PROOF_SESSIONS

# Versioned whole-bar consumed-fact contract (audit R4 seg-A hardgate
# closeout): ``BAR_FACT_KEYS`` is bound to the SINGLE versioned consumed-bar
# field contract in ``bars_mod.BAR_FIELD_CONTRACT`` - the set of produced
# fact keys the consumer reads to judge one bar's business fact at a
# knowledge clock.  ``_bar_fact`` collapses equivalent same-clock replicas
# ONLY over exactly this set; two rows that differ on any consumed field
# are a genuine conflict, never picked by physical order.  The tuple order
# follows the contract's declaration order so the collapse comparator and
# the consumer's emitted-bar fact fields stay bound to ONE declaration
# (a produced-field change must re-version the contract AND the comparator).
BAR_FACT_KEYS = tuple(
    v["produces"] for v in bars_mod.BAR_FIELD_CONTRACT.values()
    if v["role"] == "fact" and v["produces"] is not None
)


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


def _chain_fault_in_window(fault, needed_days, market_date):
    """A manifest-chain fault blocks this T only when the link belongs to a
    session this read actually needs.  A fault on a descriptor or core pinned
    for a day OUTSIDE the needed window is kept on the chain receipt but must
    not starve the day (audit R4 segment A closeout).  Links without a day
    suffix (the Base manifest) always count."""
    artifact = str(fault.get("artifact") or "")
    m = re.search(r":(\d{4}-\d{2}-\d{2})$", artifact)
    if m is None:
        return True
    day = m.group(1)
    return day in needed_days or day == market_date.isoformat()


def read_history(
    market_date: date,
    decision_cutoff: datetime,
    *,
    holdings=(),
    pending=(),
    account_snapshot=None,
    expected_account_id=None,
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
    # A missing or empty binding is itself a named block (audit R4):
    # the October replay entry always carries the pinned list, so a
    # caller that supplies None or {} is the UNPINNED case - never a
    # silent pass.
    if not replay_exclusion and purpose == REPLAY_AS_OBSERVED:
        exclusion_block = {
            "reason_code": "REPLAY_EXCLUSION_UNPINNED",
            "detail": (
                "#737 exclusion binding is absent or empty; the replay "
                "entry requires the pinned list before any member read"
            ),
            "affected_scope": "whole_day",
            "disposition": "block_new_entries",
        }
        replay_exclusion = {}
    if not exclusion_block and replay_exclusion:
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

    # A failed fixed-summary link is a hard pre-read gate (audit 749 R4
    # second segment): the Base manifest, the DATES index and the day's
    # descriptors are verified BEFORE any member is trusted, so a broken
    # / wrong / unreadable link blocks the whole day without opening a
    # single market member.  Formal update (audit R4 second segment):
    # the four external frozen links must each be present, declared,
    # readable and sha-matched - a missing path, a missing declared sha,
    # an unreadable link or a wrong sha is the block.  UNAVAILABLE stays
    # advisory only when the stub cannot expose a chain at all.
    _chain_faults = [
        c for c in manifest_chain
        if c.get("status") in ("NO_PATH", "MISSING_DECLARED_SHA256",
                               "NON_OBJECT_DESCRIPTOR", "UNREADABLE",
                               "SHA_MISMATCH", "CONTENT_NON_OBJECT",
                               "CONTENT_MALFORMED_JSON",
                               "CONTENT_MISSING_IDENTITY_FIELD",
                               "ASSOCIATED_IDENTITY_MISMATCH",
                               "CONTENT_IDENTITY_VALUE_INVALID",
                               "CHAIN_ASSOCIATION_MISMATCH")
    ]
    # Window scope (audit R4 segment A closeout): only a link for a session
    # this read actually needs is a pre-read block.  A fault pinned for a day
    # outside the needed window is kept on the chain receipt but must not
    # starve the day.  The needed window is approximated from the pinned map
    # (the last BAR_SESSIONS open days at or before T) so this gate still runs
    # before any member parquet is opened.
    try:
        _open_days = [
            d for d in smap.daily_open_days() if d <= market_date.isoformat()
        ]
        _needed_days = set(_open_days[-BAR_SESSIONS:]) | {
            market_date.isoformat()}
    except Exception:  # noqa: BLE001
        _needed_days = {market_date.isoformat()}
    _chain_faults = [
        c for c in _chain_faults
        if _chain_fault_in_window(c, _needed_days, market_date)
    ]
    if _chain_faults:
        return HistoryResult(
            raw_history={
                "schema_version": "p11.raw-history-input.v1",
                "market_date": market_date.isoformat(),
                "decision_cutoff": iso(decision_cutoff),
                "source_members": [], "calendar_rows": [],
                "listing_rows": [], "securities": [],
            },
            coverage={
                "read_at": read_at,
                "visible_at_label": purpose,
                "market_date": market_date.isoformat(),
                "manifest_chain": manifest_chain,
                "calendar": {"reads": []}, "listing": {"reads": []},
                "universe": {"reads": [], "pool_size": 0},
                "bars": {"reads": []}, "actions": {},
                "exceptions": {"reads": []}, "limits": {"reads": []},
                "security_count": 0,
                "late_visible_members": [],
                "member_consumption": {
                    "consumed_member_count": 0, "consumed_for_T_count": 0,
                    "base_member_count": 0, "daily_member_count": 0,
                    "sha_all_match": True, "members": [],
                    "registered_member_count": 0, "registered_not_read": [],
                    "november_partitions_not_used": [], "run_read_at": read_at,
                    "members_opened": 0,
                },
                "day_block": {
                    "reason_code": "SUMMARY_CHAIN_FAULT",
                    "detail": "a declared manifest/dates/descriptor link failed hash or was unreadable before any member was opened",
                    "affected_scope": "whole_day",
                    "failed_members": [c.get("artifact") for c in _chain_faults],
                    "disposition": "block_new_entries",
                },
            },
            read_at=read_at, status="INPUT_BLOCKED",
        )

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
    p1_exc, p1_exc_cov = sup_mod.read_p1_exceptions(smap, market_date)

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
    pending_aliases = {}
    def _member_key(member):
        # Package-qualified member key (audit 749-01): two daily packages
        # can ship the same relative member path (same-member-path.parquet
        # under daily-2026-11-23 and daily-2026-11-24).  The bare
        # member_id would collide and keep only the first; the composite
        # package_id::member_id keeps both registered and resolvable.
        return f"{member.package_id}::{member.member_id}"
    def _reg(member, knowledge_at, date_from=None, date_to=None, visible_at=None,
             producer_completed_at=None, channel_visible_at=None,
             keep_null_visible=False):
        # A P10 calendar-map daily alias is the SAME physical member as
        # the formal daily-core calendar member it points at (P10
        # ruling).  Register it as an alias ON the formal identity - it
        # must NOT open a separate entry clocked at base_knowledge, which
        # would let the alias bypass the formal channel-admission gate.
        if getattr(member, "alias_of", None):
            target = member_entries.get(member.alias_of)
            alias_rec = {
                "member_key": _member_key(member),
                "member_id": member.member_id,
                "package_id": member.package_id,
                "sha256": member.sha256,
                "abs_path": str(member.abs_path),
            }
            if target is not None:
                target.setdefault("aliases", []).append(alias_rec)
            else:
                pending_aliases.setdefault(member.alias_of, []).append(alias_rec)
            return
        key = _member_key(member)
        if key not in member_entries:
            # keep_null_visible: a formal read whose channel is UNKNOWN
            # keeps visible_at null (a named gap) instead of back-filling
            # the member's own knowledge clock.
            eff_visible = (
                visible_at if (visible_at is not None or keep_null_visible)
                else knowledge_at
            )
            e = env_mod.member_entry(
                member, knowledge_at,
                eff_visible,
                date_from, date_to,
                producer_completed_at=producer_completed_at,
                channel_visible_at=channel_visible_at,
            )
            e["package_key"] = key
            for alias_rec in pending_aliases.pop(key, []):
                e.setdefault("aliases", []).append(alias_rec)
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
    # P1 factor-PIT exception ledger (audit 749-05): registered in the
    # envelope source_members with its own pinned publication + sha + the
    # original source clock so member_consumption's relevant_for_T entry
    # resolves to a real envelope member (P11 load_replay_request requires
    # the consumed member to be supplied).
    p1_member = smap.p1_exception_member()
    _reg(p1_member, base_knowledge, visible_at=base_knowledge)
    # Second-segment formal update: production-complete and channel-
    # visible are separate instants (audit 749 R4).  Under FORMAL_DAILY_
    # UPDATE a package is admissible only when its QUALIFIED channel
    # visibility is at or before the cutoff - a member that completed
    # production early but only became channel-visible after the cutoff
    # is named late and contributes nothing.  Under the October replay
    # the real knowledge clock is used verbatim as before.
    formal = purpose == FORMAL_DAILY_UPDATE
    formal_inadmissible_keys = set()
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
        day_producer = smap.daily_producer_completed_at(day)
        # Channel visibility for a FORMAL read only counts the qualified
        # publication/withdrawal chain - never the producer-completion
        # fallback (audit R4 second segment: a member with no channel
        # proof is a named gap, not silently admitted).  The October
        # replay still consumes the real clock via daily_visible_at.
        day_channel = (
            smap.daily_channel_visible_at(day)
            if formal else smap.daily_visible_at(day)
        )
        # Formal admission is judged on the channel-visible instant and
        # splits two DISTINCT states (audit R4 second segment):
        #   * rejected_no_channel_proof - the channel clock is UNKNOWN
        #     (no qualified publication/withdrawal event); the member is
        #     rejected for lack of proof, NOT merely late.  Its
        #     visible_at stays null (never back-filled from knowledge).
        #   * late_for_cutoff - a qualified channel clock exists and is
        #     genuinely after the decision cutoff.
        admissible_instant = day_channel if formal else day_knowledge
        for m in pkg.members:
            rejected_no_proof = False
            late = False
            if formal:
                if day_channel is None:
                    rejected_no_proof = True   # channel UNKNOWN -> reject
                elif day_channel > decision_cutoff:
                    late = True              # qualified but genuinely late
            elif admissible_instant is not None and admissible_instant > decision_cutoff:
                late = True
            if late:
                late_visible_members.append(_member_key(m))
                if formal:
                    formal_inadmissible_keys.add(_member_key(m))
            if rejected_no_proof and formal:
                formal_inadmissible_keys.add(_member_key(m))
            _reg(
                m, day_knowledge,
                date_from=day, date_to=day,
                # channel UNKNOWN keeps visible_at null under a formal
                # read - the receipt shows the gap, never a guess.  The
                # replay still uses the real clock verbatim.
                visible_at=(day_channel if formal
                            else (day_channel if day_channel is not None else day_knowledge)),
                producer_completed_at=day_producer,
                channel_visible_at=day_channel,
                keep_null_visible=formal,
            )
            if formal:
                # Late-for-cutoff receipt + the named input gap a P10
                # shared-source proof must fill (audit R4 second
                # segment): a member with no proven channel/knowledge
                # clock is named, never silently qualified.  A gap on a
                # shared hard dependency (venue calendar, shared listing
                # identity, a needed price source, or the fixed summary
                # chain) blocks the whole day for new entries; a single-
                # security or single-action gap is isolated to that
                # dependency so a qualified neighbour keeps working.
                ent = member_entries[_member_key(m)]
                ent["formal_admission"] = (
                    "rejected_no_channel_proof" if rejected_no_proof
                    else ("late_for_cutoff" if late else "admitted"))
                mid_l = str(m.member_id or "")
                # Dependency type per P10 MEMBER-GAPS: the T shared
                # price and the native T session/calendar are shared
                # hard dependencies (their gap blocks the whole day);
                # listing/identity/security extensions and tradability
                # are conditional (applied only to a use that actually
                # depends on the member); audit/universe/other members
                # are declared-not-read.
                # Window scoping (audit R4 second segment): only THIS T's
                # package member is an actual shared dependency - a member
                # declared for a different day (market_date != T) is an
                # out-of-window registration whose gap is kept for the
                # record but must NOT block this T's day.
                # Shared-dependency scope (audit R4 segment A closeout):
                # a member is a shared hard dependency when its day is one
                # of THIS read's actually-needed sessions - not only the
                # market_date.  A prior in-window trading day whose Daily
                # price/calendar member is the pool's sole source for that
                # session and is channel-unknown or late blocks new
                # entries.  Members for days outside the needed window are
                # kept for the record but do not block.
                needed_days = {
                    (d.isoformat() if hasattr(d, "isoformat") else str(d))
                    for d in sessions_needed
                }
                in_t_window = (
                    day in needed_days or day == market_date.isoformat())

                # dwd_security_valuation is a DECLARED_NOT_READ_ONLY
                # member per the P10 named supply - it only shares the
                # "dwd_security" PREFIX, so it is tested before the
                # dwd_security identity branch.
                if "dwd_security_valuation" in mid_l:
                    dep_type = "DECLARED_NOT_READ_ONLY"
                    shared_dep = False
                elif "dwd_security_bar" in mid_l:
                    dep_type = "SHARED_T_PRICE"
                    shared_dep = in_t_window
                elif "dwd_trading_calendar" in mid_l:
                    dep_type = "NATIVE_T_SESSION_AND_TIME"
                    shared_dep = in_t_window
                elif "dwd_tradability_state" in mid_l:
                    dep_type = "CONDITIONAL_T_TRADABILITY"
                    shared_dep = False
                elif ("dwd_listing" in mid_l or "dwd_security" in mid_l
                      or "dwd_listed_company" in mid_l
                      or "dwd_company_identifier" in mid_l):
                    dep_type = "CONDITIONAL_T_IDENTITY"
                    shared_dep = False
                else:
                    dep_type = "DECLARED_NOT_READ_ONLY"
                    shared_dep = False
                if day_channel is None and day_knowledge is None:
                    ent["named_input_gap"] = (
                        "NO_PROVEN_CHANNEL_OR_KNOWLEDGE_CLOCK"
                    )
                elif day_channel is None:
                    ent["named_input_gap"] = "NO_CHANNEL_VISIBILITY_PROOF"
                if ent.get("named_input_gap"):
                    ent["dependency_type"] = dep_type
                    if shared_dep:
                        ent["shared_hard_dependency"] = True
                        # The session this shared member supplies (its own
                        # market date) - the outer gate judges whether a
                        # qualified source actually covered that session.
                        ent["gap_session_day"] = day
                elif shared_dep and ent.get("formal_admission") == "late_for_cutoff":
                    # A genuinely late member supplies its session nothing for
                    # THIS formal read either - it is a shared-dependency gap
                    # even though its channel clock is proven (audit R4 segment
                    # A closeout: a late sole supply misses the block the same
                    # way an unknown one does).  It is named late on the
                    # receipt AND enters the shared-gap gate.
                    ent["dependency_type"] = dep_type
                    ent["shared_hard_dependency"] = True
                    ent["gap_session_day"] = day

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

    # P1 factor-PIT exceptions by bare stock_code (audit 749-05): the
    # ledger keys on bare stock_code + exchange + effective_date.  Match
    # the security's bare code and admitted venue, keep the original keys
    # verbatim - a same-code exception outside the security's bar window
    # is filtered by its effective_date below.
    p1_by_code = {}
    for pe in (p1_exc.get("p1_exceptions") or []):
        code = str(pe.get("stock_code") or "")
        exch = str(pe.get("exchange") or "")
        if not code:
            continue
        p1_by_code.setdefault(code, []).append({
            "stock_code": code,
            "exchange": exch,
            "effective_date": str(pe.get("effective_date") or ""),
            "exception_type": str(pe.get("exception_type") or ""),
            "reason_code": str(pe.get("reason_code") or ""),
            "event_type": str(pe.get("event_type") or ""),
            "visibility_origin": str(pe.get("visibility_origin") or ""),
            "knowledge_cutoff": str(pe.get("knowledge_cutoff") or ""),
            "source_revision": str(pe.get("source_revision") or ""),
            "recorded_at": str(pe.get("recorded_at") or ""),
            "policy": str(pe.get("policy") or ""),
            "ledger": "P1_factor_pit_exception",
        })

    p1_unverified = bool(p1_exc_cov.get("p1_unverified"))
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
    # Account-input binding (audit 749 R4 segment B): a production read
    # binds the SAME account input to a scenario account, its original
    # as_of instant, the snapshot sha and the generation receipt BEFORE
    # the account facts are projected.  A different account swapped in, a
    # future-dated snapshot, a security/listing/venue three-key conflict
    # or a wrong snapshot sha is a NAMED rejection - the account branch is
    # refused, never silently accepted.
    account_binding = {
        "bound": False,
        "account_id": None,
        "scenario": None,
        "account_mode": None,
        "environment": None,
        "as_of": None,
        "snapshot_path": None,
        "source_databases": None,
        "nested_account": None,
        "declared_snapshot_sha256": None,
        "actual_snapshot_sha256": None,
        "generation_receipt_sha256": None,
        "rejections": [],
    }
    if account_snapshot is not None:
        snap = account_snapshot
        account_binding["bound"] = True
        account_binding["account_id"] = snap.get("account_id")
        # P11 segment-B contract: keep the scenario namespace, the raw
        # account mode/environment and the source databases verbatim so
        # the consumer re-pairs the bound input inside the closure.
        account_binding["scenario"] = snap.get("scenario")
        account_binding["account_mode"] = snap.get("account_mode")
        account_binding["environment"] = snap.get("environment")
        account_binding["as_of"] = snap.get("as_of")
        account_binding["source_databases"] = snap.get("source_databases")
        # Original nested account fields preserved verbatim for forensics.
        account_binding["nested_account"] = snap.get("account")
        snap_path = snap.get("snapshot_path")
        snap_sha = snap.get("snapshot_sha256") or snap.get("sha256")
        account_binding["snapshot_path"] = str(snap_path) if snap_path else None
        account_binding["declared_snapshot_sha256"] = snap_sha
        # Same-bytes binding (audit R4 seg-B second pass): when a snapshot
        # path is bound we verify ITS sha, then decode THAT SAME byte
        # content and project the account facts from the verified bytes -
        # never from the caller-supplied mapping.  A mapping whose
        # holdings/pending/as_of were cleared or re-pointed while still
        # borrowing the real file's sha is a refusal, not a verified
        # empty account.
        snap_v = None
        if snap_path and snap_sha:
            # Same-bytes closeout: read the bound snapshot ONCE - the single
            # in-memory bytes feed both the sha256 and the decode, so verify
            # and project never re-open the path for a swapped second read.
            # Binding-window identity (closeout pass 2): the path may be
            # atomically replaced or rewritten in place AFTER the bytes are
            # read.  Hold one fd, fstat before and after the read, and stat
            # the path again at bind completion; any change in
            # st_dev/st_ino/st_size/st_mtime_ns/st_ctime_ns is a named
            # ACCOUNT_SNAPSHOT_PATH_CHANGED refusal.  Verification and
            # decode still act ONLY on the first in-memory bytes.
            _STAT_KEYS = ("st_dev", "st_ino", "st_size",
                          "st_mtime_ns", "st_ctime_ns")

            def _stat_sig(st):
                return {k: getattr(st, k, None) for k in _STAT_KEYS}

            snap_bytes = None
            _pre_sig = _post_sig = _final_sig = None
            try:
                _fd = os.open(snap_path, os.O_RDONLY)
                try:
                    _pre_sig = _stat_sig(os.fstat(_fd))
                    _chunks = []
                    while True:
                        _c = os.read(_fd, 1 << 20)
                        if not _c:
                            break
                        _chunks.append(_c)
                    snap_bytes = b"".join(_chunks)
                    _post_sig = _stat_sig(os.fstat(_fd))
                finally:
                    os.close(_fd)
            except Exception as exc:  # noqa: BLE001
                snap_bytes = None
                account_binding["rejections"].append({
                    "reason_code": "ACCOUNT_SNAPSHOT_UNREADABLE",
                    "detail": f"account snapshot could not be opened: {type(exc).__name__}: {exc}",
                })
            if snap_bytes is not None:
                actual_snap = "sha256:" + hashlib.sha256(snap_bytes).hexdigest()
                account_binding["actual_snapshot_sha256"] = actual_snap
                if (str(actual_snap).split(":", 1)[-1]
                        != str(snap_sha).split(":", 1)[-1]):
                    account_binding["rejections"].append({
                        "reason_code": "ACCOUNT_SNAPSHOT_SHA_MISMATCH",
                        "detail": f"account snapshot sha256 {actual_snap} != pinned {snap_sha}",
                    })
                else:
                    # sha matched: decode the SAME bytes already in memory.
                    try:
                        snap_v = json.loads(snap_bytes.decode("utf-8"))
                        if not isinstance(snap_v, dict):
                            snap_v = None
                            account_binding["rejections"].append({
                                "reason_code": "ACCOUNT_SNAPSHOT_MALFORMED",
                                "detail": "verified snapshot bytes did not decode to a JSON object",
                            })
                    except Exception as exc:  # noqa: BLE001
                        account_binding["rejections"].append({
                            "reason_code": "ACCOUNT_SNAPSHOT_MALFORMED",
                            "detail": f"verified snapshot bytes failed to parse: {type(exc).__name__}: {exc}",
                        })
        elif snap_path and not snap_sha:
            # A bound path with no declared sha cannot be proven - refuse.
            account_binding["rejections"].append({
                "reason_code": "ACCOUNT_SNAPSHOT_NO_DECLARED_SHA",
                "detail": "account snapshot path is bound but no pinned sha256 is declared",
            })
        # Use the verified decoded snapshot (snap_v) when a path was bound;
        # otherwise fall back to the in-memory mapping (engineering path).
        src = snap_v if snap_v is not None else snap
        account_binding["scenario"] = src.get("scenario")
        account_binding["account_mode"] = src.get("account_mode")
        account_binding["environment"] = src.get("environment")
        account_binding["as_of"] = src.get("as_of")
        account_binding["source_databases"] = src.get("source_databases")
        account_binding["nested_account"] = src.get("account")
        account_binding["account_id"] = src.get("account_id")
        gr = src.get("generation_receipt") or {}
        account_binding["generation_receipt_sha256"] = gr.get("sha256")
        # Generation receipt actually verified (audit): the receipt is a
        # SEPARATE file - its path comes from the bound caller input while
        # the expected sha comes from the VERIFIED snapshot bytes.  Its
        # bytes must hash to that pinned sha, else a named refusal.
        caller_gr = (snap.get("generation_receipt") or {})
        # The expected sha comes from the VERIFIED snapshot bytes; the
        # receipt is a SEPARATE file whose absolute path the caller names.
        # A relative path inside the snapshot resolves against the
        # caller's declared path first (the verified sha governs what the
        # bytes must be), then against the snapshot's own directory.
        gr_path = None
        snap_gr_rel = gr.get("path")
        if snap_gr_rel and os.path.isabs(snap_gr_rel):
            gr_path = snap_gr_rel
        elif caller_gr.get("path") and os.path.isabs(caller_gr["path"]):
            gr_path = caller_gr["path"]
        elif snap_gr_rel:
            gr_path = os.path.join(
                os.path.dirname(str(snap_path)), snap_gr_rel)
        else:
            gr_path = caller_gr.get("path")
        if gr_path and gr.get("sha256"):
            try:
                actual_gr = sha256_file(gr_path)
            except Exception as exc:  # noqa: BLE001
                actual_gr = None
                account_binding["rejections"].append({
                    "reason_code": "ACCOUNT_RECEIPT_UNREADABLE",
                    "detail": f"generation receipt could not be opened: {type(exc).__name__}: {exc}",
                })
            def _hex(v):
                return str(v or "").split(":", 1)[-1].strip()
            if actual_gr is not None and _hex(actual_gr) != _hex(gr.get("sha256")):
                account_binding["rejections"].append({
                    "reason_code": "ACCOUNT_RECEIPT_SHA_MISMATCH",
                    "detail": f"generation receipt sha256 {actual_gr} != pinned {gr.get('sha256')}",
                })
            if actual_gr is not None:
                account_binding["generation_receipt_sha256"] = actual_gr
        # A different account swapped in -> named rejection.
        if (expected_account_id is not None
                and src.get("account_id") != expected_account_id):
            account_binding["rejections"].append({
                "reason_code": "ACCOUNT_ID_MISMATCH",
                "detail": (
                    f"bound account_id {src.get('account_id')} != expected "
                    f"{expected_account_id} for this read"
                ),
            })
        # A future-dated snapshot -> named rejection.
        as_of_ts = parse_ts(src.get("as_of"))
        if as_of_ts is not None and as_of_ts > decision_cutoff:
            account_binding["rejections"].append({
                "reason_code": "ACCOUNT_SNAPSHOT_FUTURE",
                "detail": (
                    f"snapshot as_of {src.get('as_of')} post-dates the "
                    f"decision cutoff {iso(decision_cutoff)}"
                ),
            })
        # security/listing/venue three-key conflict between the bound
        # snapshot's security_identity and the effective identity -> named
        # rejection.  The snapshot's declared identity must agree with the
        # security_id its holdings/pending actually carry.
        s_ident = src.get("security_identity") or {}
        snap_sids = {
            str(h.get("security_id") or h.get("security_code") or "")
            for h in src.get("holdings") or ()
        } | {
            str(p.get("security_id") or p.get("security_code") or "")
            for p in src.get("pending") or ()
        }
        snap_sids.discard("")
        declared_sid = str(s_ident.get("security_id") or "")
        if (declared_sid and snap_sids
                and snap_sids != {declared_sid}
                and len(snap_sids & ({declared_sid} if declared_sid else set())) == 0):
            account_binding["rejections"].append({
                "reason_code": "ACCOUNT_IDENTITY_KEY_CONFLICT",
                "detail": (
                    f"snapshot security_identity security_id {declared_sid} "
                    f"does not match the held/pending security_ids "
                    f"{sorted(snap_sids)}"
                ),
            })
        # Bind-completion path identity (audit R4 seg-B binding-completion):
        # the FINAL path.stat happens only now - AFTER the single in-memory
        # bytes were hashed and decoded, the account/scenario/as_of fields
        # were projected, the generation receipt was re-hashed and the
        # three-key identity was checked, but BEFORE the account facts are
        # committed.  A path replaced or rewritten in ANY of those binding
        # stages (decode, receipt verification, identity checks) is a named
        # ACCOUNT_SNAPSHOT_PATH_CHANGED refusal here.  The three identity
        # signatures (pre-read fstat, post-read fstat, bind-completion
        # path.stat) and the real read/hash evidence are recorded on the
        # binding receipt, not just held in locals.
        if snap_path and snap_sha and snap_bytes is not None:
            _final_sig = None
            try:
                _final_sig = _stat_sig(os.stat(snap_path))
            except Exception as exc:  # noqa: BLE001
                account_binding["rejections"].append({
                    "reason_code": "ACCOUNT_SNAPSHOT_PATH_CHANGED",
                    "detail": (
                        "account snapshot path disappeared inside the "
                        f"binding window: {type(exc).__name__}: {exc}"),
                })
            _fd_diff = [k for k in _STAT_KEYS
                        if _pre_sig is not None
                        and _post_sig is not None
                        and _pre_sig[k] != _post_sig[k]]
            _path_diff = [k for k in _STAT_KEYS
                          if _pre_sig is not None
                          and _final_sig is not None
                          and _pre_sig[k] != _final_sig[k]]
            # Persist the three identity signatures + read/hash evidence on
            # the binding receipt so the refusal is auditable, not local-only.
            account_binding["path_identity"] = {
                "stat_fields": list(_STAT_KEYS),
                "pre_read_fstat": _pre_sig,
                "post_read_fstat": _post_sig,
                "bind_completion_path_stat": _final_sig,
                "fd_drift_fields": _fd_diff,
                "path_drift_fields": _path_diff,
                "read_bytes_sha256": account_binding.get(
                    "actual_snapshot_sha256"),
                "declared_sha256": snap_sha,
            }
            if _fd_diff or _path_diff:
                account_binding["rejections"].append({
                    "reason_code": "ACCOUNT_SNAPSHOT_PATH_CHANGED",
                    "detail": (
                        "account snapshot path changed inside the "
                        "binding window (fd-drift fields "
                        f"{_fd_diff or '[]'}, path-drift fields "
                        f"{_path_diff or '[]'}); verified bytes are the "
                        "first read only"),
                })
        # Use the snapshot's own holdings/pending for the projection when
        # it supplies them; a rejected snapshot contributes NO account
        # facts (the named rejection stands instead).
        if not account_binding["rejections"]:
            holdings = tuple(src.get("holdings") or ())
            pending = tuple(src.get("pending") or ())
        else:
            holdings = ()
            pending = ()
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
    # Formal shared-dependency coverage (audit R4 seg-A closeout pass 2):
    # judge a needed session's price supply by whether a QUALIFIED row
    # actually survives for the security that needs it - not by whether a
    # declared member exists.  A session whose only source is channel-
    # unknown / late / hash-bad and leaves no qualified bar is a gap; a
    # session covered by an independent qualified backup is supplied.  The
    # gap is isolated to the securities whose session actually lacks a
    # qualified row (a single-security gap stays local) while a session
    # whose qualified source covers the whole needed pool blocks the day.
    _sec_session_has_rows = {}   # (security_id, session_iso) -> had source rows
    _sec_session_qualified = {}  # (security_id, session_iso) -> qualified bar survived
    _member_session_sids = {}    # (member_key, session_iso) -> securities it supplies
    # Row-level post-admission coverage (audit R4 seg-A final-qualified-
    # coverage closeout): the shared-scope decision must see a session whose
    # rows all FAILED row PIT/channel gates even though the member itself
    # READ and was channel-admitted.  Record, per (member_key, session_iso),
    # the securities whose rows that member physically surfaced and then had
    # rejected, with the actual rejection reasons, so the coverage matrix can
    # name the member that left a needed session without a qualified bar.
    _member_session_row_gaps = {}  # (member_key, session_iso) -> {"securities":set,"reasons":set,"rows":[]}
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
        formal_ineligible_sessions = []
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
            def _row_eligible(b):
                kf = parse_ts(b.get("knowledge_from"))
                # Null row-knowledge is REJECTED under a formal read -
                # it cannot be proven known by the cutoff.
                if formal and kf is None:
                    return False
                if kf is not None and kf > decision_cutoff:
                    return False
                kt = parse_ts(b.get("knowledge_to"))
                if kt is not None and kt <= decision_cutoff:
                    return False
                if formal and b.get("member_key") in formal_inadmissible_keys:
                    return False
                return True
            eligible = [b for b in ordered if _row_eligible(b)]
            # Formal read (audit R4 second segment): when NO revision is
            # eligible - every row is late, withdrawn or channel-ineligible
            # - the reader emits NO selected bar for this session.  The
            # rows stay on the auditable revision/rejection chain verbatim
            # and the affected dependency is named; a production read must
            # never fall back to an ineligible row merely because it is the
            # physical last one.  Under a non-formal read the last-known
            # row is still exposed for the H1 shape.
            if not eligible and formal:
                chosen = None
                # Rows exist but none is eligible - the session's price
                # supply failed PIT/channel admission.  Name the actual
                # rejected clocks so the named block carries real instants.
                formal_ineligible_sessions.append({
                    "session_date": ordered[-1]["session_date"],
                    "rejected_rows": [
                        {
                            "source_revision": b.get("source_revision"),
                            "knowledge_from": b.get("knowledge_from"),
                            "knowledge_to": b.get("knowledge_to"),
                            "member_key": b.get("member_key"),
                        }
                        for b in ordered
                    ],
                })
            else:
                # Same-clock equivalent replicas collapse to one before
                # conflict detection (audit R4 segment A closeout): among
                # eligible rows sharing the LATEST knowledge clock, rows
                # carrying the same value are one business fact - fold
                # them to the newest source_revision, keeping the original
                # row sources.  Two different values at the SAME latest
                # clock are a genuine ambiguity: record it per security
                # and session for BOTH physical orders, emit NO chosen
                # bar - never pick by physical order.
                chosen = None
                if eligible:
                    latest_kf = max(
                        parse_ts(b.get("knowledge_from"))
                        for b in eligible)
                    same_clock = [
                        b for b in eligible
                        if parse_ts(b.get("knowledge_from")) == latest_kf]
                    # Equivalent replicas = the SAME consumed bar fact at the
                    # same clock, not merely the same close.  Two rows sharing
                    # close but differing on open/high/volume are a genuine
                    # conflict - never picked by physical order (audit R4
                    # seg-A closeout second pass).
                    def _bar_fact(b, _keys=BAR_FACT_KEYS):
                        return tuple(b.get(k) for k in _keys)
                    facts = {_bar_fact(b) for b in same_clock}
                    if len(facts) <= 1:
                        # Collapse equivalent replicas: keep the newest
                        # source_revision at the same clock.
                        chosen = same_clock[-1]
                    else:
                        chosen = None
                        formal_ineligible_sessions.append({
                            "session_date": ordered[-1]["session_date"],
                            "ambiguity": "same_knowledge_conflicting_values",
                            "conflicting_values": sorted(
                                str(b.get("close_price"))
                                for b in same_clock),
                            "conflicting_facts": [
                                {k: b.get(k) for k in (
                                    "open_price", "high_price", "low_price",
                                    "close_price", "volume", "amount")}
                                for b in same_clock],
                            "rejected_rows": [
                                {
                                    "source_revision": b.get("source_revision"),
                                    "knowledge_from": b.get("knowledge_from"),
                                    "close_price": b.get("close_price"),
                                    "member_key": b.get("member_key"),
                                }
                                for b in same_clock
                            ],
                        })
                elif not formal and ordered:
                    chosen = ordered[-1]
            if chosen is not None:
                chosen["late_for_cutoff"] = bool(
                    parse_ts(chosen.get("knowledge_from"))
                    and parse_ts(chosen.get("knowledge_from")) > decision_cutoff
                )
                sec_bars.append(chosen)
            # Coverage bookkeeping for the shared-scope decision: a session
            # that produced a chosen bar is supplied for THIS security; a
            # session whose whole revision set was rejected is a gap for it.
            _sess_iso = ordered[-1].get("session_date")
            _sec_session_has_rows[(ident.security_id, _sess_iso)] = True
            # Which securities each member physically supplies on the
            # session - a member covering ONE security scopes its gap to
            # that security; a member covering the whole pool scopes it to
            # the session (audit R4 segment A closeout: a single-security
            # gap stays local, a pool-wide supply failure is shared).
            for _b in ordered:
                _mk = _b.get("member_key")
                if _mk:
                    _member_session_sids.setdefault(
                        (_mk, _sess_iso), set()).add(ident.security_id)
            # A qualified bar for this session is one the formal read could
            # actually select - its member is channel/knowledge-admissible,
            # not merely present.  A bar carried only by an inadmissible
            # member does NOT count as qualified supply for the session.
            _sec_session_qualified[(ident.security_id, _sess_iso)] = (
                chosen is not None
                and chosen.get("member_key") not in formal_inadmissible_keys)
            # Final qualified coverage (audit R4 seg-A final-qualified-
            # coverage closeout): when THIS security's session ended with NO
            # selected bar under a formal read, record every member that
            # physically surfaced a row for the session together with the
            # actual row-rejection reasons, so the coverage matrix can bind
            # the gap to the member that left the session starved - the
            # member READ and was channel-admitted, so it never reached the
            # physical/channel failure ledger on its own.
            if formal and chosen is None:
                for _b in ordered:
                    _mk = _b.get("member_key")
                    if not _mk:
                        continue
                    _rg = _member_session_row_gaps.setdefault(
                        (_mk, _sess_iso),
                        {"securities": set(), "reasons": set(), "rows": []})
                    _rg["securities"].add(ident.security_id)
                    _bkf = parse_ts(_b.get("knowledge_from"))
                    _bkt = parse_ts(_b.get("knowledge_to"))
                    if _mk in formal_inadmissible_keys:
                        _rg["reasons"].add("CHANNEL_INADMISSIBLE")
                    elif _bkf is None:
                        _rg["reasons"].add("NULL_KNOWLEDGE_CLOCK")
                    elif _bkf > decision_cutoff:
                        _rg["reasons"].add("LATE_FOR_CUTOFF")
                    elif _bkt is not None and _bkt <= decision_cutoff:
                        _rg["reasons"].add("WITHDRAWN_BEFORE_CUTOFF")
                    else:
                        # Eligible individually but lost the same-clock
                        # ambiguity - the conflicting revision kept it out.
                        _rg["reasons"].add("SAME_KNOWLEDGE_CONFLICT")
                    _rg["rows"].append({
                        "security_id": ident.security_id,
                        "source_revision": _b.get("source_revision"),
                        "knowledge_from": _b.get("knowledge_from"),
                        "knowledge_to": _b.get("knowledge_to"),
                    })
            # The revision/rejection chain keeps EVERY physical row for
            # the session (late, withdrawn, channel-ineligible), flagged -
            # even when no row was selected for this formal read.
            if len(ordered) > 1 or (formal and not eligible):
                chain_date = (chosen or ordered[-1])["session_date"]
                bar_revision_chain[chain_date] = [
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
        # P1 factor-PIT exceptions for this security (audit 749-05): match
        # the bare stock_code and the venue's exchange, keep a row only
        # when its effective_date lands inside this security's actual bar
        # window - the 10-08 window's single hit (301583, 2026-07-10) is a
        # named block, never collapsed to UNKNOWN or hard-coded.
        bare = _bare(ident.stock_code)
        exch_map = {"SSE": "SH", "SZSE": "SZ"}
        want_exch = exch_map.get(ident.venue_id)
        for pe in p1_by_code.get(bare) or []:
            if want_exch and pe.get("exchange") and pe["exchange"] != want_exch:
                continue
            ed = _date_iso(pe.get("effective_date"))
            if ed is None or ed.isoformat() in bar_dates:
                sec_exceptions.append(pe)
        # Per-security dependency reasons (audit 749-04): name exactly why a
        # fact is missing instead of leaving an unexplained null - the P11
        # adapter distinguishes "no source rows" from "rows present but
        # empty", never a silent gap.
        dep_reasons = {}
        if not sec_bars:
            # Formal read (audit R4 second segment): distinguish "no source
            # rows" from "rows present but every revision ineligible" - the
            # latter keeps the actual rejected clocks on the dependency so
            # the named block is auditable, not a generic empty.
            if formal_ineligible_sessions:
                dep_reasons["bars"] = "no_qualified_formal_revision"
                dep_reasons["bar_rejections"] = formal_ineligible_sessions
            else:
                dep_reasons["bars"] = "no_qualified_1d_rows"
        elif formal_ineligible_sessions:
            # A formal read with SOME eligible sessions still names each
            # session whose whole revision set was ineligible - the
            # affected dependency (that session's price) is named with the
            # real rejected clocks, never silently dropped.
            dep_reasons["bar_rejections"] = formal_ineligible_sessions
        # P1 factor-PIT ledger missing or failed sha (audit R4 item 2a):
        # actions that consume the factor evidence must see a named
        # block, never a silently-empty exception list on a security
        # whose corporate-action evaluation depends on it.
        if p1_unverified and sec_actions:
            dep_reasons["p1_factor_ledger"] = "unverified_or_missing"
            dep_reasons["factor_dependent_actions"] = "blocked"
            # Do not project P1 exceptions from an unverified ledger -
            # the empty list would read as "no exception", a fabricated
            # negative.  Keep the real block marker in dep_reasons.
            sec_exceptions = [
                e for e in sec_exceptions
                if e.get("ledger") != "P1_factor_pit_exception"
            ]
        if not any(k[0] == ident.listing_id for k in tradability_by_key):
            dep_reasons["tradability_state"] = "no_tradability_state_rows"
        if not sec_limits:
            dep_reasons["price_limit_regime"] = "no_price_limit_regime_rows"
        if not any(k[0] == ident.listing_id for k in exc_by_listing):
            dep_reasons["quality_exceptions"] = "no_exception_rows"
        # Account facts for this security (audit 749-04 / P11 segment-B
        # contract): held / typed-pending facts travel as SIBLING
        # top-level fields on securities[] - held_membership,
        # held_quantity, held_unavailable_reason, pending_kind,
        # pending_quantity and the per-record pending_records list.  The
        # nested "account" block keeps the raw snapshot detail verbatim
        # for forensics; the rule consumes the flat fields so a real
        # account issues a hold_alert / qualified exit and never
        # collapses back into empty-account semantics.
        hold = holdings_by_sid.get(ident.security_id)
        pends = pending_by_sid.get(ident.security_id) or []
        # Each in-flight order keeps its own order key, kind, remaining
        # quantity and source receipt - never summed, never collapsed to
        # a last record.  An unsupported kind (e.g. a sell order whose
        # enum is sell/5) keeps its original kind string and direction.
        pending_records = [
            {
                "order_id": p.get("order_id") or p.get("receipt_id"),
                "pending_kind": p.get("pending_kind") or p.get("kind"),
                "pending_quantity": p.get("pending_quantity") or p.get("quantity"),
                "receipt_id": p.get("receipt_id"),
                "source": p.get("source"),
            }
            for p in pends
        ]
        held_qty = hold.get("quantity") if hold else None
        held_unavailable_reason = None
        if hold is not None and held_qty is None:
            # Quantity unknown: keep the original null and name why the
            # held fact cannot be projected - never fabricate a number.
            held_unavailable_reason = "held_quantity_unavailable"
        if hold is not None and hold.get("history_unavailable"):
            # Missing held history: a named gap kept verbatim - the rule
            # keeps the held review (hold_alert) rather than a silent pass.
            held_unavailable_reason = (
                (held_unavailable_reason + ";" if held_unavailable_reason else "")
                + "held_history_unavailable")
        account = {
            "account_role": (
                "held" if hold is not None else ("pending" if pends else "candidate")
            ),
            "held_quantity": held_qty,
            "pending": pending_records,
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
                held_membership=hold is not None,
                held_quantity=held_qty,
                held_unavailable_reason=held_unavailable_reason,
                pending_kind=(
                    pending_records[0]["pending_kind"]
                    if len(pending_records) == 1 else (
                        "multiple" if pending_records else None
                    )
                ),
                pending_quantity=(
                    pending_records[0]["pending_quantity"]
                    if len(pending_records) == 1 else None
                ),
                pending_records=pending_records,
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
        (set(holdings_by_sid) | set(pending_by_sid)) - pool_sids
    )
    # Three-key alignment (audit 749 R4 segment B): the bound snapshot's
    # declared security_identity is verified against the EFFECTIVE pool
    # identity actually selected - a snapshot whose declared
    # security_id / listing_id / venue_id disagrees with the resolved
    # pool member is a named rejection, never silently accepted.
    if account_snapshot is not None and not account_binding["rejections"]:
        s_ident = account_snapshot.get("security_identity") or {}
        want_sid = str(s_ident.get("security_id") or "")
        want_lid = str(s_ident.get("listing_id") or "")
        want_ven = str(s_ident.get("venue_id") or "")
        if want_sid:
            pool_ident = next(
                (i for i in identities if i.security_id == want_sid), None)
            if pool_ident is not None:
                mism = []
                if want_lid and pool_ident.listing_id != want_lid:
                    mism.append(f"listing_id {pool_ident.listing_id} != {want_lid}")
                if want_ven and pool_ident.venue_id != want_ven:
                    mism.append(f"venue_id {pool_ident.venue_id} != {want_ven}")
                account_binding["identity_alignment"] = {
                    "security_id": want_sid,
                    "in_pool": True,
                    "aligned": not mism,
                    "conflicts": mism,
                }
                if mism:
                    account_binding["rejections"].append({
                        "reason_code": "ACCOUNT_IDENTITY_KEY_CONFLICT",
                        "detail": "; ".join(mism),
                    })
                    holdings = ()
                    pending = ()
                    holdings_by_sid = {}
                    pending_by_sid = {}
                    extra_account_sids = []
            else:
                account_binding["identity_alignment"] = {
                    "security_id": want_sid,
                    "in_pool": False,
                    "aligned": False,
                    "conflicts": ["security_id not in resolved pool"],
                }
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
        # Per-record in-flight orders keep their original order key, kind,
        # quantity and source receipt verbatim - a sell/5 order keeps its
        # own direction and enum, never rewritten to a supported buy.
        op_pending_records = [
            {
                "order_id": p.get("order_id") or p.get("receipt_id"),
                "pending_kind": p.get("pending_kind") or p.get("kind"),
                "pending_quantity": p.get("pending_quantity") or p.get("quantity"),
                "receipt_id": p.get("receipt_id"),
                "source": p.get("source"),
            }
            for p in pends
        ]
        op_held_qty = hold.get("quantity") if hold else None
        op_held_unavailable = None
        if hold is not None and op_held_qty is None:
            op_held_unavailable = "held_quantity_unavailable"
        if hold is not None and hold.get("history_unavailable"):
            op_held_unavailable = (
                (op_held_unavailable + ";" if op_held_unavailable else "")
                + "held_history_unavailable")
        securities.append(
            env_mod.security_entry(
                pseudo, [], [], [], [], {}, {},
                bar_revisions={},
                # P11 fixed contract (audit 749 R4 segment B): an
                # out-of-pool held/pending member carries its account facts
                # on a NAMED risk branch - "held_risk" for a holding with
                # no listing anchor, "pending_risk" for an in-flight order
                # - never a fabricated listing_id (kept empty/UNKNOWN).
                dependency_reasons={
                    "identity": "out_of_pool_account_member",
                    "account_branch": (
                        "held_risk" if hold is not None else "pending_risk"
                    ),
                },
                account={
                    "account_role": ("held" if hold is not None else "pending"),
                    "held_quantity": op_held_qty,
                    "pending": op_pending_records,
                },
                held_membership=hold is not None,
                held_quantity=op_held_qty,
                held_unavailable_reason=op_held_unavailable,
                pending_kind=(
                    op_pending_records[0]["pending_kind"]
                    if len(op_pending_records) == 1 else (
                        "multiple" if op_pending_records else None
                    )
                ),
                pending_quantity=(
                    op_pending_records[0]["pending_quantity"]
                    if len(op_pending_records) == 1 else None
                ),
                pending_records=op_pending_records,
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
    _collect(p1_exc_cov, "reads")

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
        "p1_exceptions": p1_exc_cov,
        "security_count": len(securities),
        "account_binding": account_binding,
        # P11 segment-B contract row 4: account members that have only an
        # account key and no valid listing identity are kept in a
        # VERSIONED unresolved-detail list - each record carries the raw
        # account key, role, quantity, the named missing reason and its
        # source, never a fabricated listing_id or a rewritten enum.
        "unresolved_account_details": [
            {
                "security_id": s["identity"]["security_id"],
                "account_role": s["account"]["account_role"],
                "held_quantity": s.get("held_quantity"),
                "held_unavailable_reason": s.get("held_unavailable_reason"),
                "pending_records": s.get("pending_records") or [],
                "missing_reason": s["dependency_reasons"]["identity"],
                "account_branch": s["dependency_reasons"]["account_branch"],
                "source": "account_snapshot",
            }
            for s in securities
            if (s.get("dependency_reasons") or {}).get("identity")
            == "out_of_pool_account_member"
        ],
        "bar_sessions": BAR_SESSIONS,
        "listing_proof_sessions": LISTING_PROOF_SESSIONS,
        "late_visible_members": late_visible_members,
        "named_input_gaps": [
            {"member_key": e["package_key"], "member_id": e["member_id"],
             "package_id": e["package_id"], "gap": e["named_input_gap"],
             "dependency_type": e.get("dependency_type"),
             "shared_hard_dependency": bool(e.get("shared_hard_dependency"))}
            for e in source_member_entries if e.get("named_input_gap")
        ] if (purpose == FORMAL_DAILY_UPDATE) else [],
        "member_consumption": member_consumption,
        "formal_not_read_dependency_set": (
            # P10 ruling (audit 749 R4 second segment): the registered-but-
            # not-read members for THIS T under a formal update, each
            # carrying its P10 dependency_type so the named-gap
            # dispositions bind to the actual selected/excluded dependency
            # set.  Only members carrying a named input gap count - the
            # sealed-October base and other-day partitions are out of
            # scope.  Only THIS T's package members (date_from == the
            # market_date) - the other-day formal gaps are not in scope.
            [
                {
                    "member_key": e["package_key"],
                    "member_id": e["member_id"],
                    "package_id": e["package_id"],
                    "dependency_type": e.get("dependency_type"),
                    "gap": e.get("named_input_gap"),
                }
                for e in source_member_entries
                if e.get("named_input_gap") and e["package_key"] in not_read
                and e.get("date_from") == market_date.isoformat()
            ]
        ) if (purpose == FORMAL_DAILY_UPDATE) else [],
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
    # Formal update (audit R4 second segment): the four external frozen
    # links (Base manifest, DATES index, Standard descriptor, Core
    # manifest) must each be present, readable and sha-matched BEFORE
    # any member is opened.  A missing path (NO_PATH), a missing declared
    # sha (MISSING_DECLARED_SHA256), an unreadable link (UNREADABLE) or a
    # wrong sha (SHA_MISMATCH) is a named whole-day fault - never a MATCH.
    chain_faults = [
        c for c in manifest_chain
        if c.get("status") in ("NO_PATH", "MISSING_DECLARED_SHA256",
                               "NON_OBJECT_DESCRIPTOR", "UNREADABLE",
                               "SHA_MISMATCH", "CONTENT_NON_OBJECT",
                               "CONTENT_MALFORMED_JSON",
                               "CONTENT_MISSING_IDENTITY_FIELD",
                               "ASSOCIATED_IDENTITY_MISMATCH",
                               "CONTENT_IDENTITY_VALUE_INVALID",
                               "CHAIN_ASSOCIATION_MISMATCH")
    ]
    chain_faults = [
        c for c in chain_faults
        if _chain_fault_in_window(c, sessions_needed, market_date)
    ]
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
    # Formal-update shared-hard-dependency gap (audit R4 second
    # segment): a member with NO proven channel/knowledge clock that is
    # a shared hard dependency (venue calendar, shared listing identity,
    # a needed price source, the fixed summary chain) blocks the whole
    # day for new entries.  A single-security or single-action gap is
    # isolated to its own dependency so a qualified neighbour keeps
    # working - those are named on coverage.named_input_gaps, not here.
    if day_block is None and purpose == FORMAL_DAILY_UPDATE:
        # Judge a needed session's shared price supply by whether a
        # QUALIFIED bar actually survived for the security that needs it,
        # not by whether a declared member exists (audit R4 seg-A closeout
        # pass 2).  A session covered by an independent qualified backup is
        # supplied even when another source for it is channel-unknown or
        # late; a single-security gap stays local; a session whose only
        # supply leaves some securities without a qualified bar blocks the
        # day only for the securities it actually starves.
        # Unified gap ledger (audit R4 seg-A unified closeout): EVERY
        # admission failure folds into one per-member ledger - a declared
        # channel/knowledge gap (named_input_gap), a formal channel-
        # admission rejection (late_for_cutoff / rejected_no_channel_proof)
        # AND a member's own physical read/verify failure (sha mismatch,
        # unreadable, corrupt parquet) are all recorded on the same entry
        # before the coverage matrix is judged.  A physical read failure is
        # a supply gap too: the member's rows never reach the candidate
        # set, so a session whose ONLY registered supplier failed its sha
        # is starved exactly like a channel-unknown one.
        _member_read_failures = {}
        for _sec_reads in (bar_cov, cal_cov, list_cov, uni_cov):
            for _r in (_sec_reads or {}).get("reads") or []:
                _st = _r.get("status")
                if _st in ("SHA_MISMATCH", "UNREADABLE") and _r.get("member_key"):
                    _member_read_failures[_r["member_key"]] = _st
        needed_day_set = {
            (d.isoformat() if hasattr(d, "isoformat") else str(d))
            for d in sessions_needed}
        for e in source_member_entries:
            _mk = e.get("package_key") or e.get("member_key")
            if _mk not in _member_read_failures or e.get("shared_hard_dependency"):
                continue
            _df = e.get("date_from")
            _df_iso = (_df.isoformat() if hasattr(_df, "isoformat") else str(_df))
            # Only a shared T / prior-session PRICE member whose own day is
            # one of THIS read's needed sessions can starve the pool.  The
            # dependency class is derived from the member id (the same rule
            # the registration loop applies) because a channel-admitted yet
            # physically-failed member never earned a stored dependency_type.
            # A failed member pinned for an out-of-window day is kept on the
            # named-gap receipt but must not block this T.
            _mid = str(e.get("member_id") or "")
            if _df_iso not in needed_day_set:
                continue
            if ("dwd_security_bar" in _mid
                    and "dwd_security_valuation" not in _mid):
                e["dependency_type"] = "SHARED_T_PRICE"
                e["shared_hard_dependency"] = True
                e["gap_session_day"] = _df
                e.setdefault("named_input_gap",
                             "MEMBER_" + _member_read_failures[_mk])
        _gap_members = [
            e for e in source_member_entries
            if e.get("shared_hard_dependency")
            and (e.get("named_input_gap")
                 or e.get("formal_admission") in ("late_for_cutoff",
                                                 "rejected_no_channel_proof")
                 or (e.get("package_key") or e.get("member_key"))
                 in _member_read_failures)
        ]
        # ONE judgement: the post-admission qualified-coverage matrix -
        # needed sessions x active-pool securities.  After every admitted
        # source (qualified backups included) ran the full gate chain, a
        # session is starved for exactly the securities that ended with NO
        # qualified bar.  A whole-day block fires ONLY when the session has
        # NO common coverage left (every pool security starved) - the cause
        # (channel UNKNOWN, proven late, member sha mismatch, unreadable) is
        # kept on the ledger, not used to widen the radius.  A session that
        # kept ANY qualified bar stays READ with the genuinely starved
        # securities isolated to their own gap (a partial backup never
        # balloons into whole_day, a shared pool-wide collapse is never
        # mistaken for an isolated gap).
        _pool_sids = {ident.security_id for ident in identities}
        _starved_set = set()
        _failed_days = []
        _shared_failed = []          # members whose gap is pool-wide
        _local_gaps = []             # (member, [starved security ids])
        # Final qualified coverage (audit R4 seg-A final-qualified-coverage
        # closeout): a member that READ and was channel-admitted still fails
        # the session's real supply when EVERY row it surfaced lost its row
        # PIT gate - null knowledge, a late source clock, a withdrawal before
        # the cutoff, or the latest same-clock conflict.  Those members never
        # reached _gap_members because they carry no named_input_gap and no
        # physical read failure.  Fold each (member, session) whose rows left
        # pool securities starved into the SAME unified ledger as a supply
        # gap, keyed on the session it actually serves - the radius judgement
        # below then decides shared vs isolated exactly as a channel gap.
        _gap_member_keys = {
            (e.get("package_key") or e.get("member_key"),
             (e.get("gap_session_day").isoformat()
              if hasattr(e.get("gap_session_day"), "isoformat")
              else str(e.get("gap_session_day"))))
            for e in _gap_members
        }
        _entries_by_key = {
            (e.get("package_key") or e.get("member_key")): e
            for e in source_member_entries
        }
        for (_mk, _sess_iso), _rg in sorted(_member_session_row_gaps.items()):
            if _sess_iso not in needed_day_set:
                continue
            if not _rg["securities"]:
                continue
            _src = _entries_by_key.get(_mk)
            if _src is None:
                continue
            # Dependency class comes from the member id (the same rule the
            # registration loop applies): an admitted member never earned a
            # stored dependency_type, so the field is absent here.
            _mid_l = str(_src.get("member_id") or "")
            if not ("dwd_security_bar" in _mid_l
                    and "dwd_security_valuation" not in _mid_l):
                continue
            if (_mk, _sess_iso) in _gap_member_keys:
                continue
            _inj = dict(_src)
            _inj["dependency_type"] = "SHARED_T_PRICE"
            _inj["shared_hard_dependency"] = True
            _inj["gap_session_day"] = _sess_iso
            _inj["named_input_gap"] = "NO_QUALIFIED_FORMAL_REVISION"
            _inj["row_rejection_reasons"] = sorted(_rg["reasons"])
            _inj["row_rejected_rows"] = _rg["rows"]
            _gap_members.append(_inj)
        for e in _gap_members:
            _gday = e.get("gap_session_day")
            _gday_iso = (_gday.isoformat()
                         if hasattr(_gday, "isoformat") else str(_gday))
            if e.get("dependency_type") != "SHARED_T_PRICE":
                # A non-price shared hard dependency (the T session/calendar,
                # the fixed summary chain) has no qualified-backup concept -
                # a channel-unknown or late member on a needed session is an
                # unconditional whole-day block, T and prior alike.
                _shared_failed.append(e)
                _failed_days.append(_gday)
                _starved_set.update(
                    ident.security_id for ident in identities)
                continue
            # Post-admission coverage on this needed session: the securities
            # that ended with NO qualified bar are the session's real gap.
            starved_here = sorted(
                sid for sid in _pool_sids
                if not _sec_session_qualified.get((sid, _gday_iso)))
            if not starved_here:
                # A qualified backup (an independent same-session member or
                # a qualified base aggregate row the formal read could
                # select) covered the whole pool for this session - the
                # member's gap left nothing starved, no block.
                continue
            # The failed member's own supply radius: the securities it
            # physically surfaced rows for on this session.  A member that
            # only ever supplies ONE security can never be a shared pool
            # supply - its gap stays isolated to that security even on a
            # one-security pool.  A member with NO readable rows (sha-bad,
            # unreadable, or every row rejected before surfacing) has an
            # UNPROVEN declared radius - it is treated as the session's
            # shared pool supply, never narrowed to a single local gap.
            _mk = e.get("package_key") or e.get("member_key")
            _declared_sids = _member_session_sids.get((_mk, _gday_iso)) or set()
            _single_security_supply = (
                len(_declared_sids) == 1 and len(starved_here) == 1)
            if len(starved_here) == len(_pool_sids) and not _single_security_supply:
                # The session's merged admitted sources left the WHOLE pool
                # without a qualified bar - a shared pool-wide collapse,
                # whatever the member's failure cause.
                _shared_failed.append(e)
                _failed_days.append(_gday)
                _starved_set.update(starved_here)
            else:
                # Some pool securities kept a qualified bar (a qualified
                # backup covered part of the pool) - isolate the gap to the
                # securities that actually starved; never widen a partial
                # coverage into a whole-day block, and never drop a real
                # starved set back to READ.
                _local_gaps.append((e, starved_here))
                _starved_set.update(starved_here)
        _starved = sorted(_starved_set)
        if _shared_failed and _failed_days:
            day_block = {
                "reason_code": "SHARED_INPUT_GAP",
                "detail": (
                    "a shared hard dependency left needed sessions without "
                    "a qualified source for this formal read"
                ),
                "affected_scope": "whole_day",
                "failed_members": [e.get("package_key") or e.get("member_key")
                                   for e in _shared_failed],
                "affected_securities": _starved,
                "disposition": "block_new_entries",
            }
        elif _local_gaps:
            # A local single-security gap is NOT a whole-day block: record
            # the isolated dependency so the consumer blocks only the
            # affected security's new entries while a qualified neighbour
            # (and an independently-held security) keeps working.
            coverage["isolated_input_gaps"] = [
                {"member_key": e.get("package_key") or e.get("member_key"),
                 "member_id": e.get("member_id"),
                 "session_date": e.get("gap_session_day"),
                 "gap": e.get("named_input_gap") or "LATE_FOR_CUTOFF",
                 "affected_securities": sids,
                 "disposition": "block_new_entries_for_security"}
                for e, sids in _local_gaps
            ]

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
        # Only members that can actually project into THIS T's window
        # count as declared price supply (audit R4 second pass): a member
        # is window-relevant when its observation month intersects the
        # needed sessions (Base) or its market_date is a needed session
        # (Daily).  Members pinned only for OTHER windows are not supply
        # for this T - a map that declares only out-of-window members
        # still has no usable price input and must block.
        sessions_set = set(sessions_sorted)
        months_needed = {(s.year, s.month) for s in sessions_sorted}
        def _bar_member_in_window(m):
            if "interval_size=1d" not in m.member_id:
                return False
            if m.market_date is not None:
                return _date_iso(m.market_date) in sessions_set
            ym = re.search(
                r"observation_start_year=(\d{4})/observation_start_month=(\d+)",
                m.member_id,
            )
            if ym:
                return (int(ym.group(1)), int(ym.group(2))) in months_needed
            # No decodable window key: keep it as a conservative declared
            # member rather than silently dropping a possible supplier.
            return True
        bar_members = [
            m for m in smap.base_members("dwd_security_bar")
            if _bar_member_in_window(m)
        ]
        bar_members += [
            m for d in smap.daily_open_days()
            for m in ([smap.daily_member(d, "dwd_security_bar")] or [])
            if m is not None and _bar_member_in_window(m)
        ]
        # No bar member declared at all: the pool has no price input -
        # a named shared-input block, distinct from a real zero-signal day
        # whose members READ and simply carry no rows (audit R4 item 3).
        if not bar_members:
            day_block = {
                "reason_code": "SHARED_PRICE_UNAVAILABLE",
                "detail": "no 1d bar member is declared in the pinned map",
                "affected_scope": "whole_day",
                "failed_members": [],
                "disposition": "block_new_entries",
            }
        elif bar_reads and all(
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
