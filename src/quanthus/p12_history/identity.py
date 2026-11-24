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


def build_universe(source_map: SourceMap, market_date, holdings, pending, verify, decision_cutoff=None):
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
        ["security_id", "identifier_namespace", "identifier_value", "venue_id"],
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
    # Primary coded identifier (the 6-digit exchange code the bar source keys on).
    code_by_security = {}
    for i in id_rows:
        sid = str(i.get("security_id") or "")
        ns = str(i.get("identifier_namespace") or "")
        val = str(i.get("identifier_value") or "")
        if not sid or not val:
            continue
        code_by_security.setdefault(sid, val)

    securities = []
    seen = set()
    dup_ids = set()
    cutoff_ts = decision_cutoff
    mkt_ts = market_date
    for s in sec_rows:
        sid = str(s.get("security_id") or "")
        itype = str(s.get("instrument_type") or "")
        if not sid or itype != COMMON_EQUITY:
            continue
        cid = str(s.get("company_id") or "")
        # Pick the listing row effective at T: the half-open
        # [effective_from, effective_to) interval must cover the market
        # date and be known by the cutoff.  A future listing and an
        # already-retired boundary stay off the candidate pool (audit
        # 749-03); the rule still sees every raw listing_row.
        for lst in listing_by_security.get(sid, ()):
            mic = venue_by_id.get(str(lst.get("venue_id") or ""), "")
            venue = "SSE" if mic == "XSHG" else ("SZSE" if mic == "XSHE" else str(lst.get("venue_id") or ""))
            if venue not in ADMITTED_VENUES:
                continue
            eff_from = parse_ts(lst.get("effective_from"))
            eff_to = parse_ts(lst.get("effective_to"))
            if mkt_ts is not None:
                if eff_from is not None and eff_from.date() > mkt_ts:
                    continue  # future listing
                if eff_to is not None and eff_to.date() <= mkt_ts:
                    continue  # already retired at the open boundary
            if cutoff_ts is not None:
                kf = parse_ts(lst.get("knowledge_from"))
                if kf is not None and kf > cutoff_ts:
                    continue  # not yet known at the decision cutoff
            pair = (sid, str(lst.get("listing_id") or ""))
            if pair in seen:
                continue
            if sid in seen:
                # Distinct listing row for an already-emitted security:
                # collapse to one identity (one security_id) and record the
                # duplicate so the coverage names it instead of silently
                # emitting a second securities[] entry.
                dup_ids.add(sid)
                continue
            code = code_by_security.get(sid) or str(lst.get("listing_code") or sid)
            securities.append(
                SecurityIdentity(
                    security_id=sid,
                    stock_code=code,
                    listing_code=str(lst.get("listing_code") or code),
                    security_name=company_name.get(cid) or UNKNOWN,
                    industry_id=company_industry.get(cid) or UNKNOWN,
                    venue_id=venue,
                    instrument_type=itype,
                    listing_id=str(lst.get("listing_id") or ""),
                    company_id=cid,
                    depends_on_member_ids=tuple(member_ids),
                    invalidations=(),
                    price_bound=None,
                    source_evidence_digest="sha256:" + digest,
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
    }
