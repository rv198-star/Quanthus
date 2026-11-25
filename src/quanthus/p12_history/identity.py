"""Security identity + universe domain for the bounded reader (issue #749).

The scan domain is the bound strategy pool: SSE/SZSE common_equity,
resolved from the pinned Base reference members (dwd_security +
dwd_security_identifier + dwd_listing + dwd_trading_venue +
dwd_listed_company + dwd_company_industry_membership + dwd_industry_node).
Account holdings and typed pending securities are merged in on top even
when they fall outside the new-candidate pool.

The reader never computes a trend/price/volume screen.  Fields whose
qualified source is absent or unbound (a security display name, a price
bound, an industry id) are carried as UNKNOWN markers rather than
defaulted - the P11 adapter decides whether that security drops.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from quanthus.p12_history.clocks import parse_ts
from quanthus.p12_history.calendar_listing import _date, _rows
from quanthus.p12_history.source_pins import SourceMap

UNKNOWN = "UNKNOWN"
ADMITTED_VENUES = {"SSE", "SZSE"}
ADMITTED_MIC = {"XSHG", "XSHE"}
COMMON_EQUITY = "common_equity"


@dataclass(frozen=True)
class SecurityIdentity:
    security_id: str
    stock_code: str
    listing_code: str
    security_name: str
    industry_id: str
    venue_id: str
    instrument_type: str
    listing_id: str
    company_id: str
    depends_on_member_ids: tuple
    invalidations: tuple
    price_bound: Decimal | None
    source_evidence_digest: str
    # True when the qualifying listing row is in force at T but its archive
    # knowledge_from post-dates the decision cutoff (October as-observed
    # use): the security stays a candidate and the rule reads the late flag.
    late_visible: bool = False


def _read_table(source_map, table, columns):
    rows = []
    reads = []
    for member in source_map.base_members(table):
        verify = source_map.verify_member(member)
        if not verify["matches"]:
            reads.append({
                "member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH",
                "declared_sha256": verify["declared_sha256"],
                "actual_sha256": verify["actual_sha256"], "verified_at": verify.get("verified_at"),
            })
            continue
        try:
            recs = _rows(member.abs_path, columns)
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        rows.extend(recs)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(recs), "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    return rows, reads


def _in_open_interval(row, open_instant, market_date):
    """Half-open [effective_from, effective_to) test at the venue's real
    open instant (audit R4 fourth pass).  Both the listing row and the
    trading-identifier row must describe the SAME instant so a code that
    only becomes effective after the open is never adopted early.
    Falls back to the session date only when the open instant is null."""
    eff_from = parse_ts(row.get("effective_from"))
    eff_to = parse_ts(row.get("effective_to"))
    if open_instant is not None:
        if eff_from is not None and eff_from > open_instant:
            return False
        if eff_to is not None and eff_to <= open_instant:
            return False
        return True
    if eff_from is not None and eff_from.date() > market_date:
        return False
    if eff_to is not None and eff_to.date() <= market_date:
        return False
    return True


def _pick_unique_version(items, fact_key, cutoff_ts, row_of=lambda x: x):
    """Select the unique qualified latest knowledge version, then collapse
    equivalent replicas of the SAME business fact before judging a
    conflict (audit R4 fourth pass).

    Steps:
      1. keep items known by the decision cutoff (knowledge_from <= cutoff);
      2. find the max knowledge_from among them - the latest version;
      3. collapse items at that latest version into distinct business
         facts; identical facts (same fact_key fields) are equivalent
         replicas and fold to one;
      4. if exactly one distinct fact survives -> unique winner;
         several distinct facts at the same latest clock -> conflict.

    ``row_of`` extracts the source row from each item (a plain row is its
    own source; a qualifying tuple keeps the row at index 0).  Returns
    (winner_item, conflict, known_items): the chosen original item keeps
    its source record; conflict True means no unique named winner."""
    def kf(it):
        return parse_ts(row_of(it).get("knowledge_from"))

    known = [it for it in items if cutoff_ts is None
             or kf(it) is None or kf(it) <= cutoff_ts]
    if not known:
        return None, False, []
    kfs = [kf(it) for it in known if kf(it) is not None]
    best = max(kfs) if kfs else None
    latest = [it for it in known
              if (kf(it) == best or (best is None and kf(it) is None))]
    # Collapse equivalent replicas of the same business fact at the latest
    # version; keep one original item per distinct fact (source preserved).
    distinct = {}
    for it in latest:
        distinct.setdefault(fact_key(it), it)
    if len(distinct) == 1:
        return next(iter(distinct.values())), False, known
    return None, True, known


def build_universe(source_map: SourceMap, market_date, holdings, pending, verify, decision_cutoff=None, open_at_by_venue=None, late_archive_as_observed=False):
    """Scan-domain identities: SSE/SZSE common_equity + account members.

    Returns (securities_identity_list, coverage_report).  Every identity
    field whose qualified source is absent is left UNKNOWN, never
    defaulted; the coverage report names each source actually read.
    """
    sec_rows, r1 = _read_table(
        source_map, "dwd_security",
        ["security_id", "company_id", "instrument_type", "effective_from", "effective_to"],
    )
    id_rows, r2 = _read_table(
        source_map, "dwd_security_identifier",
        ["security_id", "identifier_namespace", "identifier_value", "venue_id",
         "effective_from", "effective_to", "knowledge_from"],
    )
    list_rows, r3 = _read_table(
        source_map, "dwd_listing",
        ["security_id", "listing_id", "venue_id", "listing_code", "board_code", "effective_from", "effective_to", "knowledge_from"],
    )
    venue_rows, r4 = _read_table(
        source_map, "dwd_trading_venue",
        ["venue_id", "mic_code"],
    )
    company_rows, r5 = _read_table(
        source_map, "dwd_listed_company",
        ["company_id", "display_name"],
    )
    industry_rows, r6 = _read_table(
        source_map, "dwd_company_industry_membership",
        ["company_id", "industry_node_id", "membership_type", "effective_from", "effective_to", "knowledge_from"],
    )
    reads = r1 + r2 + r3 + r4 + r5 + r6

    # Member ids each identity depends on (its evidence chain).
    member_ids = sorted({
        m.member_id
        for t in ("dwd_security", "dwd_security_identifier", "dwd_listing",
                  "dwd_trading_venue", "dwd_listed_company",
                  "dwd_company_industry_membership")
        for m in source_map.base_members(t)
    })
    digest = hashlib.sha256("|".join(member_ids).encode()).hexdigest()
    listing_by_security = {}
    for lst in list_rows:
        listing_by_security.setdefault(str(lst.get("security_id") or ""), []).append(lst)
    venue_by_id = {str(v.get("venue_id") or ""): str(v.get("mic_code") or "") for v in venue_rows}
    company_name = {str(c.get("company_id") or ""): str(c.get("display_name") or "") for c in company_rows}
    company_industry = {}
    cutoff_ts = decision_cutoff
    mkt_ts = market_date
    for i in industry_rows:
        cid = str(i.get("company_id") or "")
        eff_from = parse_ts(i.get("effective_from"))
        eff_to = parse_ts(i.get("effective_to"))
        # PIT (audit 749-02/749-03): a membership row is usable for T only
        # when its half-open [effective_from, effective_to) interval covers
        # the market date AND it was known by the decision cutoff.  A
        # future-effective row never replaces the current industry.
        if eff_from is not None and mkt_ts is not None and eff_from.date() > mkt_ts:
            continue
        if eff_to is not None and mkt_ts is not None and eff_to.date() <= mkt_ts:
            continue
        if cutoff_ts is not None:
            kf = parse_ts(i.get("knowledge_from"))
            if kf is not None and kf > cutoff_ts:
                continue
        company_industry[cid] = str(i.get("industry_node_id") or "")
    # Primary trading identifier (the exchange code the bar source keys
    # on).  Audit R4 third pass: resolve each (security, namespace) to the
    # rows effective at the venue's actual open instant AND known by the
    # decision cutoff.  Mutually-exclusive historical code-changes
    # (old code effective_to at a boundary, new code effective_from at
    # the same boundary) leave exactly ONE effective row per namespace -
    # not a conflict.  Two rows effective at the open in the SAME
    # namespace are a real key-fact conflict unless a strictly-later
    # knowledge_from uniquely names the live row; otherwise that
    # namespace is ambiguous.  Distinct namespaces are distinct facts.
    mkt_date = market_date
    code_eff_by_ns = {}
    for i in id_rows:
        sid = str(i.get("security_id") or "")
        ns = str(i.get("identifier_namespace") or "")
        val = str(i.get("identifier_value") or "")
        if not sid or not val:
            continue
        code_eff_by_ns.setdefault((sid, ns), []).append(i)
    # The identifier effective interval is judged against the SAME venue
    # open instant as the listing row (audit R4 fourth pass), so it is
    # resolved per-security inside the loop once the venue is known; the
    # version pick + replica collapse lives in _pick_unique_version.
    # The primary trading code comes from the CN_STOCK_CODE:{venue}
    # namespace, not an arbitrary first row - so #737 and the P1 key stay
    # stable regardless of identifier row order or a non-trading
    # namespace (ISIN / short-name) appearing first.  The venue is known
    # only after the listing resolves, so the code is chosen inside the
    # security loop; a security whose chosen namespace is in unresolved
    # conflict is isolated there.

    securities = []
    seen = set()
    dup_ids = set()
    ambiguous_ids = set()
    cutoff_ts = decision_cutoff
    open_at_by_venue = open_at_by_venue or {}
    for s in sec_rows:
        sid = str(s.get("security_id") or "")
        itype = str(s.get("instrument_type") or "")
        if not sid or itype != COMMON_EQUITY:
            continue
        # A key-fact conflict with no named unique winner isolates the
        # security from the new-candidate pool (audit R4 second pass) -
        # it is still named on the ambiguous list for the rule.
        if sid in ambiguous_ids:
            continue
        cid = str(s.get("company_id") or "")
        # Pick the listing row effective at the VENUE's actual open instant
        # (audit 749-03): the half-open [effective_from, effective_to)
        # interval is tested against the real native open_at for that
        # venue's T session, not the bare calendar date.  A row that opens
        # later than the venue open (e.g. effective_from=02:00Z vs
        # open_at=01:30Z) is not yet listed at the open; a row that ends
        # after the open is still in force.  Rows whose interval cannot be
        # resolved to an instant fall back to the market date.
        qualifying = []
        for lst in listing_by_security.get(sid, ()):
            mic = venue_by_id.get(str(lst.get("venue_id") or ""), "")
            venue = "SSE" if mic == "XSHG" else ("SZSE" if mic == "XSHE" else str(lst.get("venue_id") or ""))
            if venue not in ADMITTED_VENUES:
                continue
            eff_from = parse_ts(lst.get("effective_from"))
            eff_to = parse_ts(lst.get("effective_to"))
            open_instant = open_at_by_venue.get(venue)
            if open_instant is not None:
                # Half-open test against the real open instant.
                if eff_from is not None and eff_from > open_instant:
                    continue  # opens after the venue open: not yet listed
                if eff_to is not None and eff_to <= open_instant:
                    continue  # already ended before/at the open
            else:
                # No native open for this venue/date (e.g. closed or the
                # open instant is null): fall back to the session date.
                if eff_from is not None and eff_from.date() > market_date:
                    continue  # future listing
                if eff_to is not None and eff_to.date() <= market_date:
                    continue  # already retired at the open boundary
            if cutoff_ts is not None:
                kf = parse_ts(lst.get("knowledge_from"))
                if kf is not None and kf > cutoff_ts:
                    # Late archival visibility (audit 749-03): under the
                    # engineering-replay-as-observed purpose an in-force
                    # listing whose archive knowledge_from post-dates the
                    # decision cutoff is NOT deleted - the October use
                    # keeps the original in-force row and marks it
                    # late_visible so the rule judges per-security.  A
                    # formal read still drops it on real availability.
                    if late_archive_as_observed:
                        qualifying.append((lst, venue, eff_from, True))
                        continue
                    continue  # not yet known at the decision cutoff
            qualifying.append((lst, venue, eff_from, False))
        # Resolve to ONE qualifying listing per security ONLY when the
        # source names a unique in-force row (audit 749-03): when several
        # rows cover the open with different effective_from values, the
        # original rows carry no named version or qualification proving
        # one is the live listing - that is a genuine ambiguity, not a
        # "take the later one" decision.  Block the security from new
        # candidates (ambiguous) rather than resolve it by source order or
        # later start.  An identical-pair duplicate still collapses.
        if qualifying:
            distinct_listings = {str(q[0].get("listing_id") or "") for q in qualifying}
            if len(distinct_listings) > 1:
                # Several distinct listing ids cover the open - ambiguous.
                ambiguous_ids.add(sid)
                continue
            # Same listing_id may carry several source versions of the
            # same fact (audit R4 item 3): the row must resolve to ONE
            # version deterministically - the newest version known by
            # the decision cutoff - independent of physical row order.
            # Rows whose intervals genuinely differ name a real
            # conflict and are ambiguous; identical intervals collapse.
            eff_pairs = {
                (str(q[0].get("effective_from") or ""), str(q[0].get("effective_to") or ""))
                for q in qualifying
            }
            if len(eff_pairs) > 1:
                ambiguous_ids.add(sid)
                continue
            known = [q for q in qualifying if not q[3]]
            pool = known if known else qualifying
            # First select the qualified latest knowledge version, then
            # collapse equivalent replicas of the SAME business fact at
            # that version before judging a conflict (audit R4 fourth
            # pass).  An exact duplicate latest row folds into the one
            # real version and never counts as a second conflicting fact;
            # a genuinely different value at the same latest clock still
            # isolates the security by name.  The fact key covers the
            # business fields, so two byte-identical latest originals are
            # one fact while a same-clock board/code difference is not.
            def _lst_fact(q):
                r = q[0]
                return tuple(str(r.get(f) or "") for f in (
                    "listing_id", "listing_code", "board_code",
                    "venue_id", "effective_from", "effective_to"))
            def _lst_kf(q):
                return q[0]
            # Reuse the shared picker over the (lst, ...) tuples: the
            # knowledge version and the equivalence test both read the
            # original row, and the chosen tuple keeps its source record.
            # The pool was already split into pre-cutoff known rows vs
            # late-observed-as-observed rows above, so the version pick
            # must NOT re-filter by the decision cutoff (a late row is a
            # deliberately retained observation).  Pass cutoff=None so
            # the picker selects the latest version within this pool.
            winner_q, lst_conflict, _known_pool = _pick_unique_version(
                pool, _lst_fact, None, row_of=lambda q: q[0])
            if lst_conflict:
                ambiguous_ids.add(sid)
                continue
            chosen_lst, venue, _, chosen_late = winner_q
            # A late-observed row chosen only because no earlier version
            # exists keeps its late_visible mark; a pre-cutoff version
            # always wins over a later re-post of the same fact.
            chosen_late = chosen_late and not known
            pair = (sid, str(chosen_lst.get("listing_id") or ""))
            if pair in seen:
                continue
            if sid in seen:
                dup_ids.add(sid)
                continue
            # Primary trading code: prefer CN_STOCK_CODE:{venue}; fall
            # back to the generic "code" namespace, then listing_code.
            # The identifier effective interval is judged at the SAME
            # venue open instant as the listing row (audit R4 fourth
            # pass): a code that only becomes effective after the open is
            # never adopted early.  Within each namespace the newest
            # qualified knowledge version is chosen first and equivalent
            # replicas of the same business fact collapse before any
            # conflict is declared; same-clock differing values still
            # isolate the security.
            id_open_instant = open_at_by_venue.get(venue)
            cand_nss = ["CN_STOCK_CODE:" + str(venue), "code"]
            code = None
            code_conflict = set()
            for ns_c in cand_nss:
                rows = code_eff_by_ns.get((sid, ns_c), ())
                effective = [r for r in rows
                             if _in_open_interval(r, id_open_instant, market_date)]
                if not effective:
                    continue
                winner, conflict, _known = _pick_unique_version(
                    effective,
                    lambda r: str(r.get("identifier_value") or ""),
                    cutoff_ts,
                )
                if conflict:
                    code_conflict.add(ns_c)
                    continue
                if winner is not None:
                    code = str(winner.get("identifier_value") or "")
                    break
            # If a trading-code namespace exists but ALL of them are in
            # unresolved conflict, the code cannot be proven unique.
            trading_nss = [n for n in cand_nss if (sid, n) in code_eff_by_ns]
            if code is None and trading_nss and all(
                n in code_conflict for n in trading_nss
            ):
                ambiguous_ids.add(sid)
                continue
            code = code or str(chosen_lst.get("listing_code") or sid)
            securities.append(
                SecurityIdentity(
                    security_id=sid,
                    stock_code=code,
                    listing_code=str(chosen_lst.get("listing_code") or code),
                    security_name=company_name.get(cid) or UNKNOWN,
                    industry_id=company_industry.get(cid) or UNKNOWN,
                    venue_id=venue,
                    instrument_type=itype,
                    listing_id=str(chosen_lst.get("listing_id") or ""),
                    company_id=cid,
                    depends_on_member_ids=tuple(member_ids),
                    invalidations=(),
                    price_bound=None,
                    source_evidence_digest="sha256:" + digest,
                    late_visible=bool(chosen_late),
                )
            )
            seen.add(sid)
            seen.add(pair)
    # Account roles (audit 749-04): separate the new-candidate pool from
    # held-inside-pool, held-with-unknown-quantity, and pending-only.
    # Held and pending securities that are NOT in the candidate pool are
    # still named so the rule can evaluate them as independent holdings
    # (hold_alert path) or pending orders - they never silently drop.
    held_sids = set()
    held_unknown_qty = []
    for h in holdings or ():
        sid = str(h.get("security_id") or h.get("security_code") or "")
        if not sid:
            continue
        held_sids.add(sid)
        if h.get("quantity") is None:
            held_unknown_qty.append(sid)
    pending_sids = set()
    pending_typed = []
    for pd in pending or ():
        sid = str(pd.get("security_id") or pd.get("security_code") or "")
        if not sid:
            continue
        pending_sids.add(sid)
        pending_typed.append({
            "security_id": sid,
            "pending_kind": pd.get("pending_kind") or pd.get("kind"),
            "pending_quantity": pd.get("pending_quantity") or pd.get("quantity"),
        })
    # Split account members vs the new-candidate pool by identity.
    account_extra = sorted(
        sid for sid in (held_sids | pending_sids) if sid not in seen
    )
    security_by_listing = {i.listing_id: i.security_id for i in securities if i.listing_id}
    return securities, security_by_listing, {
        "reads": reads,
        "pool_size": len(securities),
        "account_extra_not_in_pool": account_extra,
        "held_in_pool": sorted(held_sids & seen),
        "held_unknown_quantity": sorted(set(held_unknown_qty)),
        "pending_only": sorted(pending_sids - seen),
        "pending_typed": pending_typed,
        "duplicate_security_ids": sorted(dup_ids),
        "ambiguous_security_ids": sorted(ambiguous_ids),
    }
