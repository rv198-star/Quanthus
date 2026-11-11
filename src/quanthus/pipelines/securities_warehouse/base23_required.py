"""Scoped daily qualification and source-bound share-unit successor observations.

These projections grant neither consumer admission nor legal lifecycle/PIT proof.
The caller owns the complete physical raw replay; cold verification repeats these
projections from image-local members. Matching dates alone never aliases events.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base23-required-daily-and-share-units-v1"
SHARE_FIELDS = {
    "BONUS_SHARES_PER_SHARE": "bonus_shares",
    "TRANSFER_SHARES_PER_SHARE": "transfer_shares",
}
VERSION = (
    "corporate_action_id",
    "effective_at",
    "knowledge_from",
    "knowledge_to",
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "announced_at",
)


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BoundedError("REQUIRED_EXACT_SHARE_NUMBER_INVALID")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BoundedError("REQUIRED_EXACT_SHARE_NUMBER_INVALID") from exc
    if not result.is_finite():
        raise BoundedError("REQUIRED_EXACT_SHARE_NUMBER_INVALID")
    return result


def _effects(terms: Sequence[Mapping[str, Any]]) -> dict[str, Fraction]:
    result: dict[str, Fraction] = {}
    for row in terms:
        typ = row["term_type"]
        if (
            typ in result
            or row.get("option_id") is not None
            or row.get("subscription_price") is not None
        ):
            raise BoundedError("REQUIRED_COMPLETE_PLAIN_TERM_SET_INVALID")
        if typ == "CASH_AMOUNT_PER_SHARE" and row.get("currency") == "CNY":
            value = _number(row["amount_per_share"])
            result[typ] = Fraction(value)
        elif typ in SHARE_FIELDS and row.get("currency") is None:
            numerator, denominator = (
                _number(row["ratio_numerator"]),
                _number(row["ratio_denominator"]),
            )
            if denominator <= 0:
                raise BoundedError("REQUIRED_EXACT_SHARE_NUMBER_INVALID")
            result[typ] = Fraction(numerator) / Fraction(denominator)
        else:
            raise BoundedError("REQUIRED_COMPLETE_PLAIN_TERM_SET_INVALID")
    if not result or len(result) > 3:
        raise BoundedError("REQUIRED_COMPLETE_PLAIN_TERM_SET_INVALID")
    return result


def revise_share_units(
    raw: dict[str, Any],
    before: dict[str, Any],
    before_terms: list[dict[str, Any]],
    donor: dict[str, Any],
    donor_terms: list[dict[str, Any]],
    *,
    vocabulary: dict[str, Any],
    binding: dict[str, Any],
    observed_at: Any,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Correct only a proven per-ten/per-share representation error on the old ID.

    Cash, source acquisition/publication and business coordinates stay untouched.
    The explicit unit contract and complete raw are prerequisites; a factor-ten
    resemblance alone is insufficient. The donor confirms shared effects only;
    its event identity and optional/missing fields are never transplanted.
    """
    old_key = raw.get("action_id")
    sha_fields = (
        "original_manifest_sha256",
        "original_member_sha256",
        "donor_scope_identity",
        "donor_raw_replay_identity",
        "vocabulary_sha256",
    )
    if (
        binding.get("purpose") != CONTRACT_ID
        or binding.get("original_raw_row_sha256") != digest(raw)
        or binding.get("before_row_sha256") != digest(before)
        or binding.get("before_term_rows_sha256") != sorted(digest(t) for t in before_terms)
        or binding.get("donor_row_sha256") != digest(donor)
        or binding.get("donor_term_rows_sha256") != sorted(digest(t) for t in donor_terms)
        or binding.get("vocabulary_identity") != digest(vocabulary)
        or any(
            not isinstance(binding.get(f), str)
            or len(binding[f]) != 71
            or not binding[f].startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in binding[f][7:])
            for f in sha_fields
        )
        or vocabulary.get("share_quantity_unit") != "per_ten_shares"
        or raw.get("source") != "md.dividend_history"
        or not isinstance(old_key, str)
        or not old_key
        or before.get("corporate_action_id")
        != _stable_id("corporate_action", "corporate_actions", old_key)
        or before.get("source_record_id") != old_key
        or before.get("record_source") != "corporate_actions"
        or before.get("source_artifact_sha256") != binding["original_member_sha256"]
        or type(raw.get("ex_date")) is not date
        or raw["ex_date"] > date(2026, 9, 30)
        or before.get("ex_date") != raw["ex_date"]
        or before.get("action_status") != "IMPLEMENTED"
        or donor.get("action_status") != "IMPLEMENTED"
        or any(
            before.get(f) != donor.get(f)
            for f in ("company_id", "security_id", "effective_at", "ex_date")
        )
        or before.get("knowledge_to") is not None
        or donor.get("knowledge_to") is not None
    ):
        raise BoundedError("REQUIRED_SHARE_UNIT_COMPLETE_RAW_BINDING_INVALID")
    clock = _clock(observed_at)
    for parent, terms in ((before, before_terms), (donor, donor_terms)):
        if clock <= _clock(parent["knowledge_from"]) or not terms:
            raise BoundedError("REQUIRED_SHARE_UNIT_NEW_KNOWLEDGE_INVALID")
        if any(any(t.get(f) != parent.get(f) for f in VERSION) for t in terms):
            raise BoundedError("REQUIRED_SHARE_UNIT_TERM_VERSION_NOT_CLOSED")
    old, confirmed = _effects(before_terms), _effects(donor_terms)
    if any(old[t] != confirmed[t] for t in old.keys() & confirmed.keys() - SHARE_FIELDS.keys()):
        raise BoundedError("REQUIRED_SHARE_UNIT_OTHER_EFFECT_CONFLICT")
    changes: dict[str, Decimal] = {}
    with localcontext() as ctx:
        ctx.prec = 80
        for typ, field in SHARE_FIELDS.items():
            if typ not in old:
                continue
            value = _number(raw.get(field))
            target = value / Decimal(10)
            if old[typ] != Fraction(value) or confirmed.get(typ) != Fraction(target):
                raise BoundedError("REQUIRED_SHARE_UNIT_RAW_AND_EXPLICIT_UNIT_NOT_CONFIRMED")
            if value != 0:
                changes[typ] = target
    if not changes:
        raise BoundedError("REQUIRED_SHARE_UNIT_NO_PROVEN_CHANGE")
    revision = CONTRACT_ID + ":" + digest(binding)
    after = {**before, "knowledge_from": clock, "source_revision": revision}
    terms_after = []
    for term in before_terms:
        item = {**term, "knowledge_from": clock, "source_revision": revision}
        if term["term_type"] in changes:
            target = changes[term["term_type"]]
            with localcontext() as ctx:
                ctx.prec = 80
                quantum = Decimal(1).scaleb(term["ratio_numerator"].as_tuple().exponent)
                serialized = target.quantize(quantum)
                if serialized != target:
                    raise BoundedError("REQUIRED_SHARE_UNIT_SCHEMA_CANNOT_PRESERVE_EXACT_VALUE")
                denominator = Decimal(1).quantize(
                    Decimal(1).scaleb(term["ratio_denominator"].as_tuple().exponent)
                )
            item.update(ratio_numerator=serialized, ratio_denominator=denominator)
        terms_after.append(item)
    result = {
        "dwd_corporate_action": [{**before, "knowledge_to": clock}, after],
        "dwd_corporate_action_term": [{**t, "knowledge_to": clock} for t in before_terms]
        + terms_after,
    }
    proof = {
        "contract_id": CONTRACT_ID,
        "binding_identity": digest(binding),
        "old_action_sha256": digest(before),
        "old_terms_sha256": sorted(digest(t) for t in before_terms),
        "output_sha256": digest(result),
        "changed_term_types": sorted(changes),
        "knowledge_observed_at": clock,
        "historical_PIT_admission": False,
        "normalization_original_recovered": False,
        "source_priority_granted": False,
        "donor_event_id_adopted": False,
        "cash_or_business_coordinates_changed": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return result, proof


def qualify_daily_observation(
    row: Mapping[str, Any],
    listing: Mapping[str, Any],
    calendar: Mapping[str, Any],
    intervals: Sequence[Mapping[str, Any]],
    *,
    observed_at: Any,
) -> dict[str, Any]:
    """Dated source qualification, never resume-date or legal lifecycle inference.

    A conflicting old suspension blocks new entry for that observation. It does
    not force a sale or rewrite the provider's tradability flag or old interval.
    """
    start, clock = _clock(row["observation_start"]), _clock(row["knowledge_from"])
    now = _clock(observed_at)
    day = start.astimezone(ZoneInfo("Asia/Shanghai")).date()
    calendar_day = calendar["trade_date"]
    if isinstance(calendar_day, str):
        calendar_day = date.fromisoformat(calendar_day)
    if (
        not date(2026, 8, 1) <= day <= date(2026, 9, 30)
        or now < clock
        or row["listing_id"] != listing["listing_id"]
        or row["calendar_session_id"] != calendar["calendar_session_id"]
        or calendar_day != day
        or listing["venue_id"] != calendar["venue_id"]
        or start < _clock(listing["effective_from"])
        or listing.get("effective_to") is not None
        and start >= _clock(listing["effective_to"])
        or clock < _clock(listing["knowledge_from"])
        or listing.get("knowledge_to") is not None
        and clock >= _clock(listing["knowledge_to"])
        or type(row["is_tradable"]) is not bool
        or row["is_tradable"]
        and calendar["is_open"] is not True
    ):
        raise BoundedError("REQUIRED_DAILY_LISTING_DATE_VENUE_OR_CLOCK_INVALID")
    conflicts = []
    for prior in intervals:
        if prior["listing_id"] != listing["listing_id"]:
            raise BoundedError("REQUIRED_DAILY_INTERVAL_PARENT_INVALID")
        relevant = (
            _clock(prior["effective_from"]) <= start
            and (prior.get("effective_to") is None or start < _clock(prior["effective_to"]))
            and _clock(prior["knowledge_from"]) <= clock
            and (prior.get("knowledge_to") is None or clock < _clock(prior["knowledge_to"]))
        )
        if (
            relevant
            and prior["status_type"] == "SUSPENSION"
            and prior["status_value"] == "SUSPENDED"
            and row["is_tradable"]
        ):
            conflicts.append(digest(prior))
    result = {
        "contract_id": CONTRACT_ID,
        "listing_id": listing["listing_id"],
        "security_id": listing["security_id"],
        "venue_id": listing["venue_id"],
        "trade_date": str(day),
        "source_observation_sha256": digest(dict(row)),
        "source_knowledge_from": row["knowledge_from"],
        "observed_at": now,
        "disposition": "CONFLICT_BLOCKS_NEW_ENTRY"
        if conflicts
        else "DATED_PROVIDER_OBSERVATION_CONSISTENT",
        "prior_conflict_sha256": sorted(conflicts),
        "force_sale": False,
        "provider_flag_changed": False,
        "legal_lifecycle_qualified": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
