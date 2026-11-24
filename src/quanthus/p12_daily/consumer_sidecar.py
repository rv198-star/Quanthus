'''Read and check a P10 daily consumer sidecar record (spec v1, task #726).

The sidecar is P10's append-only record of one publish/revise/withdraw event:
it binds the immutable descriptor and manifest, the consumed member closure and
the source/validated/publication/available clocks for each member.  This module
is P12's read port only - it never rewrites the record, never substitutes file
mtime, built_at or the current read-back time for a missing clock, and never
turns a REVISE or WITHDRAW event into a same-day replacement advice package
(frozen decision b: same-day advice is never replaced; an error only appends a
retraction and the next executable advice is the next trading day's package).

For every hard member of a PUBLISH/REVISE record the clock bounds must satisfy
source_available_at <= validated_at <= published_at <= available_at <= cutoff.
A member missing a clock, an evidence cover, or out-of-order bounds leaves the
run blocked for that use rather than defaulted.  P12 appends its own
received_at / first_read_at / verified_at and the seen digest per consumed
member; the sidecar itself does not sign those consumer stamps.
'''

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.shared.fact_contracts import FactError

EXCHANGE_TZ = timezone(timedelta(hours=8))

SIDECAR_SCHEMA = "quanthus.p10.daily-consumer-sidecar.v1"

DISPOSITION_TEST_ONLY = "TEST_ONLY"
DISPOSITION_INPUT_OR_CLOCK_BLOCKED = "INPUT_OR_CLOCK_BLOCKED"
DISPOSITION_CLOCKS_COMPLETE = "CLOCKS_COMPLETE_FOR_CONSUMER_REVIEW"

HARD_EVENTS_REQUIRING_CLOCK_ORDER = ("PUBLISH", "REVISE")


class SidecarError(FactError):
    def __init__(self, detail: str, member: str | None = None) -> None:
        self.member = member
        super().__init__("INPUT_OR_CLOCK_BLOCKED", detail)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _clock_bounds(node: Any) -> tuple[datetime | None, datetime | None] | None:
    '''Return (start, end) of a typed clock object, or None if absent.'''
    if node is None:
        return None
    if not isinstance(node, dict):
        raise SidecarError("clock entry is not an object")
    return (_parse_ts(node.get("start")), _parse_ts(node.get("end")))


def _required(node: dict, key: str, where: str) -> Any:
    value = node.get(key)
    if value is None:
        raise SidecarError(f"{where} missing required field {key!r}")
    return value


@dataclass(frozen=True)
class MemberReceipt:
    '''One consumed member plus the consumer stamps P12 appends.'''

    member_id: str
    required: bool
    state: str
    sha256: str
    source_version: str | None
    source_available_at: tuple[str | None, str | None]
    validated_at: tuple[str | None, str | None]
    available_at: tuple[str | None, str | None]
    unavailable_reason: str | None
    # P12's own stamps on what it actually read; null with a reason when the
    # clock is unknown, never a substituted file mtime.
    received_at: str | None
    first_read_at: str | None
    verified_at: str | None
    clock_note: str | None
    # Audit R3 (r2): the member's sha256 is RECOMPUTED by P12 over the bytes
    # it read - never echoed from the sidecar's declaration.  null when no
    # bytes could be located to recompute.
    p12_recomputed_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        def bounds(pair):
            return {"start": pair[0], "end": pair[1]}

        return {
            "member_id": self.member_id,
            "required": self.required,
            "state": self.state,
            "sha256": self.sha256,
            "source_version": self.source_version,
            "source_available_at": bounds(self.source_available_at),
            "validated_at": bounds(self.validated_at),
            "available_at": bounds(self.available_at),
            "unavailable_reason": self.unavailable_reason,
            "received_at": self.received_at,
            "first_read_at": self.first_read_at,
            "verified_at": self.verified_at,
            "p12_recomputed_sha256": self.p12_recomputed_sha256,
            "clock_note": self.clock_note,
        }


@dataclass(frozen=True)
class SidecarRead:
    '''The checked sidecar plus P12's per-member consumer stamps.'''

    schema: str
    event: str
    market_date: str
    delivery_id: str
    consumer_id: str
    evidence_grade: str
    disposition: str
    record_id: str
    published_at: tuple[str | None, str | None]
    members: tuple[MemberReceipt, ...]
    blocked_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event": self.event,
            "market_date": self.market_date,
            "delivery_id": self.delivery_id,
            "consumer_id": self.consumer_id,
            "evidence_grade": self.evidence_grade,
            "disposition": self.disposition,
            "record_id": self.record_id,
            "published_at": {
                "start": self.published_at[0],
                "end": self.published_at[1],
            },
            "members": [m.to_dict() for m in self.members],
            "blocked_reasons": list(self.blocked_reasons),
        }


def _record_identity(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "record_id"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_sidecar(
    path: Path,
    *,
    consumer_now: datetime,
    decision_cutoff: datetime | None = None,
    member_files_root: Path | None = None,
) -> SidecarRead:
    '''Load one sidecar record and check the consumed member clocks.

    consumer_now is the moment P12 opens the record.  Each member's
    received_at / first_read_at / verified_at is stamped from the actual
    moment P12 observes that member (audit R3), never one shared stamp
    copied into all three fields.  When decision_cutoff is supplied, a hard
    member whose available_at end exceeds it blocks the run.
    '''

    path = Path(path)
    if not path.is_file():
        raise SidecarError("sidecar record missing: " + str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SidecarError("sidecar record unreadable: " + str(exc)) from exc
    if not isinstance(payload, dict):
        raise SidecarError("sidecar record is not a JSON object")
    if payload.get("schema") != SIDECAR_SCHEMA:
        raise SidecarError(
            "sidecar schema {!r} is not {!r}".format(payload.get("schema"), SIDECAR_SCHEMA)
        )
    event = str(_required(payload, "event", "sidecar"))
    if event not in ("PUBLISH", "REVISE", "WITHDRAW"):
        raise SidecarError(f"sidecar event {event!r} is not a named event")
    binding = _required(payload, "binding", "sidecar")
    if not isinstance(binding, dict):
        raise SidecarError("sidecar binding is not an object")
    evidence_grade = str(payload.get("evidence_grade") or "")
    published = _clock_bounds((payload.get("publication") or {}).get("published_at"))
    blocked: list[str] = []

    members: list[MemberReceipt] = []
    for index, raw in enumerate(payload.get("members") or []):
        if not isinstance(raw, dict):
            raise SidecarError(f"member[{index:d}] is not an object")
        mid = str(raw.get("id") or f"member[{index:d}]")
        required_member = bool(raw.get("required"))
        state = str(raw.get("state") or "UNKNOWN")
        src_av = _clock_bounds(raw.get("source_available_at"))
        val = _clock_bounds(raw.get("validated_at"))
        av = _clock_bounds(raw.get("available_at"))
        member_block: list[str] = []
        clock_notes: list[str] = []

        # Audit R3 (r2): bind each stamp to its real event.  received_at =
        # the moment P12 first located the member's bytes; first_read_at =
        # the moment the bytes were actually read; verified_at = the moment
        # after P12 recomputed the member sha itself.  The declared sha is a
        # claim to verify, never copied into the stamp.  When the member's
        # file cannot be located under member_files_root the stamps stay
        # null with the reason in clock_note.
        received_at = None
        first_read_at = None
        verified_at = None
        recomputed_sha = None
        member_rel = raw.get("path")
        member_file = None
        if member_rel and member_files_root is not None:
            # Try the path as declared, then stripped of leading "fixtures/"
            # segments (the fixture tree nests the member files one level
            # deeper than the sidecar's relative paths).
            candidates = []
            p = Path(member_rel)
            candidates.append(p)
            parts = list(p.parts)
            while parts and parts[0] in ("fixtures", "synthetic"):
                parts = parts[1:]
                candidates.append(Path(*parts) if parts else p)
            for cand_rel in candidates:
                candidate = (Path(member_files_root) / cand_rel).resolve()
                try:
                    candidate.relative_to(Path(member_files_root).resolve())
                except ValueError:
                    continue
                if candidate.is_file():
                    member_file = candidate
                    break
        if member_file is not None:
            received_at = datetime.now(EXCHANGE_TZ).isoformat()
            try:
                h = hashlib.sha256()
                with member_file.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                first_read_at = datetime.now(EXCHANGE_TZ).isoformat()
                recomputed_sha = "sha256:" + h.hexdigest()
                verified_at = datetime.now(EXCHANGE_TZ).isoformat()
            except OSError:
                member_block.append(mid + " bytes unreadable; consumer stamps null")
        else:
            # Audit R3 (r2): no bytes could be located - stamps stay null
            # with the reason on the note; never a fabricated time.  An
            # unlocatable file is an evidence NOTE, not a blocking fact -
            # the sidecar's own clock order already gates the run above.
            clock_notes.append(
                mid + " member file not located; consumer stamps null"
            )
        # Audit R3 (r2): the member's declared sha is a CLAIM to verify
        # against P12's own recomputation; a mismatch on a located file is
        # a block, never a copy.
        # Audit R3 (r2): the member's declared sha is a CLAIM to verify
        # against P12's own recomputation; a mismatch on a located file is
        # a block, never a copy.
        if recomputed_sha is not None and str(raw.get("sha256") or "") != recomputed_sha:
            member_block.append(
                mid + " declared sha256 differs from P12's recomputed digest"
            )

        if required_member and event in HARD_EVENTS_REQUIRING_CLOCK_ORDER:
            chain = [
                ("source_available_at", src_av),
                ("validated_at", val),
                ("available_at", av),
            ]
            for name, bounds in chain:
                if bounds is None or bounds[1] is None:
                    member_block.append(mid + "." + name + " clock unknown")
            if published is None or published[1] is None:
                member_block.append(mid + ".published_at clock unknown")
            ends = [b[1] for b in (src_av, val, av) if b and b[1]]
            starts = [b[0] for b in (src_av, val, av) if b and b[0]]
            if len(ends) == 3 and not (ends[0] <= ends[1] <= ends[2]):
                member_block.append(
                    mid + " clock bounds out of order "
                    "(source<=validated<=available violated)"
                )
            if (
                published
                and published[1] is not None
                and av
                and av[1] is not None
                and not published[1] <= av[1]
            ):
                member_block.append(mid + " available_at precedes published_at")
            if (
                decision_cutoff is not None
                and av
                and av[1] is not None
                and av[1] > decision_cutoff
            ):
                member_block.append(mid + " available_at end exceeds decision_cutoff")
            elif (
                starts
                and starts[-1] is not None
                and decision_cutoff is not None
                and starts[-1] > decision_cutoff
            ):
                member_block.append(mid + " available_at start exceeds decision_cutoff")
        if state != "PRESENT" and required_member:
            member_block.append(mid + " state " + repr(state) + " is not PRESENT")
        if required_member and raw.get("unavailable_reason"):
            member_block.append(mid + " unavailable: " + str(raw["unavailable_reason"]))
        blocked.extend(member_block)
        members.append(
            MemberReceipt(
                member_id=mid,
                required=required_member,
                state=state,
                sha256=str(raw.get("sha256") or ""),
                source_version=raw.get("source_version"),
                source_available_at=(
                    None if src_av is None else (src_av[0].isoformat() if src_av[0] else None),
                    None if src_av is None else (src_av[1].isoformat() if src_av[1] else None),
                ),
                validated_at=(
                    None if val is None else (val[0].isoformat() if val[0] else None),
                    None if val is None else (val[1].isoformat() if val[1] else None),
                ),
                available_at=(
                    None if av is None else (av[0].isoformat() if av[0] else None),
                    None if av is None else (av[1].isoformat() if av[1] else None),
                ),
                unavailable_reason=raw.get("unavailable_reason"),
                received_at=received_at,
                first_read_at=first_read_at,
                verified_at=verified_at,
                p12_recomputed_sha256=recomputed_sha,
                clock_note="; ".join(member_block + clock_notes) or None,
            )
        )

    if blocked:
        disposition = DISPOSITION_INPUT_OR_CLOCK_BLOCKED
    elif evidence_grade == "SYNTHETIC":
        disposition = DISPOSITION_TEST_ONLY
    else:
        disposition = DISPOSITION_CLOCKS_COMPLETE
    return SidecarRead(
        schema=SIDECAR_SCHEMA,
        event=event,
        market_date=str(binding.get("market_date") or ""),
        delivery_id=str(binding.get("delivery_id") or ""),
        consumer_id=str(binding.get("consumer_id") or ""),
        evidence_grade=evidence_grade,
        disposition=disposition,
        record_id=str(payload.get("record_id") or _record_identity(payload)),
        published_at=(
            None if published is None else (published[0].isoformat() if published[0] else None),
            None if published is None else (published[1].isoformat() if published[1] else None),
        ),
        members=tuple(members),
        blocked_reasons=tuple(blocked),
    )
