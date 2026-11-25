"""P11 bounded historical paper replay: raw_history envelopes over the fixed chain.

This module is the isolated engineering replay entry for the first-stage
2026-10 historical simulation use. It does NOT:

* read market rows or the physical Base (P12's reader owns that),
* modify source-declared clocks to satisfy the formal PIT gate,
* evaluate strategy performance, select fills from T+1 high/low, or
* write to the daily/live account ledger.

It DOES:

* strictly validate a versioned replay request (p11.paper-replay-request.v1),
* derive per-T raw_history envelopes carrying the last <=61 open-session bars
  plus the verbatim source-declared member/calendar/listing clocks,
* run the REAL chain (history_input -> daily_runner -> rule_seam ->
  FormalStrategyRunner) once per declared open session T, and
* emit a p11.paper-replay-day.v1 receipt per T carrying the as-observed job
  clock separately from the logical decision coordinate.

Clock boundary (see execution/paper-replay-m0/M0-CALLGRAPH.md): three times are
kept distinct. (a) source-declared instants (member knowledge_at/visible_at,
calendar/listing knowledge_from, per-bar knowledge_cutoff) are verbatim, never
rewritten; (b) the logical decision coordinate is T 21:30 Asia/Shanghai with
next_session_open from the Base calendar; (c) the actual job read/run instant is
recorded only in the replay receipt's replay_observed_at. Output grade is
ENGINEERING_REPLAY_AS_OBSERVED and binds only to the isolated
historical-replay:{scenario_id} ledger namespace - it is never formal H1,
daily, or live evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, NoReturn

from quanthus.event_library.contracts import sha256_digest
from quanthus.forward_validation.base24_runtime import (
    AfterCloseDecisionClock,
    P6Base24CandidatePolicy,
)
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import (
    DailyFactsView,
    MemberEvidence,
    NoQualifiedRawInputs,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)

SHANGHAI = timezone(timedelta(hours=8))
REPLAY_REQUEST_SCHEMA = "p11.paper-replay-request.v1"
REPLAY_DAY_SCHEMA = "p11.paper-replay-day.v1"
INPUT_GRADE = "ENGINEERING_REPLAY_AS_OBSERVED"
LEDGER_NAMESPACE_RULE = "historical-replay:{scenario_id}"
# Fixed logical decision coordinate for replayed days (21:30 Asia/Shanghai).
REPLAY_DECISION_TIME = time(21, 30)
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 0)
BAR_CONTEXT = 61
ALLOWED_PURPOSE_MODE = "historical_replay"
ALLOWED_ACCOUNT_USE = "personal_simulation"
# The bound release's held_reduction_wire.wire_version: the replay serializes
# decision_sets on the registered v2 surface, never the field-dropping v1
# package path.  Kept as the module constant the release contract registers.
RELEASE_WIRE_VERSION = "p12.decision-set.v2"


def _fail(code: str, message: str) -> NoReturn:
    raise ProductDailyError(code, message)


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_replay_request", f"{label} must be an object")
    return dict(value)


def _seq(value: Any, label: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        _fail("invalid_replay_request", f"{label} must be a list")
    return list(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_replay_request", f"{label} must be a non-empty string")
    return str(value)


_ARTIFACT_ID_FIELDS = ("security_id", "listing_id", "venue_id")


def _invalid_artifact_ids(row: Mapping[str, Any], *, required: bool) -> list[str]:
    """Signed P10 rows carry all three ids. A placeholder never identifies a security."""
    invalid = []
    for field in _ARTIFACT_ID_FIELDS:
        if not required and field not in row:
            continue
        value = row.get(field)
        if (
            not isinstance(value, str)
            or value != value.strip()
            or value.casefold() in {"", "none", "null", "nan", "unknown"}
        ):
            invalid.append(field)
    return invalid


def _coverage_records(coverage: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    """Coalesce identical records and reject a key that names two different sources."""
    records: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(_seq(coverage.get(field, ()), f"action_coverage.{field}")):
        record = _obj(raw, f"{field}[{i}]")
        key = _text(record.get("coverage_id"), f"{field}[].coverage_id")
        if key in records and records[key] != record:
            _fail("conflicting_action_coverage_id", f"{field}: conflicting records for {key!r}")
        records[key] = record
    return records


def _isodate(value: Any, label: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    _fail("invalid_replay_request", f"{label} must be an ISO date")


def _isodatetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        out = value
    elif isinstance(value, str):
        try:
            iso = value[:-1] + "+00:00" if value.endswith("Z") else value
            out = datetime.fromisoformat(iso)
        except ValueError:
            _fail("invalid_replay_request", f"{label} must be an ISO datetime")
    else:
        _fail("invalid_replay_request", f"{label} must be an ISO datetime")
    if out.tzinfo is None:
        _fail("invalid_replay_request", f"{label} must be timezone-aware")
    return out


def _sha256(value: Any, label: str) -> str:
    text_value = _text(value, label)
    body = text_value[7:] if text_value.startswith("sha256:") else text_value
    if len(body) != 64 or any(c not in "0123456789abcdef" for c in body.lower()):
        _fail("invalid_replay_request", f"{label} is not a sha256 digest")
    return "sha256:" + body.lower()


# --- typed request -----------------------------------------------------------------


@dataclass(frozen=True)
class ReplayExclusion:
    """#737 exclusion metadata: a security excluded on one session or all days."""

    security_code: str
    session_date: date | None  # None = all replay sessions

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "session_date": (
                self.session_date.isoformat() if self.session_date is not None else None
            ),
        }


@dataclass(frozen=True)
class ReplayRequest:
    """The frozen replay batch declaration. Never self-declares market truth."""

    scenario_id: str
    market_dates: tuple[date, ...]
    open_sessions: tuple[date, ...]
    package_id: str
    manifest_sha256: str
    source_members: tuple[dict[str, Any], ...]
    calendar_rows: tuple[dict[str, Any], ...]
    listing_rows: tuple[dict[str, Any], ...]
    securities: tuple[dict[str, Any], ...]
    exclusions: tuple[ReplayExclusion, ...]
    account: Mapping[str, Any]
    policy: Mapping[str, Any]
    facts_template: Mapping[str, Any]
    required_member_ids: tuple[str, ...]
    member_evidence: tuple[MemberEvidence, ...]
    run_id: str
    final_day_policy: Mapping[str, Any] | None = None
    signal_window: tuple[date, date] | None = None
    action_coverage: Mapping[str, Any] | None = None
    request_digest: str = ""

    @property
    def ledger_namespace(self) -> str:
        return LEDGER_NAMESPACE_RULE.format(scenario_id=self.scenario_id)


def load_replay_request(raw: Mapping[str, Any]) -> ReplayRequest:
    """Strict-load a p11.paper-replay-request.v1 mapping, fail closed."""
    envelope = _obj(raw, "replay_request")
    if envelope.get("schema_version") != REPLAY_REQUEST_SCHEMA:
        _fail(
            "invalid_replay_request",
            f"schema_version must be {REPLAY_REQUEST_SCHEMA}",
        )
    if envelope.get("purpose_mode") != ALLOWED_PURPOSE_MODE:
        _fail("invalid_purpose_mode", "replay request purpose_mode must be historical_replay")
    if envelope.get("permitted_account_use") != ALLOWED_ACCOUNT_USE:
        _fail("invalid_account_use", "permitted_account_use must be personal_simulation")
    scenario_id = _text(envelope.get("scenario_id"), "scenario_id")
    open_sessions = tuple(
        sorted(
            {
                _isodate(d, f"open_sessions[{i}]")
                for i, d in enumerate(_seq(envelope.get("open_sessions"), "open_sessions"))
            }
        )
    )
    if not open_sessions:
        _fail("invalid_replay_request", "open_sessions must be non-empty")
    market_dates = tuple(
        _isodate(d, f"market_dates[{i}]")
        for i, d in enumerate(_seq(envelope.get("market_dates"), "market_dates"))
    )
    if not market_dates:
        _fail("invalid_replay_request", "market_dates must be non-empty")
    open_set = set(open_sessions)
    for md in market_dates:
        if md not in open_set:
            _fail(
                "market_date_not_open",
                f"market_date {md} is not an open session in the bound Base calendar",
            )
    if tuple(sorted(market_dates)) != market_dates:
        _fail("invalid_replay_request", "market_dates must be sorted ascending")
    # A trailing market_date whose sealed calendar has no next session is only
    # admissible when the request explicitly declares final_day_policy for it;
    # it replays T-only (display/intent review) and never receives a fabricated
    # T+1 open quote.
    # signal_window bounds which bound sessions may be replayed as signal days
    # (e.g. October only).  The one exception is the explicitly declared final
    # display day, which replays T-only outside the window; anything else
    # outside the window is rejected, never trimmed.
    signal_window: tuple[date, date] | None = None
    sw = envelope.get("signal_window")
    if sw is not None:
        swo = _obj(sw, "signal_window")
        signal_window = (
            _isodate(swo.get("from"), "signal_window.from"),
            _isodate(swo.get("through"), "signal_window.through"),
        )
        if signal_window[0] > signal_window[1]:
            _fail("invalid_signal_window", "signal_window.from must precede through")

    final_day_policy = envelope.get("final_day_policy")
    fdp_date: date | None = None
    if final_day_policy is not None:
        fdp = _obj(final_day_policy, "final_day_policy")
        fdp_date = _isodate(fdp.get("market_date"), "final_day_policy.market_date")
        if fdp.get("next_session") is not None:
            _fail(
                "invalid_final_day_policy",
                "final_day_policy.next_session must stay null inside the sealed scope",
            )
        if fdp.get("disposition") != "final_display_only":
            _fail(
                "invalid_final_day_policy",
                "final_day_policy.disposition must be final_display_only",
            )
        if fdp_date != open_sessions[-1]:
            _fail(
                "invalid_final_day_policy",
                "final_day_policy.market_date must be the last bound open session",
            )

    if signal_window is not None:
        for md in market_dates:
            if not signal_window[0] <= md <= signal_window[1] and md != fdp_date:
                _fail(
                    "market_date_outside_signal_window",
                    f"market_date {md} is outside the declared signal window "
                    f"{signal_window[0]}..{signal_window[1]}",
                )
        if (
            fdp_date is not None
            and fdp_date in market_dates
            and (signal_window[0] <= fdp_date <= signal_window[1])
        ):
            _fail(
                "invalid_final_day_policy",
                "final_day_policy.market_date is inside the signal window; "
                "it is the final execution/display day, not a signal day",
            )

    if final_day_policy is None:
        for md in market_dates:
            if not any(d > md for d in open_sessions):
                _fail(
                    "missing_next_session",
                    f"no open session follows market_date {md}; declare "
                    "final_day_policy to replay a trailing day T-only",
                )

    def _exclusion(i: int, e: Any) -> ReplayExclusion:
        eo = _obj(e, f"exclusions[{i}]")
        return ReplayExclusion(
            security_code=_text(eo.get("security_code"), "exclusions[].security_code"),
            session_date=(
                _isodate(eo["session_date"], "exclusions[].session_date")
                if eo.get("session_date")
                else None
            ),
        )

    exclusions = tuple(
        _exclusion(i, e) for i, e in enumerate(_seq(envelope.get("exclusions", ()), "exclusions"))
    )

    def _member_evidence(i: int, m: Any) -> MemberEvidence:
        mo = _obj(m, f"member_evidence[{i}]")
        return MemberEvidence(
            member_id=_text(mo.get("member_id"), "member_evidence[].member_id"),
            version=_text(mo.get("version"), "member_evidence[].version"),
            digest=_sha256(mo.get("digest"), "member_evidence[].digest"),
            available_at=_isodatetime(mo.get("available_at"), "member_evidence[].available_at"),
            required=bool(mo.get("required", True)),
        )

    member_evidence = tuple(
        _member_evidence(i, m)
        for i, m in enumerate(_seq(envelope.get("member_evidence"), "member_evidence"))
    )
    required_member_ids = tuple(
        _text(mid, "required_member_ids[]")
        for mid in _seq(envelope.get("required_member_ids"), "required_member_ids")
    )
    supplied_members = {
        _text(_obj(m, f"source_members[{i}]").get("member_id"), "source_members[].member_id")
        for i, m in enumerate(_seq(envelope.get("source_members"), "source_members"))
    }
    missing = set(required_member_ids) - supplied_members
    if missing:
        _fail(
            "required_member_not_supplied",
            f"required members absent from source_members: {sorted(missing)}",
        )

    # Corporate-action coverage evidence, per
    # october-action-feasibility-intake/P11-OCTOBER-AS-OBSERVED-v1.md: an empty
    # actions[] would otherwise become a literal "no adjustment" inside the
    # real chain.  The replay adapter requires the coverage state to be bound
    # to named evidence (per P11-P10-ACTION-REGISTER-EVIDENCE-v1), never a
    # caller-only label: a security x signal-day x purpose record must carry a
    # verified event_state AND a hashed register/member identity.
    action_coverage: dict[str, Any] | None = None
    ac = envelope.get("action_coverage")
    if ac is not None:
        aco = _obj(ac, "action_coverage")
        default_state = _text(aco.get("default_state", "unknown"), "action_coverage.default_state")
        # default_state may no longer be "covered": coverage without named
        # evidence is exactly the empty-credential case the R2 probe caught.
        if default_state not in ("unknown",):
            _fail(
                "invalid_action_coverage",
                "action_coverage.default_state must be unknown; a covered "
                "default would admit unproven securities",
            )

        # Named coverage registers (the official action-directory scopes). Each
        # carries the member bytes identity P12 reads; a register alone is a
        # locator, not proof of a security's window state.
        registers: dict[str, dict[str, Any]] = {}
        for rid, ro in _coverage_records(aco, "registers").items():
            registers[rid] = {
                "coverage_id": rid,
                "version": _text(ro.get("version"), "registers[].version"),
                "sha256": _sha256(ro.get("sha256"), "registers[].sha256"),
                "venue_id": _text(ro.get("venue_id"), "registers[].venue_id"),
                "event_types": [
                    _text(e, "registers[].event_types[]")
                    for e in _seq(ro.get("event_types", ()), "registers[].event_types")
                ],
                "source_member_id": ro.get("source_member_id"),
            }

        # Per-security identity: security_id / listing_id / venue_id / code, so
        # a window record binds the exact identity, not a bare code.
        securities_cov: dict[str, dict[str, Any]] = {}
        for i, s in enumerate(_seq(aco.get("securities", ()), "action_coverage.securities")):
            so = _obj(s, f"action_coverage.securities[{i}]")
            code = _text(so.get("security_code"), "coverage securities[].security_code")
            securities_cov[code] = {
                "security_code": code,
                "security_id": _text(so.get("security_id"), "coverage securities[].security_id"),
                "listing_id": _text(so.get("listing_id"), "coverage securities[].listing_id"),
                "venue_id": _text(so.get("venue_id"), "coverage securities[].venue_id"),
            }

        # window_evidence: security x T x purpose records.  event_state must be
        # verified (verified_none | verified_event); a record that cannot prove
        # its window is unresolved/unknown and does NOT admit the branch.
        VALID_STATES = {"verified_none", "verified_event", "unresolved", "unknown"}
        VALID_PURPOSES = {"scan_two_session", "returns_61_session"}
        window_evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
        for i, ev in enumerate(
            _seq(aco.get("window_evidence", ()), "action_coverage.window_evidence")
        ):
            eo = _obj(ev, f"action_coverage.window_evidence[{i}]")
            code = _text(eo.get("security_code"), "window_evidence[].security_code")
            t = _isodate(eo.get("signal_date"), "window_evidence[].signal_date")
            purpose = _text(eo.get("purpose"), "window_evidence[].purpose")
            if purpose not in VALID_PURPOSES:
                _fail(
                    "invalid_action_coverage",
                    f"window_evidence[].purpose {purpose!r} not in {sorted(VALID_PURPOSES)}",
                )
            state = _text(eo.get("event_state"), "window_evidence[].event_state")
            if state not in VALID_STATES:
                _fail(
                    "invalid_action_coverage",
                    f"window_evidence[].event_state {state!r} not in {sorted(VALID_STATES)}",
                )
            coverage_id = _text(eo.get("coverage_id"), "window_evidence[].coverage_id")
            if coverage_id not in registers:
                _fail(
                    "invalid_action_coverage",
                    f"window_evidence[{i}] references unregistered coverage_id {coverage_id!r}",
                )
            member_sha = _sha256(eo.get("member_sha256"), "window_evidence[].member_sha256")
            # member_map_sha256: the digest of the P10 member map that located
            # the raw register rows this record claims.  A verified state must
            # reach back to named raw member rows, never a bare summary hash.
            member_map_sha = _sha256(
                eo.get("member_map_sha256"), "window_evidence[].member_map_sha256"
            )
            # Upstream credential: the bound P10 batch/result identity that
            # actually read and verified this window.  It is the provenance
            # the caller may NOT self-declare -- a record without a bound
            # upstream credential is a locator, not proof.
            credential = eo.get("credential")
            cred_o = None
            if credential is not None:
                cred_o = _obj(credential, "window_evidence[].credential")
                cred_o = {
                    "batch_id": _text(cred_o.get("batch_id"), "credential.batch_id"),
                    "manifest_sha256": _sha256(
                        cred_o.get("manifest_sha256"), "credential.manifest_sha256"
                    ),
                    "result_sha256": _sha256(
                        cred_o.get("result_sha256"), "credential.result_sha256"
                    ),
                    "reader": _text(cred_o.get("reader"), "credential.reader"),
                }
            window_evidence[(code, t.isoformat(), purpose)] = {
                "security_code": code,
                "signal_date": t.isoformat(),
                "purpose": purpose,
                "event_state": state,
                "coverage_id": coverage_id,
                "member_sha256": member_sha,
                "member_map_sha256": member_map_sha,
                "credential": cred_o,
                "sessions": [
                    _isodate(d, "window_evidence[].sessions[]").isoformat()
                    for d in _seq(eo.get("sessions", ()), "window_evidence[].sessions")
                ],
                "scan_sessions": [
                    {
                        "date": _isodate(
                            _obj(s, "scan_sessions[]").get("date"),
                            "scan_sessions[].date",
                        ).isoformat(),
                        "adjustment_effective": _obj(s, "scan_sessions[]").get(
                            "adjustment_effective"
                        ),
                    }
                    for s in _seq(eo.get("scan_sessions", ()), "window_evidence[].scan_sessions")
                ],
                "events": [
                    _obj(e, "window_evidence[].events[]")
                    for e in _seq(eo.get("events", ()), "window_evidence[].events")
                ],
                "source_published_at": eo.get("source_published_at"),
                "verified_at": eo.get("verified_at"),
            }
        # Verified-artifact catalogues: per-register named original rows the
        # bound credential actually verified.  The catalogue must carry a
        # digest plus producer/authority identity bound to the P10-signed
        # member bytes (B02/B03/B10 lineage) - a bare caller-assembled list
        # with only register_row is not an authority.  Each row carries the
        # complete original clause (identity, ex_date, cash/bonus/transfer,
        # currency, implementation sha/notice) so the declared event AND the
        # projected action are both compared to the same source row.
        ART_ROW_REQUIRED = (
            "ex_date",
            "cash_gross_per_share",
            "bonus_share_ratio",
            "transfer_share_ratio",
            "currency",
            "implementation_sha256",
            "register_row",
        )
        verified_artifacts: dict[str, dict[str, Any]] = {}
        for i, (cid, vo) in enumerate(_coverage_records(aco, "verified_artifacts").items()):
            if cid not in registers:
                _fail(
                    "invalid_action_coverage",
                    f"verified_artifacts[{i}] references unregistered coverage_id {cid!r}",
                )
            digest = _text(vo.get("digest"), f"verified_artifacts[{i}].digest")
            producer = _text(vo.get("producer"), f"verified_artifacts[{i}].producer")
            authority = vo.get("authority") or vo.get("receipt")
            if authority is None:
                _fail(
                    "invalid_action_coverage",
                    f"verified_artifacts[{i}] lacks authority/receipt binding",
                )
            auth_o = _obj(authority, f"verified_artifacts[{i}].authority")
            authority_id = {
                "manifest_sha256": _sha256(
                    auth_o.get("manifest_sha256"), "authority.manifest_sha256"
                ),
                "result_sha256": _sha256(auth_o.get("result_sha256"), "authority.result_sha256"),
                "batch_id": _text(auth_o.get("batch_id"), "authority.batch_id"),
            }
            rows: dict[Any, dict[str, Any]] = {}
            rejected_rows = []
            for j, r in enumerate(_seq(vo.get("rows", ()), "verified_artifacts[].rows")):
                rr = _obj(r, f"verified_artifacts[{i}].rows[{j}]")
                for field in ART_ROW_REQUIRED:
                    if rr.get(field) is None:
                        _fail(
                            "invalid_action_coverage",
                            f"verified_artifacts[{i}].rows[{j}] lacks {field}",
                        )
                reg_row = rr.get("register_row")
                invalid = _invalid_artifact_ids(rr, required=producer == _SIGNED_ACTION_PRODUCER)
                if invalid:
                    rejected_rows.append({"register_row": reg_row, "fields": invalid})
                rows[reg_row] = dict(rr)
            verified_artifacts[cid] = {
                "digest": digest,
                "producer": producer,
                "authority": authority_id,
                "rows": rows,
            }
            if rejected_rows:
                verified_artifacts[cid]["identity_rejection"] = {
                    "reason": "action_artifact_invalid_identity",
                    "coverage_id": cid,
                    "rows": rejected_rows,
                }
        action_coverage = {
            "default_state": default_state,
            "registers": registers,
            "securities": securities_cov,
            "window_evidence": window_evidence,
            "verified_artifacts": verified_artifacts,
            "note": aco.get("note"),
        }

    request = ReplayRequest(
        scenario_id=scenario_id,
        market_dates=market_dates,
        open_sessions=open_sessions,
        package_id=_text(envelope.get("package_id"), "package_id"),
        manifest_sha256=_sha256(envelope.get("manifest_sha256"), "manifest_sha256"),
        source_members=tuple(_seq(envelope.get("source_members"), "source_members")),
        calendar_rows=tuple(_seq(envelope.get("calendar_rows"), "calendar_rows")),
        listing_rows=tuple(_seq(envelope.get("listing_rows"), "listing_rows")),
        securities=tuple(_seq(envelope.get("securities"), "securities")),
        exclusions=exclusions,
        account=_obj(envelope.get("account"), "account"),
        policy=_obj(envelope.get("policy"), "policy"),
        facts_template=_obj(envelope.get("facts_template"), "facts_template"),
        required_member_ids=required_member_ids,
        member_evidence=member_evidence,
        run_id=_text(envelope.get("run_id"), "run_id"),
        final_day_policy=(
            dict(_obj(final_day_policy, "final_day_policy"))
            if final_day_policy is not None
            else None
        ),
        signal_window=signal_window,
        action_coverage=action_coverage,
    )
    object.__setattr__(request, "request_digest", _request_digest(request))
    return request


def _request_digest(request: ReplayRequest) -> str:
    def norm(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [norm(v) for v in value]
        return value

    return sha256_digest(
        norm(
            {
                "scenario_id": request.scenario_id,
                "market_dates": request.market_dates,
                "open_sessions": request.open_sessions,
                "package_id": request.package_id,
                "manifest_sha256": request.manifest_sha256,
                "source_members": request.source_members,
                "calendar_rows": request.calendar_rows,
                "listing_rows": request.listing_rows,
                "securities": request.securities,
                "exclusions": [e.to_dict() for e in request.exclusions],
                "account": dict(request.account),
                "policy": dict(request.policy),
                "facts_template": dict(request.facts_template),
                "required_member_ids": request.required_member_ids,
                "member_evidence": [m.to_dict() for m in request.member_evidence],
                "run_id": request.run_id,
                "final_day_policy": (
                    dict(request.final_day_policy) if request.final_day_policy is not None else None
                ),
                "signal_window": request.signal_window,
                "action_coverage": (
                    _action_coverage_digest(request.action_coverage)
                    if request.action_coverage is not None
                    else None
                ),
            }
        )
    )


# --- per-day envelope ------------------------------------------------------------


def _action_coverage_digest(ac: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize action_coverage for the request digest: window_evidence is
    keyed by a (code, date, purpose) tuple, which is not JSON-serializable; it
    is emitted as a deterministic sorted record list instead."""
    out = dict(ac)
    we = ac.get("window_evidence") or {}
    out["window_evidence"] = [we[k] for k in sorted(we)]
    return out


def replay_decision_at(market_date: date) -> datetime:
    return datetime.combine(market_date, REPLAY_DECISION_TIME, SHANGHAI)


def _next_open_session(open_sessions: tuple[date, ...], market_date: date) -> date:
    for session in open_sessions:
        if session > market_date:
            return session
    _fail(
        "missing_next_session",
        f"the bound Base calendar has no open session after {market_date}",
    )


def _has_next_session(request: ReplayRequest, market_date: date) -> bool:
    return any(d > market_date for d in request.open_sessions)


def _is_final_display_day(request: ReplayRequest, market_date: date) -> bool:
    fdp = request.final_day_policy
    return (
        fdp is not None
        and _isodate(fdp.get("market_date"), "final_day_policy.market_date") == market_date
    )


def _excluded_codes(request: ReplayRequest, market_date: date) -> frozenset[str]:
    return frozenset(
        e.security_code
        for e in request.exclusions
        if e.session_date is None or e.session_date == market_date
    )


def bar_context_window(open_sessions: tuple[date, ...], market_date: date) -> tuple[date, ...]:
    """The last <=BAR_CONTEXT contiguous open sessions ending at (and including) T.

    Bound by the authoritative calendar, never by counting delivered rows; a
    missing bar stays a gap for its security.
    """
    upto = [d for d in open_sessions if d <= market_date]
    return tuple(upto[-BAR_CONTEXT:])


def _p1_price_input(
    security: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    default_basis: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Check the actual window before DailyBar drops the source price-basis field.

    The fixed release builds holder returns from native prices plus signed
    distribution terms. An adjusted price cannot be substituted even when
    its exception ledger was available.
    """
    dependencies = security.get("dependency_reasons") or {}
    p1 = {
        k: dependencies[k]
        for k in ("p1_factor_ledger", "factor_dependent_actions")
        if isinstance(dependencies, Mapping) and k in dependencies
    }
    incompatible = []
    for bar in bars:
        # Older #749 projected rows inherit the batch basis. An explicit null
        # is unknown, and must not inherit a native basis.
        basis = bar.get("price_basis", default_basis)
        if basis not in ("none", "NONE", "UNADJUSTED"):
            incompatible.append({"session_date": bar["session_date"], "price_basis": basis})
    if not incompatible and not p1:
        return None, None
    reason = None
    if incompatible:
        if (
            p1.get("p1_factor_ledger") == "unverified_or_missing"
            or p1.get("factor_dependent_actions") == "blocked"
        ):
            reason = "p1_factor_ledger_unverified"
        elif any(
            isinstance(row, Mapping) and row.get("ledger") == "P1_factor_pit_exception"
            for row in security.get("exceptions") or ()
        ):
            reason = "p1_factor_visibility_restricted"
        else:
            reason = "replay_price_basis_not_admitted"
    return {
        "contract": "p11.october-price-basis.v1",
        "security_code": security["identity"]["listing_code"],
        "input_default_basis": default_basis,
        "dependency_reasons": p1,
        "non_native_bars": incompatible,
        "disposition": reason or "native_prices_independent",
    }, reason


def _pending_qty(value, label):
    """Strict pending quantity: non-negative int, never truncated or coerced."""
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _fail("invalid_pending_quantity", f"{label} must be a non-negative int, not {value!r}")
    return value


def build_replay_envelope(
    request: ReplayRequest,
    market_date: date,
) -> tuple[dict[str, Any], tuple[str, ...], dict[str, Any], tuple[dict[str, Any], ...]]:
    """Project the request into a p11.raw-history-input.v1 envelope for one T.

    Bars are the security's own rows on the venue's last <=BAR_CONTEXT
    contiguous open sessions ending at T; gaps stay gaps.  limits,
    close_at/knowledge_cutoff maps and the account view are sliced to T's
    signal view (the T+1 open price is only for the execution stage, owned by
    P12).  Source member/calendar/listing declared instants pass verbatim.
    Excluded securities are filtered BEFORE the envelope and named in the
    returned record, plus corporate-action coverage-unknown new-entry
    isolations.  Returns (envelope, excluded_codes, window_info,
    coverage_exclusions).
    """
    if market_date not in set(request.open_sessions):
        _fail("market_date_not_open", f"{market_date} is not a bound open session")
    decision_cutoff = replay_decision_at(market_date)
    excluded = _excluded_codes(request, market_date)
    window = bar_context_window(request.open_sessions, market_date)
    window_set = set(window)

    securities: list[dict[str, Any]] = []
    delivered: dict[str, dict[str, Any]] = {}
    coverage_exclusions: list[dict[str, Any]] = []
    # B-segment: per-order typed pending facts + pool-external unresolved
    # account records (both inside the issued closure, never dropped).
    pending_records: list[dict[str, Any]] = []
    account_unresolved: list[dict[str, Any]] = [
        dict(r)
        for r in _seq(
            request.account.get("unresolved") or (), "account.unresolved"
        )
    ]
    default_basis = _text(request.facts_template.get("price_basis"), "facts_template.price_basis")
    for index, sec in enumerate(request.securities):
        sec_o = _obj(sec, f"securities[{index}]")
        identity = _obj(sec_o.get("identity"), f"securities[{index}].identity")
        code = _text(identity.get("listing_code"), "identity.listing_code")
        if code in excluded:
            continue
        # Held membership is decided by fill/position facts, not bars; an
        # account-member stays on its own held evidence path even when the
        # coverage gate strips its entry-scan input.
        held_member = (
            sec_o.get("held_membership") is True
            or sec_o.get("position_facts") is not None
            or sec_o.get("held_quantity") not in (None, 0)
            or sec_o.get("pending_kind") is not None
        )
        bars = sorted(
            (
                bar
                for bar in _seq(sec_o.get("bars"), f"securities[{index}].bars")
                if _isodate(_obj(bar, "bar").get("session_date"), "bar.session_date") in window_set
            ),
            key=lambda b: _isodate(_obj(b, "bar").get("session_date"), "bar.session_date"),
        )
        filtered = {**sec_o, "bars": bars}
        # October as-observed action projection (P11-side single conversion):
        # the raw typed rows P12 assembles (action_status/ex_date/terms.
        # amount_per_share) become panel feature actions bound to the signed
        # register's proven terms; competing units/revisions are kept as
        # evidence, non-implemented rows never bypass the negative check.
        projected_actions, action_aside = project_signed_actions(
            sec_o.get("actions"), identity, request.action_coverage
        )
        filtered["actions"] = projected_actions
        if action_aside:
            filtered["action_aside"] = action_aside
        for map_key in ("close_at_by_date", "knowledge_cutoff_by_date"):
            raw_map = sec_o.get(map_key) or {}
            filtered[map_key] = {
                day: value
                for day, value in _obj(raw_map, map_key).items()
                if _isodate(day, f"{map_key} key") in window_set
            }
        if "limits" in sec_o:
            filtered["limits"] = [
                lim
                for lim in _seq(sec_o.get("limits"), "securities[].limits")
                if _isodate(_obj(lim, "limit").get("session_date"), "limit.session_date")
                in window_set
            ]
        # An IMPLEMENTED raw row whose owning identity cannot be resolved to
        # this security - or whose implemented event lands inside (or cannot be
        # excluded from) the observed window without resolvable terms - is an
        # unexplained implemented event.  It is kept-aside evidence (never a
        # silent drop AND never a phantom adjustment), and the security
        # isolates on the coverage dependency rather than passing verified.
        unresolvable_aside = any(
            a.get("disposition") in _UNRESOLVABLE_ACTION_DISPOSITIONS for a in action_aside
        )
        filtered.pop("replay_p1_price_input", None)
        p1_evidence, p1_block = _p1_price_input(sec_o, bars, default_basis)
        if p1_evidence is not None:
            filtered["replay_p1_price_input"] = p1_evidence
        # This gate owns new-entry action coverage only. Other evidence,
        # including the separately sourced P1 factor ledger, passes to H1.
        action_block = not held_member and (
            (
                unresolvable_aside
                and _unresolvable_action_affects_window(
                    action_aside, request.open_sessions, market_date
                )
            )
            or not _action_coverage_ok(request, filtered, code, market_date)
        )
        if action_block or p1_block:
            isolation: dict[str, Any] = {
                "security_code": code,
                "reason": p1_block or "replay_action_coverage_unknown",
                "member_or_field": "securities[].bars.price_basis"
                if p1_block
                else "securities[].actions",
                "at": market_date.isoformat(),
                "scope": "per_security",
            }
            if action_block and (
                rejected := _artifact_identity_rejections(request, code, market_date)
            ):
                isolation["action_artifact_rejections"] = rejected
                if not p1_block:
                    isolation["reason"] = rejected[0]["reason"]
            if p1_block:
                isolation["p1_price_input"] = p1_evidence
                if action_block:
                    isolation["action_coverage_reason"] = "replay_action_coverage_unknown"
            if sec_o.get("exceptions"):
                isolation["source_exceptions"] = sec_o["exceptions"]
            coverage_exclusions.append(isolation)
            filtered["bars"] = []
            filtered["actions"] = []
            filtered["limits"] = []
            filtered["close_at_by_date"] = {}
            filtered["knowledge_cutoff_by_date"] = {}
        # Version dedup: byte-identical re-statements of the same action row
        # (same security_code, effective_date, cash/bonus terms and known_at)
        # collapse to ONE row so the projected actions feed the panel once.
        # Two DISTINCT same-date rows are NOT collapsed here - they are the
        # multiplicity conflict the coverage join isolates.
        if filtered.get("actions"):
            filtered["actions"] = _dedupe_action_rows(filtered["actions"])
        bar_dates = {_isodate(_obj(b, "bar").get("session_date"), "bar.session_date") for b in bars}
        # B-segment consumption projection: per-order typed pending facts on
        # securities[].pending[] become the versioned pending_securities list;
        # a sell order keeps its own direction/quantity verbatim.  A nested
        # securities[].account block is only an unresolved candidate: when it
        # contradicts the row-level held/pending markers the row wins and the
        # raw nested row is named and refused as projection evidence; when it
        # is the ONLY account signal it stays pool-external (never an entry).
        nested_account = sec_o.get("account")
        if nested_account is not None:
            nested_o = _obj(nested_account, f"securities[{index}].account")
            nested_role = nested_o.get("account_role")
            # Only an actual value divergence against the row-level held
            # markers is a projection conflict.  A bare nested account block
            # on a row with NO held/pending markers is never fill evidence and
            # lands on account_unresolved below (never an entry).
            conflict = (
                nested_role == "held"
                and held_member
                and nested_o.get("held_quantity") is not None
                and sec_o.get("held_quantity") is not None
                and int(nested_o["held_quantity"]) != int(sec_o["held_quantity"])
            )
            if conflict:
                _fail(
                    "account_projection_conflict",
                    f"securities[{index}] nested account block contradicts the "
                    f"row-level held/pending markers: {nested_o!r}",
                )
            if not held_member:
                # A bare nested account block is NOT fill evidence: it cannot
                # admit a held review or a new entry on its own.  It lands on
                # the versioned account-unresolved detail instead.
                account_unresolved.append(
                    {
                        "account_key": str(nested_o.get("account_key") or code),
                        "role": str(nested_role or "held"),
                        "quantity": nested_o.get("held_quantity"),
                        "missing_reason": "nested_account_block_only_no_row_markers",
                        "source": "securities[].account",
                        "raw": dict(nested_o),
                        "security_code": code,
                        "scope": "account_unresolved",
                    }
                )
                continue  # never reaches the securities[] scan list
        # Per-order pending projection: each securities[].pending[] record is a
        # typed PendingSecurityInput (kind/quantity verbatim); the row-level
        # pending_kind/pending_quantity remain the single-record projection.
        entry_blocked_by_unresolved_direction = False
        for po in _seq(sec_o.get("pending") or (), f"securities[{index}].pending"):
            pobj = _obj(po, f"securities[{index}].pending[]")
            kind = _text(pobj.get("kind"), "pending[].kind")
            if kind == "sell":
                entry_blocked_by_unresolved_direction = True
                # A sell order keeps direction + quantity verbatim but needs an
                # explicit consumer disposition: it lands on the versioned
                # account-unresolved list (role=pending, direction=sell),
                # never folded into a buy-side PendingSecurityInput.
                account_unresolved.append(
                    {
                        "account_key": str(pobj.get("order_key") or code),
                        "role": "pending",
                        "quantity": _pending_qty(
                            pobj.get("quantity"), "pending[].quantity"
                        ),
                        "missing_reason": "sell_order_requires_explicit_disposition",
                        "source": "securities[].pending",
                        "direction": "sell",
                        "order_key": pobj.get("order_key"),
                        "source_receipt": pobj.get("source_receipt"),
                        "security_code": code,
                        "scope": "account_unresolved",
                    }
                )
                continue
            pending_records.append(
                {
                    "security_code": code,
                    "kind": kind,
                    "quantity": _pending_qty(
                        pobj.get("quantity"), "pending[].quantity"
                    ),
                    "order_key": pobj.get("order_key"),
                    "source_receipt": pobj.get("source_receipt"),
                    "direction": pobj.get("direction"),
                }
            )
        if entry_blocked_by_unresolved_direction:
            # The sell direction is an unresolved account disposition; the
            # security never receives a new entry on this run.  Keep held
            # facts so an existing position still surfaces its hold review.
            filtered["bars"] = []
            filtered["actions"] = []
            filtered["limits"] = []
            filtered["close_at_by_date"] = {}
            filtered["knowledge_cutoff_by_date"] = {}
            filtered["scan_eligible"] = False
        securities.append(filtered)
        delivered[code] = {
            "bar_rows": len(bars),
            "window_sessions": len(window),
            "gap_sessions": len(window_set - bar_dates),
            "entry_eligible": not any(e["security_code"] == code for e in coverage_exclusions),
        }

        signed = (request.action_coverage or {}).get("securities", {}).get(code)
        history_venue = identity.get("venue_id")
        if signed and history_venue in _OCTOBER_VENUES:
            mic, warehouse_venue = _OCTOBER_VENUES[history_venue]
            if signed.get(
                "venue_id"
            ) == warehouse_venue and warehouse_venue in _bound_action_venues(request, identity):
                delivered[code]["action_venue_binding"] = {
                    "history_venue": history_venue,
                    "source_venue_id": warehouse_venue,
                    "mic_code": mic,
                    "member_id": _VENUE_MEMBER,
                    "member_sha256": _VENUE_SHA,
                }

    if not securities and not coverage_exclusions:
        _fail(
            "replay_universe_empty",
            f"no qualified securities remain for {market_date} after exclusions",
        )

    calendar_rows = [
        row
        for row in request.calendar_rows
        if _isodate(_obj(row, "calendar_row").get("session_date"), "calendar.session_date")
        <= market_date
    ]
    envelope = {
        "schema_version": "p11.raw-history-input.v1",
        "market_date": market_date.isoformat(),
        "decision_cutoff": decision_cutoff.isoformat(),
        "source_members": [dict(m) for m in request.source_members],
        "calendar_rows": [dict(r) for r in calendar_rows],
        "listing_rows": [dict(r) for r in request.listing_rows],
        "securities": securities,
        # B-segment: per-order typed pending facts (sell orders keep their
        # direction/quantity verbatim; never summed or last-one-wins).
        "pending_securities": pending_records,
        # Pool-external account records with no resolvable listing identity:
        # named per-record inside the issued closure; a held role surfaces a
        # hold_alert, a pending role stays pending-isolated, neither admits
        # any new entry or exit decision for that key.
        "account_unresolved": account_unresolved,
    }
    window_info = {
        "bar_context_sessions": len(window),
        "window_first": window[0].isoformat(),
        "window_last": window[-1].isoformat(),
        "delivered": delivered,
        "coverage_isolated": len(coverage_exclusions),
    }
    return envelope, tuple(sorted(excluded)), window_info, tuple(coverage_exclusions)


# The October Base 2.5.1 venue reference (primary key venue_id, column mic_code).
# #749 projects XSHG/XSHE to SSE/SZSE for H1. Keep both source domains on the wire.
_VENUE_MEMBER = "payload/dwd_trading_venue/all/part-000000.parquet"
_VENUE_SHA = "sha256:ba7e6c9bdcde4918c3167e1efbd9db49b02244fbd64f4d2c5da343b219ad5fed"
_OCTOBER_VENUES = {
    "SSE": ("XSHG", "venue_bec59e31eb6bb28111a10d7e6d2c689f"),
    "SZSE": ("XSHE", "venue_8c0bd9088a0dc88f28e5e7fa8efdce87"),
}


def _bound_action_venues(request: ReplayRequest, identity: Mapping[str, Any]) -> frozenset[str]:
    venue = identity.get("venue_id")
    if not isinstance(venue, str) or not venue:
        return frozenset()
    exact = frozenset((venue,))
    if venue not in _OCTOBER_VENUES or _VENUE_MEMBER not in (
        identity.get("depends_on_member_ids") or ()
    ):
        return exact
    members = [m for m in request.source_members if m.get("member_id") == _VENUE_MEMBER]
    if len(members) != 1:
        return exact
    member = members[0]
    if (
        member.get("member_path"),
        member.get("package_id"),
        member.get("version"),
        member.get("sha256"),
    ) != (
        _VENUE_MEMBER,
        "standard-base-2.5.1-8cd966be87fc",
        "2.5.1",
        _VENUE_SHA,
    ):
        return exact
    return exact.union(_OCTOBER_VENUES[venue])


def _action_row_identity_matches(
    row: Mapping[str, Any], identity: Mapping[str, Any], venues: frozenset[str]
) -> bool:
    return all(
        row.get(k) is None or row[k] == identity.get(k) for k in ("security_id", "listing_id")
    ) and (
        row.get("venue_id") is None
        or (isinstance(row["venue_id"], str) and row["venue_id"] in venues)
    )


def _artifact_identity_rejections(
    request: ReplayRequest, code: str, market_date: date
) -> list[dict[str, Any]]:
    coverage = request.action_coverage or {}
    evidence = coverage.get("window_evidence", {})
    ids = {
        row["coverage_id"]
        for purpose in ("scan_two_session", "returns_61_session")
        if (row := evidence.get((code, market_date.isoformat(), purpose)))
    }
    catalogues = coverage.get("verified_artifacts", {})
    return [
        rejected
        for cid in sorted(ids)
        if (rejected := catalogues.get(cid, {}).get("identity_rejection"))
    ]


def _action_coverage_ok(
    request: ReplayRequest, sec_o: Mapping[str, Any], code: str, market_date: date
) -> bool:
    if request.action_coverage is None or _artifact_identity_rejections(request, code, market_date):
        return False
    we = request.action_coverage.get("window_evidence", {})
    iso = market_date.isoformat()
    scan = we.get((code, iso, "scan_two_session"))
    returns = we.get((code, iso, "returns_61_session"))
    for record in (scan, returns):
        if record is None or record.get("event_state") not in (
            "verified_none",
            "verified_event",
        ):
            return False
        if record.get("credential") is None:
            return False
        # member_map_sha256: the digest of the P10 member map that located the
        # raw register rows; a verified state must reach back to named raw
        # member rows, never a bare state label + one summary hash.
        if not record.get("member_map_sha256"):
            return False

    # Per-security identity in action_coverage.securities is REQUIRED (the R3
    # probe admitted a record bound to no security identity at all).  The
    # declared identity must match the security's own listing/venue/security.
    declared = request.action_coverage.get("securities", {}).get(code)
    if declared is None:
        return False
    ident = sec_o.get("identity", {}) if isinstance(sec_o, Mapping) else {}
    venues = _bound_action_venues(request, ident)
    if not all(
        declared.get(k) and declared[k] == ident.get(k) for k in ("security_id", "listing_id")
    ):
        return False
    if declared.get("venue_id") not in venues:
        return False
    if declared["venue_id"] != ident.get("venue_id"):
        registers = request.action_coverage["registers"]
        if any(registers[r["coverage_id"]]["venue_id"] not in venues for r in (scan, returns)):
            return False

    # The source action/feature key is identity.stock_code (e.g. sh.600000);
    # listing_code (600000) is the trading/coverage key.  When the frozen
    # identity distinguishes them, ONLY stock_code keys this security's
    # action rows (R7-P2): a bare listing_code row is a foreign key, not a
    # fallback.  When they are the same code the single value is the key.
    stock_code = str(ident.get("stock_code") or "")
    listing_code = str(ident.get("listing_code") or code)
    codes = (stock_code,) if stock_code != listing_code else (listing_code,)

    # Verified-artifact catalogues bound to each coverage register: the named
    # rows the upstream credential actually verified.  Caller-supplied events
    # must hit these rows, never a self-recomputed hash.
    bound_artifacts = request.action_coverage.get("verified_artifacts", {})

    # Authoritative venue open calendar: the SAME bound calendar the envelope
    # uses, so sessions are checked verbatim against the venue's real open
    # days -- a session list that skips a real open day, duplicates, goes out
    # of order, or lands on a non-session is a local exclusion.
    opens = list(request.open_sessions)

    # --- scan_two_session: exactly [T-1, T] on the venue calendar, both real
    # open days, no gap/dup/disorder.  An adjustment_effective event in either
    # session blocks the new entry outright.
    idx = opens.index(market_date)
    expected_scan = [opens[idx - 1].isoformat(), market_date.isoformat()] if idx >= 1 else None
    scan_dates = _session_dates(scan.get("scan_sessions") or scan.get("sessions"))
    if expected_scan is None or scan_dates != expected_scan:
        return False
    if _sessions_have_effective_action(scan):
        return False
    # R5: a scan record that declares verified_event but cannot point at a
    # resolvable adjustment (no event rows, or event rows with neither an
    # ex-date nor an adjustment_effective marker) is self-contradictory - the
    # positive state was asserted without a row to bind it to, so the security
    # stays out rather than getting a free adjustment_effective=False.
    if scan.get("event_state") == "verified_event" and not _scan_events_resolvable(
        scan.get("events"), scan.get("scan_sessions")
    ):
        return False

    # --- returns_61_session: exactly the 61 contiguous venue open sessions
    # ending at T (60 adjacent returns), verbatim and in order.  A positive
    # event record must carry the FULL original terms; an unknown/missing one
    # keeps the security out (fail closed), it is not filled in.
    if idx + 1 < BAR_CONTEXT:
        return False
    expected_returns = [d.isoformat() for d in opens[idx - BAR_CONTEXT + 1 : idx + 1]]
    returns_dates = _session_dates(returns.get("sessions"))
    if returns_dates != expected_returns:
        return False
    # A verified_event returns window must carry full raw terms; verified_none
    # passes.  The negated condition is returned directly.
    if returns.get("event_state") == "verified_event":
        if not _returns_events_have_full_terms(returns.get("events")):
            return False
        # R5: the verified event terms must JOIN the projected actions[] - a
        # declared dividend/bonus that never reaches the feature row, or one
        # that reaches it with a different amount, is a local block.  The
        # projection maps pre-tax cash -> cash_dividend_per_share and folds
        # bonus+transfer into the panel's single bonus_share_ratio (once, never
        # double-counted); the ex-date that affects a return is inside
        # (first_close, last_close] i.e. sessions[1:].
        if not _returns_events_join_actions(
            returns.get("events"),
            sec_o.get("actions"),
            codes,
            returns_dates,
            identity=ident,
            venues=venues,
            bound_artifacts=bound_artifacts,
            coverage_id=returns.get("coverage_id"),
        ):
            return False
    elif returns.get("event_state") == "verified_none" and _actions_in_window(
        sec_o.get("actions"), codes, returns_dates
    ):
        # A positively "no adjustment" returns window contradicts any raw
        # action row inside it (the conflict is isolated, never coalesced away).
        return False
    return True


# --- signed action-authority assembly (R9) ------------------------------------------
_SIGNED_ACTION_PRODUCER = "p10.signed-action-members.v1"
_SIGNED_ACTION_READER = "p12.e1-signed-member-reader"


def _pointer_rows(pointer_root: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Normalize a batch RESULT pointer to {basename: {path, sha256}}."""
    out: dict[str, dict[str, str]] = {}
    ml = pointer_root.get("members")
    if isinstance(ml, (list, tuple)):
        for m in ml:
            if not isinstance(m, Mapping):
                continue
            rel = _text(m.get("path"), "pointer_root.members[].path")
            out[rel.rsplit("/", 1)[-1]] = {
                "path": rel,
                "sha256": _sha256(m.get("sha256"), "pointer_root.members[].sha256"),
            }
    for key in ("events", "windows", "coverage"):
        p = pointer_root.get(key)
        if isinstance(p, Mapping) and p.get("path") is not None:
            rel = _text(p.get("path"), "pointer_root." + key + ".path")
            out[rel.rsplit("/", 1)[-1]] = {
                "path": rel,
                "sha256": _sha256(p.get("sha256"), "pointer_root." + key + ".sha256"),
            }
    return out


def _member_bytes(members: Mapping[str, bytes], rel_path: str) -> bytes:
    b = members.get(rel_path)
    if not isinstance(b, (bytes, bytearray)):
        _fail(
            "action_authority_member_missing",
            "member " + repr(rel_path) + " not present in members map",
        )
    return bytes(b)


def _member_sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def bind_signed_manifest(
    manifest_bytes: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    """Verify real MANIFEST bytes once and return a reuse-bound object.

    #749 reads the pinned release MANIFEST file once, recomputes its sha256,
    and confirms it equals the release's frozen pin (ROUTES.json /
    external_manifests).  This helper performs that single verification and
    returns an object the same job can hand to build_signed_action_authority()
    for every signed security-day - the parsed ``manifest`` plus the proven
    ``manifest_sha256`` derived from the actual bytes, never a caller-copied
    digest.  A byte whose recomputed hash differs from the frozen pin fails
    closed.
    """
    expected = _sha256(expected_sha256, "expected_manifest_sha256")
    actual = _member_sha(bytes(manifest_bytes))
    if actual != expected:
        _fail(
            "action_authority_manifest_pin_mismatch",
            "MANIFEST bytes sha " + actual + " != frozen pin " + expected,
        )
    return {
        "manifest": _obj(json.loads(bytes(manifest_bytes).decode("utf-8")), "manifest"),
        "manifest_sha256": actual,
    }


def _resolve(
    manifest_members: Mapping[str, Any],
    members: Mapping[str, bytes],
    rel_path: str,
) -> tuple[bytes, str]:
    """Locate member bytes by exact MANIFEST path and verify declared sha256.

    MANIFEST.members is keyed by the path under the P10 source_prefix; each
    entry carries {path, sha256, bytes}.  The caller names the member by that
    MANIFEST path (or a unique trailing suffix of it); we require the entry
    then verify the recomputed bytes equal the declared sha256.  A basename
    alone is NOT sufficient - many batches share filenames like RESULT.json.
    """
    entry = manifest_members.get(rel_path)
    if entry is None:
        matches = [
            (k, e)
            for k, e in manifest_members.items()
            if isinstance(e, Mapping)
            and (str(k).endswith("/" + rel_path) or str(e.get("path", "")) == rel_path)
        ]
        if len(matches) == 1:
            entry = matches[0][1]
        elif len(matches) > 1:
            _fail(
                "action_authority_member_ambiguous",
                "member path "
                + repr(rel_path)
                + " matches "
                + str(len(matches))
                + " MANIFEST entries",
            )
    if not isinstance(entry, Mapping):
        _fail(
            "action_authority_manifest_member_missing",
            "MANIFEST has no member entry for " + repr(rel_path),
        )
    declared = _sha256(entry.get("sha256"), "manifest.members[].sha256")
    b = _member_bytes(members, rel_path)
    if _member_sha(b) != declared:
        _fail(
            "action_authority_member_sha_mismatch",
            "member " + rel_path + " sha " + _member_sha(b) + " != MANIFEST " + declared,
        )
    return b, declared


def build_signed_action_authority(
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str | Mapping[str, Any],
    accepted_member: str,
    result_member: str,
    members: Mapping[str, bytes],
    accepted_manifest_members: Mapping[str, Any] | None = None,
    index_member: str | None = None,
    security_code: str,
    listing_code: str,
    stock_code: str,
    security_id: str,
    listing_id: str,
    venue_id: str,
    signal_date: str,
    coverage_id: str | None = None,
    routes_sha256: str | None = None,
) -> dict[str, Any]:
    """Rebuild action_coverage for ONE signed security-day from real P10 members.

    The content root is the pinned release MANIFEST
    (quanthus.p10.action-evidence-reader-map.v1): ``manifest.members`` names
    every member's path under the P10 source_prefix and its declared sha256,
    and ``manifest_sha256`` is the real sha256 of the MANIFEST file bytes.
    ``result_member`` / ``accepted_member`` are the MANIFEST paths of the
    batch RESULT pointer and the Owner-signed accepted-uses table; both are
    resolved by content sha256 out of the same members map, so the file the
    caller read must equal the MANIFEST-declared member.

    ``members`` maps MANIFEST path -> raw bytes P12 read once.  Every resolved
    member is recomputed and compared to the MANIFEST-declared sha256; a
    substituted member fails closed.  The emitted credential carries the
    actual manifest/result/accepted file sha256 - never a recomputed JSON
    digest - so the bound provenance is the named original, not a hash the
    caller could fill in.
    """
    iso = _isodate(signal_date, "signal_date").isoformat()
    # ``manifest_sha256`` may be the bound object returned by
    # bind_signed_manifest() (carrying the proven file sha derived from the
    # real bytes) or the raw digest string a caller verified itself.  Both
    # resolve to the same proven sha - never a recomputed JSON digest.
    if isinstance(manifest_sha256, Mapping):
        manifest_sha = _sha256(manifest_sha256.get("manifest_sha256"), "manifest_sha256")
        mroot = _obj(manifest_sha256.get("manifest", manifest), "manifest")
    else:
        manifest_sha = _sha256(manifest_sha256, "manifest_sha256")
        mroot = _obj(manifest, "manifest")
    manifest_members = _obj(mroot.get("members"), "manifest.members")
    routes_sha = _sha256(routes_sha256, "routes_sha256") if routes_sha256 is not None else None

    # accepted table bytes -> the versioned Owner-signed uses table.  It may
    # live in this release's MANIFEST or in the separate MANIFEST that pins the
    # signed uses (e.g. B10 when the batch itself is B02); resolve by sha.
    acc_members = manifest_members
    if accepted_member not in acc_members and accepted_manifest_members is not None:
        acc_members = _obj(accepted_manifest_members, "accepted_manifest_members")
    accepted_b, accepted_decl = _resolve(acc_members, members, accepted_member)
    accepted = json.loads(accepted_b.decode("utf-8"))
    # batch RESULT pointer -> which member files belong to this batch.
    pointer_b, result_decl = _resolve(manifest_members, members, result_member)
    pointer_root = json.loads(pointer_b.decode("utf-8"))

    # Resolve this batch's events/windows/coverage members by their declared
    # sha256 out of the same MANIFEST-pinned members map.
    prows = _pointer_rows(pointer_root)
    for need in ("events", "security-windows", "coverage"):
        name = "security-windows.json" if need == "security-windows" else need + ".json"
        if name not in prows:
            _fail(
                "action_authority_pointer_missing",
                "batch RESULT does not name member " + name,
            )
    batch_dir = result_member.rsplit("/", 1)[0]

    def _batch_rel(p: str) -> str:
        # Pointer paths may already be MANIFEST paths or batch-relative names.
        return p if p in manifest_members else (batch_dir + "/" + p)

    events_rel = _batch_rel(prows["events.json"]["path"])
    windows_rel = _batch_rel(prows["security-windows.json"]["path"])
    coverage_rel = _batch_rel(prows["coverage.json"]["path"])
    events_b, _ = _resolve(manifest_members, members, events_rel)
    windows_b, batch_windows_sha = _resolve(manifest_members, members, windows_rel)
    coverage_b, _ = _resolve(manifest_members, members, coverage_rel)
    events_doc = json.loads(events_b.decode("utf-8"))
    windows_doc = json.loads(windows_b.decode("utf-8"))
    coverage_doc = json.loads(coverage_b.decode("utf-8"))
    # Optional signed windows INDEX (accepted.windows_file e.g. WINDOW-USE.json):
    # for security-days the batch member does not cover, this signed index is
    # the authoritative per-(code,T) window record.  Resolve it by sha too.
    index_doc = None
    windows_index_sha: str | None = None
    if index_member is not None:
        idx_members = manifest_members
        if index_member not in idx_members and accepted_manifest_members is not None:
            idx_members = _obj(accepted_manifest_members, "accepted_manifest_members")
        index_b, windows_index_sha = _resolve(idx_members, members, index_member)
        index_doc = json.loads(index_b.decode("utf-8"))
    # 2. The Owner-signed table must name this security/date and both uses.
    acc = _obj(accepted, "accepted")
    acc_codes = _obj(acc.get("accepted_codes_by_date"), "accepted.accepted_codes_by_date")
    day_codes = acc_codes.get(iso)
    if not isinstance(day_codes, list) or listing_code not in [str(c) for c in day_codes]:
        _fail(
            "action_authority_security_day_not_signed",
            "(security "
            + listing_code
            + ", T "
            + iso
            + ") is not in the signed accepted_codes_by_date",
        )
    required = {
        _text(u, "accepted.required_uses[]")
        for u in _seq(acc.get("required_uses", ()), "accepted.required_uses")
    }
    if not {"scan_two_session", "returns_61_session"} <= required:
        _fail(
            "action_authority_required_uses_missing",
            "signed table does not require both window purposes",
        )
    # windows_sha256 pins the signed windows INDEX document; verify the bytes
    # the caller supplied for accepted.windows_file when present.
    acc_windows_file = acc.get("windows_file")
    if acc_windows_file is not None:
        acc_windows_sha = _sha256(acc.get("windows_sha256"), "accepted.windows_sha256")
        if index_doc is not None and windows_index_sha != acc_windows_sha:
            _fail(
                "action_authority_windows_index_sha_mismatch",
                "selected index member differs from the signed windows index",
            )
        idx_b = members.get(str(acc_windows_file))
        if idx_b is not None and _member_sha(idx_b) != acc_windows_sha:
            _fail(
                "action_authority_windows_index_sha_mismatch",
                "signed windows index member sha != accepted.windows_sha256",
            )
        if windows_index_sha is None and idx_b is not None:
            windows_index_sha = acc_windows_sha
    member_map_sha = _sha256(acc.get("source_members_sha256"), "accepted.source_members_sha256")

    # 3. Rebuild the two windows for THIS security/T.
    rows = [
        w
        for w in _seq(windows_doc.get("windows"), "windows.windows")
        if isinstance(w, Mapping)
        and str(w.get("security_code")) == listing_code
        and str(w.get("T")) == iso
    ]

    def _find(use: str) -> dict[str, Any] | None:
        for w in rows:
            if str(w.get("use")) == use:
                return dict(w)
        return None

    scan_w = _find("scan_previous_and_T")
    returns_w = _find("61_session_total_return_and_exceptions")
    scan_member_sha = returns_member_sha = batch_windows_sha

    # Fallback: the signed windows INDEX (WINDOW-USE.json) carries the per-
    # (code,T) window for signed security-days the batch member does not cover.
    # Its row names code/signal_date/security_id/listing_id/venue_id and a
    # `scan` + `returns` sub-record; map it to the same internal shape.
    if (scan_w is None or returns_w is None) and index_doc is not None:
        assert windows_index_sha is not None  # returned by _resolve for this index
        idx_row = None
        for x in _seq(index_doc.get("windows"), "index.windows"):
            if (
                isinstance(x, Mapping)
                and str(x.get("code")) == listing_code
                and str(x.get("signal_date")) == iso
            ):
                idx_row = dict(x)
                break
        if idx_row is not None:
            idx_scan = idx_row.get("scan") or {}
            idx_ret = idx_row.get("returns") or {}
            if scan_w is None and idx_scan:
                scan_member_sha = windows_index_sha
                scan_w = {
                    "use": "scan_previous_and_T",
                    "event_state": _text(idx_scan.get("state"), "index.scan.state"),
                    "session_states": [
                        {
                            "session": s.get("date"),
                            "event_state": (
                                "verified_event"
                                if s.get("adjustment_effective")
                                else "verified_none"
                            ),
                        }
                        for s in _seq(idx_scan.get("sessions"), "index.scan.sessions")
                    ],
                    "sessions": [
                        s.get("date") for s in _seq(idx_scan.get("sessions"), "index.scan.sessions")
                    ],
                    "security_id": idx_row.get("security_id"),
                    "listing_id": idx_row.get("listing_id"),
                    "venue_id": idx_row.get("venue_id"),
                    "observed_at": (idx_row.get("original_clocks") or {}).get("observed_at"),
                    "verified_at": (idx_row.get("original_clocks") or {}).get("verified_at"),
                }
            if returns_w is None and idx_ret:
                returns_member_sha = windows_index_sha
                returns_w = {
                    "use": "61_session_total_return_and_exceptions",
                    "event_state": _text(idx_ret.get("state"), "index.returns.state"),
                    "sessions": [
                        _text(d, "index.returns.sessions[]")
                        for d in _seq(idx_ret.get("sessions"), "index.returns.sessions")
                    ],
                    "effective_dates_in_60_return_intervals": [
                        e.get("ex_date")
                        for e in _seq(idx_ret.get("events", ()), "index.returns.events")
                    ],
                    "_index_events": _seq(idx_ret.get("events", ()), "index.returns.events"),
                    "security_id": idx_row.get("security_id"),
                    "listing_id": idx_row.get("listing_id"),
                    "venue_id": idx_row.get("venue_id"),
                    "observed_at": (idx_row.get("original_clocks") or {}).get("observed_at"),
                    "verified_at": (idx_row.get("original_clocks") or {}).get("verified_at"),
                }
    if scan_w is None or returns_w is None:
        _fail(
            "action_authority_window_missing",
            "no window for " + listing_code + " T=" + iso + " in batch member or signed index",
        )
    for w in (scan_w, returns_w):
        for key, expected in (
            ("security_id", security_id),
            ("listing_id", listing_id),
            ("venue_id", venue_id),
        ):
            if str(w.get(key)) != expected:
                _fail(
                    "action_authority_identity_mismatch",
                    "window "
                    + repr(w.get("use"))
                    + " "
                    + key
                    + " "
                    + repr(w.get(key))
                    + " != frozen "
                    + repr(expected),
                )

    # scan window -> loader's scan_sessions shape
    scan_sessions = []
    for s in _seq(scan_w.get("session_states"), "window.session_states"):
        so = _obj(s, "window.session_states[]")
        st = _text(so.get("event_state"), "session_states[].event_state")
        scan_sessions.append(
            {
                "date": _isodate(so.get("session"), "session_states[].session").isoformat(),
                "adjustment_effective": st == "verified_event",
            }
        )

    # returns window -> sessions + declared events joined to bound terms
    returns_sessions = [
        _isodate(d, "window.sessions[]").isoformat()
        for d in _seq(returns_w.get("sessions"), "window.sessions")
    ]
    returns_state = _text(returns_w.get("event_state"), "window.event_state")
    # 4. Catalogue of named original rows the bound credential verified.
    #    SSE events carry sse_directory_positive.evidence.source_row_ordinal;
    #    SZSE events name the issuer notice_id and a version/raw implementation
    #    sha instead - both resolve to the row's register key + impl digest.
    # Index-sourced windows carry their own pre-normalized event rows (full
    # terms + register_row + implementation sha) - use them directly; batch
    # windows use the events member filtered to this security.
    idx_events = returns_w.get("_index_events") or []
    if idx_events:
        ev_rows = [e for e in idx_events if isinstance(e, Mapping)]
    else:
        ev_rows = [
            e
            for e in _seq(events_doc.get("events"), "events.events")
            if isinstance(e, Mapping) and str(e.get("security_code")) == listing_code
        ]
    cat_rows: dict[Any, dict[str, Any]] = {}
    event_rows: list[dict[str, Any]] = []
    effective_dates = {
        str(d)
        for d in _seq(
            returns_w.get("effective_dates_in_60_return_intervals"),
            "window.effective_dates_in_60_return_intervals",
        )
    }
    for e in ev_rows:
        pos = e.get("sse_directory_positive")
        pos_e = (
            _obj(pos.get("evidence"), "sse_directory_positive.evidence")
            if isinstance(pos, Mapping)
            else {}
        )
        reg_row: Any = pos_e.get("source_row_ordinal")
        if reg_row is None:
            reg_row = e.get("register_row")
        if reg_row is None:
            reg_row = e.get("notice_id") or e.get("notice")
        if reg_row is None:
            _fail("action_authority_event_no_register", "event has no register key")
        # implementation digest: official_pdf.sha256 (SSE), version/raw.sha256
        # (SZSE batch), or implementation_sha256 (signed-index normalized row).
        impl_raw = e.get("official_pdf")
        impl: str | None = None
        if isinstance(impl_raw, Mapping) and impl_raw.get("sha256"):
            impl = _sha256(impl_raw.get("sha256"), "events[].official_pdf.sha256")
        else:
            for cand in (
                e.get("implementation_sha256"),
                e.get("version"),
                (e.get("raw") or {}).get("sha256") if isinstance(e.get("raw"), Mapping) else None,
            ):
                if cand:
                    impl = _sha256(cand, "events[].implementation sha")
                    break
        if impl is None:
            _fail("action_authority_event_no_implementation", "event has no implementation sha")
        row = {
            "register_row": reg_row,
            "ex_date": _text(e.get("ex_date"), "events[].ex_date"),
            "cash_gross_per_share": _text(
                e.get("cash_dividend_per_share_gross")
                if e.get("cash_dividend_per_share_gross") is not None
                else e.get("cash_gross_per_share"),
                "events[].cash_dividend_per_share_gross",
            ),
            "bonus_share_ratio": _text(e.get("bonus_share_ratio"), "events[].bonus_share_ratio"),
            "transfer_share_ratio": _text(
                e.get("transfer_share_ratio"), "events[].transfer_share_ratio"
            ),
            "currency": _text(e.get("currency"), "events[].currency"),
            "implementation_sha256": impl,
            "notice": _text(e.get("notice_id") or e.get("notice"), "events[].notice_id"),
            # Index-sourced rows do not repeat the identity the window already
            # bound (security/listing/venue were verified equal above); fall
            # back to the function's own verified identity so the catalogue row
            # binds the real security, never a literal "None" string.
            "security_id": e["security_id"] if e.get("security_id") is not None else security_id,
            "listing_id": e["listing_id"] if e.get("listing_id") is not None else listing_id,
            "venue_id": e["venue_id"] if e.get("venue_id") is not None else venue_id,
            "stock_code": stock_code,
        }
        cat_rows[reg_row] = row
        ex_iso = row["ex_date"]
        if returns_state == "verified_event" and ex_iso in effective_dates:
            event_rows.append(
                {
                    "ex_date": ex_iso,
                    "cash_gross_per_share": row["cash_gross_per_share"],
                    "bonus_share_ratio": row["bonus_share_ratio"],
                    "transfer_share_ratio": row["transfer_share_ratio"],
                    "currency": row["currency"],
                    "implementation_sha256": impl,
                    "register_row": reg_row,
                    "notice": row["notice"],
                }
            )
    cov_id = coverage_id or coverage_doc.get("coverage_id") or coverage_doc.get("id")
    if not cov_id:
        # SZSE coverage.json carries no coverage_id; derive a stable register id
        # from the batch RESULT id + venue, never from the member bytes' hash.
        cov_id = (
            str(pointer_root.get("id", "batch"))
            + "-"
            + str(coverage_doc.get("register", {}).get("venue", venue_id)).replace("XSHE", "XSHE")
            + "-action-coverage"
        )
    for reg_row, row in cat_rows.items():
        invalid = _invalid_artifact_ids(row, required=True)
        if invalid:
            _fail(
                "action_artifact_invalid_identity",
                f"coverage_id={cov_id} register_row={reg_row} fields={','.join(invalid)}",
            )
    batch_id = _text(pointer_root.get("batch", pointer_root.get("id")), "pointer_root.batch")
    cred = {
        "batch_id": batch_id,
        "manifest_sha256": manifest_sha,
        "result_sha256": result_decl,
        "accepted_sha256": accepted_decl,
        "reader": _SIGNED_ACTION_READER,
    }
    if routes_sha is not None:
        cred["routes_sha256"] = routes_sha
    catalog = {
        "coverage_id": cov_id,
        "digest": sha256_digest(
            {str(k): v for k, v in sorted(cat_rows.items(), key=lambda kv: str(kv[0]))}
        ),
        "producer": _SIGNED_ACTION_PRODUCER,
        "authority": {
            "manifest_sha256": manifest_sha,
            "result_sha256": result_decl,
            "accepted_sha256": accepted_decl,
            "batch_id": batch_id,
        },
        "rows": list(cat_rows.values()),
    }

    def _ev(
        purpose: str, w: dict[str, Any], member_sha: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "security_code": listing_code,
            "signal_date": iso,
            "purpose": purpose,
            "event_state": _text(w.get("event_state"), "window.event_state"),
            "coverage_id": cov_id,
            "member_sha256": member_sha,
            "member_map_sha256": member_map_sha,
            "credential": dict(cred),
            "observed_at": w.get("observed_at"),
            "verified_at": w.get("verified_at"),
            **extra,
        }

    return {
        "default_state": "unknown",
        "registers": [
            {
                "coverage_id": cov_id,
                "version": str(coverage_doc.get("version", "1")),
                "sha256": _member_sha(coverage_b),
                "venue_id": venue_id,
                "event_types": [
                    _text(t, "coverage.categories[]")
                    for t in _seq(coverage_doc.get("categories", ()), "coverage.categories")
                ],
                "source_member_id": coverage_rel,
            }
        ],
        "securities": [
            {
                "security_code": security_code,
                "security_id": security_id,
                "listing_id": listing_id,
                "venue_id": venue_id,
            }
        ],
        "window_evidence": [
            _ev(
                "scan_two_session",
                scan_w,
                scan_member_sha,
                {
                    "scan_sessions": scan_sessions,
                    "sessions": [s["date"] for s in scan_sessions],
                },
            ),
            _ev(
                "returns_61_session",
                returns_w,
                returns_member_sha,
                {"sessions": returns_sessions, "events": event_rows},
            ),
        ],
        "verified_artifacts": [catalog],
        "bound_provenance": {
            "manifest_sha256": manifest_sha,
            "result_sha256": result_decl,
            "accepted_sha256": accepted_decl,
            "source_members_sha256": member_map_sha,
            "windows_index_sha256": windows_index_sha,
            "routes_sha256": routes_sha,
            "batch_id": batch_id,
        },
        "note": (
            "derived from Owner-signed member bytes resolved through the pinned "
            "MANIFEST; caller-copied fields are not independently qualifying"
        ),
    }


def _feature_action_rows(actions: Any) -> list[Mapping[str, Any]]:
    """Rows already in the panel feature-action shape pass through verbatim."""
    out: list[Mapping[str, Any]] = []
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return out
    for a in actions:
        if (
            isinstance(a, Mapping)
            and a.get("security_code") is not None
            and a.get("effective_date") is not None
            and a.get("cash_dividend_per_share") is not None
        ):
            out.append(a)
    return out


def _raw_term_number(term: Any) -> Decimal | None:
    """Best-effort numeric from one raw term cell (amount or numerator/denominator)."""
    if not isinstance(term, Mapping):
        return None
    for key in ("amount_per_share",):
        v = term.get(key)
        if v is not None:
            try:
                return Decimal(str(v))
            except Exception:
                return None
    num = term.get("ratio_numerator")
    den = term.get("ratio_denominator")
    if num is not None and den is not None:
        try:
            d = Decimal(str(den))
            if d == 0:
                return None
            return Decimal(str(num)) / d
        except Exception:
            return None
    return None


def _signed_action_terms(
    action_coverage: Mapping[str, Any] | None,
    *,
    security_id: str,
    ex_iso: str,
) -> tuple[Decimal | None, Decimal | None, str | None, Mapping[str, Any] | None]:
    """Look up the proven terms for (security, ex_date) in the signed register.

    Returns (cash_gross_per_share, bonus_share_ratio_incl_transfer, known_at_or_none,
    the matched catalogue row) when the verified-artifact catalogue binds an
    event for this ex_date, else (None, None, None, None).  The signed register
    is the frozen P11/P10 ruling that already resolved competing units/revisions
    (per-share vs per-10) - the projection uses its terms, not a raw row's.
    """
    if not isinstance(action_coverage, Mapping):
        return None, None, None, None
    artifacts = action_coverage.get("verified_artifacts") or ()
    # verified_artifacts is emitted as a list of catalogues (each with a
    # coverage_id + rows); tolerate a mapping keyed by coverage_id too.
    if isinstance(artifacts, Mapping):
        catalogues = list(artifacts.values())
    elif isinstance(artifacts, Sequence) and not isinstance(artifacts, str):
        catalogues = list(artifacts)
    else:
        return None, None, None, None

    def _dec(v: Any) -> Decimal | None:
        try:
            return Decimal(str(v))
        except Exception:
            return None

    for cat in catalogues:
        if isinstance(cat, Mapping) and cat.get("identity_rejection"):
            continue
        rows = cat.get("rows", cat) if isinstance(cat, Mapping) else cat
        seq = rows.values() if isinstance(rows, Mapping) else rows
        for row in seq or ():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("security_id")) != str(security_id):
                continue
            ex = row.get("ex_date")
            ex_row = ex.isoformat() if isinstance(ex, date) else str(ex)
            if ex_row != ex_iso:
                continue
            cash = _dec(row.get("cash_gross_per_share"))
            bonus = _dec(row.get("bonus_share_ratio"))
            transfer = _dec(row.get("transfer_share_ratio"))
            if cash is None or bonus is None or transfer is None:
                continue
            # known_at: the row's day-level source publication date (the
            # implementation-notice business day) when present, within the
            # signed October replay's business-day semantics; never a
            # back-filled historical instant.
            known: str | None = None
            oc = row.get("original_clocks")
            if isinstance(oc, Mapping):
                spd = oc.get("source_publication_date")
                if isinstance(spd, str) and spd:
                    known = spd[:10]
            if known is None:
                spd = row.get("source_publication_date") or row.get("announce_date")
                if isinstance(spd, str) and spd:
                    known = spd[:10]
                elif isinstance(spd, date):
                    known = spd.isoformat()
            return cash, bonus + transfer, known, row
    return None, None, None, None


# Aside dispositions that mark an IMPLEMENTED raw action row the projection
# could not resolve to a real adjustment for this security.  These are kept
# as evidence (never silently dropped), and when the unexplained implemented
# event lands inside - or cannot be excluded from - the observed window the
# caller isolates the security's action dependency.
_UNRESOLVABLE_ACTION_DISPOSITIONS = frozenset({"implemented_unknown"})


def _bound_action_venues_for_identity(identity: Mapping[str, Any]) -> frozenset[str]:
    """The venue ids a raw action row may legitimately name for this security.

    The identity's own venue_id plus its Base-2.5.1 warehouse counterpart
    (SSE/SZSE <-> venue_<hash>): a source row may carry either wire name.
    """
    venue = identity.get("venue_id")
    out: set[str] = {str(venue)} if venue else set()
    mapped = _OCTOBER_VENUES.get(str(venue)) if venue is not None else None
    if mapped is not None:
        out.add(mapped[0])
        out.add(mapped[1])
    else:
        # A warehouse venue_id maps back to its exchange short name too.
        for short, (_mic, wh) in _OCTOBER_VENUES.items():
            if str(venue) == wh:
                out.add(short)
    return frozenset(out)


def _unresolvable_action_affects_window(
    aside: Sequence[Mapping[str, Any]],
    open_sessions: Sequence[date],
    market_date: date,
) -> bool:
    """True when an unexplained IMPLEMENTED aside row touches the window.

    The observed window is the <=BAR_CONTEXT contiguous open sessions ending
    at T.  An unexplained implemented event whose ex_date lands inside it -
    or whose ex_date is absent/unparseable so it cannot be excluded - is an
    adjustment the projection cannot account for; the security isolates.
    An unexplained row strictly BEFORE the window is out of scope and does
    not isolate (a real old event already left the observed series).
    """
    window = bar_context_window(tuple(open_sessions), market_date)
    earliest = window[0].isoformat() if window else market_date.isoformat()
    for a in aside:
        if a.get("disposition") not in _UNRESOLVABLE_ACTION_DISPOSITIONS:
            continue
        ex = a.get("ex_date")
        if ex is None or str(ex) == "":
            # Implemented but no usable ex_date: cannot exclude it from the
            # window - fail closed on the dependency.
            return True
        ex_iso = ex.isoformat() if isinstance(ex, date) else str(ex)
        try:
            ex_iso = date.fromisoformat(ex_iso).isoformat()
        except (ValueError, TypeError):
            return True
        if ex_iso >= earliest:
            # Inside the observed window (or after it): unexplained.  An
            # ex_date BEFORE the window does not affect these returns.
            return True
    return False


def project_signed_actions(
    actions: Any,
    identity: Mapping[str, Any],
    action_coverage: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project raw-wire action rows to the panel feature-action shape.

    The single P11-side conversion for the October as-observed replay.  P12
    delivers the original typed rows (action_status/ex_date/terms.amount_per_share/
    record_source); the panel consumes security_code/effective_date/
    cash_dividend_per_share/bonus_share_ratio/known_at.

    - Rows already in feature shape pass through unchanged.
    - Every consumed raw row's OWNER is verified: any security_id /
      listing_id / stock_code / venue_id field the row fills must equal this
      security's identity (venue admits the bound exchange<->warehouse pair).
      A row naming a different owner is an unexplained foreign IMPLEMENTED
      event - kept-aside evidence; the caller isolates the dependency and the
      row never contributes terms nor silently passes through.
    - Only IMPLEMENTED rows project; PROPOSED / NO_DISTRIBUTION / other
      statuses are kept-aside evidence (they are not adjustments and must not
      bypass the verified_none negative check by being silently dropped NOR
      emitted as phantom actions).  An IMPLEMENTED row with no ex_date, or no
      resolvable terms and no signed ruling, is 'implemented_unknown' aside -
      unexplained, never a silent drop.
    - One feature row per ex_date: the signed register's proven per-share terms
      (the frozen ruling that resolved competing unit/revision versions) win;
      the raw rows' competing terms are recorded as audit evidence.  When the
      register binds no event for that ex_date the raw row's own normalized
      terms project so the coverage join's verified_none / multiplicity checks
      still see the unproven adjustment and isolate it.
    - known_at is the day-level source-publication date (the implementation
      notice's business day) inside signed replay semantics; never a
      back-filled historical instant.
    Returns (feature_actions, kept_aside_evidence).
    """
    feature = [dict(a) for a in _feature_action_rows(actions)]
    aside: list[dict[str, Any]] = []
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return feature, aside

    stock_code = str(identity.get("stock_code") or identity.get("listing_code") or "")
    security_id = str(identity.get("security_id") or "")
    # The P11 projection consumes only rows whose raw ownership resolves to
    # this security's verified identity.  A wire row that names a DIFFERENT
    # security/listing/venue/stock owner is foreign material: it must not
    # contribute terms to this security's feature action nor silently pass
    # through as aside evidence - it is an unexplained IMPLEMENTED row and
    # the caller isolates the dependency.
    venues = _bound_action_venues_for_identity(identity)

    def _row_owner_matches(a: Mapping[str, Any]) -> bool:
        """Each consumed raw row must resolve to THIS security's identity.

        A field the row leaves empty is unconstraining (the wire keeps the
        security-level owner implicit); a field the row fills must equal the
        security's own security_id / listing_id / venue (bound venues incl.)
        / stock_code.  Any filled field that disagrees is a foreign row.
        """
        checks = (
            (a.get("security_id"), identity.get("security_id")),
            (a.get("listing_id"), identity.get("listing_id")),
        )
        for row_v, id_v in checks:
            if row_v is not None and id_v is not None and str(row_v) != str(id_v):
                return False
            if row_v is not None and id_v is None:
                return False
        sc = a.get("stock_code")
        if sc is not None:
            id_sc = identity.get("stock_code")
            if id_sc is not None and str(sc) != str(id_sc):
                return False
            if id_sc is None and str(sc) != str(identity.get("listing_code")):
                return False
        ven = a.get("venue_id")
        return not (ven is not None and str(ven) not in venues)

    def _dec(v: Any) -> Decimal | None:
        try:
            return Decimal(str(v))
        except Exception:
            return None

    # Group raw IMPLEMENTED rows by ex_date.
    implemented: dict[str, list[Mapping[str, Any]]] = {}
    for a in actions:
        if not isinstance(a, Mapping) or a.get("action_status") is None:
            continue
        status = str(a.get("action_status"))
        ex = a.get("ex_date")
        ex_iso = ex.isoformat() if isinstance(ex, date) else (str(ex) if ex else None)
        if status != "IMPLEMENTED":
            # NO_DISTRIBUTION / PROPOSED / withdrawn: not an adjustment.  Kept
            # as evidence so a raw row never silently skips the negative check.
            aside.append(
                {
                    "corporate_action_id": a.get("corporate_action_id"),
                    "action_status": status,
                    "ex_date": ex_iso,
                    "disposition": "not_implemented_no_adjustment",
                }
            )
            continue
        # Owner binding is checked on EVERY consumed IMPLEMENTED row: a row
        # whose named owner is not this security is an unexplained foreign
        # implemented event (ex_date kept for the caller's window test).
        if not _row_owner_matches(a):
            aside.append(
                {
                    "corporate_action_id": a.get("corporate_action_id"),
                    "action_status": status,
                    "ex_date": ex_iso,
                    "security_id": a.get("security_id"),
                    "listing_id": a.get("listing_id"),
                    "venue_id": a.get("venue_id"),
                    "stock_code": a.get("stock_code"),
                    "disposition": "implemented_unknown",
                    "unknown_kind": "foreign_owner",
                }
            )
            continue
        if ex_iso is None:
            aside.append(
                {
                    "corporate_action_id": a.get("corporate_action_id"),
                    "action_status": status,
                    "disposition": "implemented_unknown",
                    "unknown_kind": "no_ex_date",
                }
            )
            continue
        implemented.setdefault(ex_iso, []).append(a)
    for ex_iso, rows in implemented.items():
        signed_cash, signed_bonus, signed_known, matched = _signed_action_terms(
            action_coverage, security_id=security_id, ex_iso=ex_iso
        )
        # Normalize each raw row's own terms for evidence + fallback.
        raw_terms = []
        for a in rows:
            terms = a.get("terms") or {}
            rt_cash = _raw_term_number(terms.get("CASH_AMOUNT_PER_SHARE"))
            rt_bonus = _raw_term_number(terms.get("BONUS_SHARES_PER_SHARE"))
            rt_transfer = _raw_term_number(terms.get("TRANSFER_SHARES_PER_SHARE"))
            shares: Decimal | None = None
            parts = [b for b in (rt_bonus, rt_transfer) if b is not None]
            if parts:
                shares = Decimal(sum(parts))
            raw_terms.append(
                {
                    "corporate_action_id": a.get("corporate_action_id"),
                    "record_source": a.get("record_source"),
                    "source_revision": a.get("source_revision"),
                    "cash_dividend_per_share": rt_cash,
                    "bonus_share_ratio": shares,
                    "announced_at": a.get("announced_at"),
                }
            )
        cash: Decimal | None
        bonus: Decimal | None
        if matched is not None and signed_cash is not None:
            cash, bonus = signed_cash, signed_bonus
            provenance = "signed_register"
        else:
            # No signed event for this ex_date: use the raw row's own terms so
            # the coverage join's negative check can still isolate the unproven
            # adjustment (never dropped).  Distinct competing raw terms on the
            # same date stay a multiplicity conflict downstream.
            cash = raw_terms[0]["cash_dividend_per_share"]
            bonus = raw_terms[0]["bonus_share_ratio"]
            provenance = "raw_row_unsigned"
        # known_at: signed source-publication date first, else the matching raw
        # IMPLEMENTED row's announced_at business date; must precede ex_date.
        known = signed_known
        if known is None:
            for rt in raw_terms:
                ann = rt.get("announced_at")
                if isinstance(ann, str) and ann:
                    known = ann[:10]
                    break
                if isinstance(ann, (date, datetime)):
                    known = ann.date().isoformat() if isinstance(ann, datetime) else ann.isoformat()
                    break
        if cash is None and bonus is None:
            # IMPLEMENTED but no numeric term and no signed ruling: the row
            # asserts an adjustment the projection cannot price.  It is
            # unexplained - the caller isolates the dependency when it lands
            # in (or cannot be excluded from) the window; never a silent drop.
            aside.append(
                {
                    "ex_date": ex_iso,
                    "disposition": "implemented_unknown",
                    "unknown_kind": "no_terms",
                    "raw": raw_terms,
                }
            )
            continue
        feature.append(
            {
                "security_code": stock_code,
                "effective_date": ex_iso,
                "cash_dividend_per_share": str(cash if cash is not None else Decimal(0)),
                "bonus_share_ratio": str(bonus if bonus is not None else Decimal(0)),
                "known_at": known,
                "projection_provenance": provenance,
                "competing_terms": raw_terms if len(raw_terms) > 1 else [],
            }
        )
    return feature, aside


def _dedupe_action_rows(actions: Any) -> list[Any]:
    """Collapse byte-identical action re-statements to one row per term tuple.

    Rows are keyed on (security_code, effective_date, cash, bonus, known_at);
    a re-delivered row with identical terms is the same version and feeds the
    panel once.  DISTINCT rows on the same date are preserved verbatim - they
    are the multiplicity conflict the coverage join isolates, not data to merge.
    """
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return list(actions) if isinstance(actions, Sequence) else []

    def _nd(v: Any) -> Decimal | None:
        try:
            return Decimal(str(v))
        except Exception:
            return None

    seen: set[tuple[Any, ...]] = set()
    out: list[Any] = []
    for a in actions:
        if not isinstance(a, Mapping):
            out.append(a)
            continue
        eff = a.get("effective_date")
        eff_iso = eff.isoformat() if isinstance(eff, date) else str(eff)
        ka = a.get("known_at")
        ka_iso = ka.isoformat() if isinstance(ka, (date, datetime)) else str(ka)
        key = (
            str(a.get("security_code")),
            eff_iso,
            _nd(a.get("cash_dividend_per_share")),
            _nd(a.get("bonus_share_ratio")),
            ka_iso,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _actions_in_window(
    actions: Any, codes: tuple[str, ...], window_sessions: Sequence[str]
) -> bool:
    """True when any projected action for THIS security has an effective_date
    inside the return window (first_close, last_close] = window_sessions[1:].

    The action row key is the source stock_code (e.g. sh.600000); a bare
    listing_code is admitted only when it is the identity's own listing_code.
    An action row keyed to neither code is not this security's action.
    """
    allowed = {c for c in codes if c}
    if not isinstance(actions, Sequence) or isinstance(actions, str):
        return False
    window_set = set(window_sessions[1:])
    for a in actions:
        if not isinstance(a, Mapping):
            continue
        if str(a.get("security_code")) not in allowed:
            continue
        eff = a.get("effective_date")
        eff_iso = eff.isoformat() if isinstance(eff, date) else str(eff)
        if eff_iso in window_set:
            return True
    return False


def _returns_events_join_actions(
    events: Any,
    actions: Any,
    codes: tuple[str, ...],
    window_sessions: Sequence[str],
    identity: Mapping[str, Any] | None = None,
    bound_artifacts: Mapping[str, Mapping[Any, Mapping[str, Any]]] | None = None,
    coverage_id: str | None = None,
    venues: frozenset[str] | None = None,
) -> bool:
    """Join verified returns events to the projected actions[] for this security.

    Returns True only when every in-window declared event maps to a matching
    projected action AND every in-window projected action is backed by a
    declared event - the panel's total_return then used the same amounts the
    verified register proved.  Field mapping (P11-P10-ACTION-REGISTER-EVIDENCE):
      event.cash_gross_per_share -> action.cash_dividend_per_share (pre-tax, CNY)
      event.bonus_share_ratio + event.transfer_share_ratio -> action.bonus_share_ratio
        (the panel's single dilution ratio; summed once, never double-counted)
      event.currency must be CNY (the amount column's declared unit)
      action.known_at must precede effective_date (the panel's mapped_actions filter)
      event.implementation_sha256 / register_row / notice bind the named
        register artifact the bound credential actually verified.
    """
    window_set = set(window_sessions[1:])
    ident = identity or {}
    if venues is None:
        venues = frozenset((str(ident["venue_id"]),)) if ident.get("venue_id") else frozenset()
    allowed_codes = {c for c in codes if c}

    def _dec(v: Any) -> Decimal | None:
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def _event_ex(e: Mapping[str, Any]) -> str | None:
        """Parse the declared ex_date strictly; an unparseable date is NOT
        skipped (that would silently admit a malformed row) - it returns the
        sentinel '!' which never matches an in-window or out-window date, and
        the caller treats it as a hard failure below."""
        ex = e.get("ex_date")
        if ex is None:
            return None
        if isinstance(ex, date):
            return ex.isoformat()
        try:
            return date.fromisoformat(str(ex)).isoformat()
        except (ValueError, TypeError):
            return "!"

    # Index in-window projected actions by effective_date for THIS security.
    # The action row key is the source stock_code (e.g. sh.600000); a bare
    # listing_code is admitted only when it is the identity's own listing_code.
    window_actions: dict[str, list[Mapping[str, Any]]] = {}
    if isinstance(actions, Sequence) and not isinstance(actions, str):
        for a in actions:
            if not isinstance(a, Mapping) or str(a.get("security_code")) not in allowed_codes:
                continue
            eff = a.get("effective_date")
            eff_iso = eff.isoformat() if isinstance(eff, date) else str(eff)
            if eff_iso in window_set:
                if not _action_row_identity_matches(a, ident, venues):
                    return False
                window_actions.setdefault(eff_iso, []).append(a)

    # One-to-one binding: each in-window ex_date must have exactly ONE distinct
    # projected action row.  Identical duplicate rows (same cash/bonus/known_at)
    # dedupe to one by version; two DISTINCT rows on the same date (a proven
    # 0.116 plus an unproven 0.232) are a multiplicity conflict - the real
    # adjustment cannot be resolved to a single row, so the security isolates.
    def _action_terms(a: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            _dec(a.get("cash_dividend_per_share")),
            _dec(a.get("bonus_share_ratio")),
            str(a.get("known_at")),
        )

    distinct_window_actions: dict[str, list[Mapping[str, Any]]] = {}
    for eff_iso, rows in window_actions.items():
        seen_terms: set[tuple[Any, ...]] = set()
        deduped: list[Mapping[str, Any]] = []
        for a in rows:
            t = _action_terms(a)
            if t not in seen_terms:
                seen_terms.add(t)
                deduped.append(a)
        if len(deduped) != 1:
            # Two different adjustment rows claim the same ex_date.
            return False
        distinct_window_actions[eff_iso] = deduped

    declared_dates: set[str] = set()
    declared_terms: dict[str, tuple[Any, ...]] = {}
    for e in events or ():
        if not isinstance(e, Mapping):
            return False
        ex_iso = _event_ex(e)
        if ex_iso == "!" or ex_iso is None:
            # An unparseable or absent ex_date on a declared verified event is
            # a hard failure, not an out-of-window skip - the caller asserted
            # a concrete event and the row cannot be located.
            return False
        if ex_iso not in window_set:
            # An event outside (first_close, last_close] does not affect these
            # returns; it is neither required nor a conflict for THIS window.
            continue
        if str(e.get("currency")) != "CNY":
            return False
        cash = _dec(e.get("cash_gross_per_share"))
        bonus = _dec(e.get("bonus_share_ratio"))
        transfer = _dec(e.get("transfer_share_ratio"))
        if cash is None or bonus is None or transfer is None:
            return False
        expected_bonus = bonus + transfer  # combined once, not double-counted
        # A second declared event on the same ex_date with DIFFERENT terms is a
        # declaration conflict; identical re-declarations dedupe to one.
        this_terms = (cash, expected_bonus)
        prior = declared_terms.get(ex_iso)
        if prior is not None and prior != this_terms:
            return False
        declared_terms[ex_iso] = this_terms
        declared_dates.add(ex_iso)
        # Original provenance: the declared implementation_sha256 /
        # register_row / notice must hit the named row in the verified
        # artifact catalogue the bound credential actually verified - a
        # caller may not self-declare a forged register row, and an event
        # whose coverage register carries NO catalogue has no object the
        # provenance can bind to, so the verified_event is refused (the
        # evidence grade is preserved, never upgraded by a non-null field).
        cat = (bound_artifacts or {}).get(coverage_id) if coverage_id else None
        if cat is None:
            return False
        artifacts = cat.get("rows", cat) if isinstance(cat, Mapping) else cat
        reg_row = e.get("register_row")
        reg_key: Any
        try:
            reg_key = int(reg_row)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            reg_key = str(reg_row)
        row = artifacts.get(reg_key) or artifacts.get(str(reg_row))
        if row is None:
            return False
        impl = row.get("implementation_sha256") or row.get("sha256")
        if impl is None or str(e.get("implementation_sha256")) != str(impl):
            return False
        notice = row.get("notice") or row.get("notice_id")
        if notice is not None and str(e.get("notice")) != str(notice):
            return False
        # Bind the verified security identity the row names.
        if not _action_row_identity_matches(row, ident, venues):
            return False
        # Source terms: the catalogue row's original clause must equal the
        # event's declared clause AND the projected action's clause.  A row
        # may carry the true announcement digest while the event/action use a
        # different economic term - compare ex_date, cash, bonus+transfer
        # (combined once) and currency so the real clause actually governs.
        row_ex = row.get("ex_date")
        row_ex_iso = (
            row_ex.isoformat()
            if isinstance(row_ex, date)
            else (str(row_ex) if row_ex is not None else None)
        )
        if row_ex_iso is not None and row_ex_iso != ex_iso:
            return False
        row_cash = _dec(row.get("cash_gross_per_share"))
        row_bonus = _dec(row.get("bonus_share_ratio"))
        row_transfer = _dec(row.get("transfer_share_ratio"))
        if row_cash is None or row_bonus is None or row_transfer is None:
            return False
        if row_cash != cash or (row_bonus + row_transfer) != expected_bonus:
            return False
        row_cur = row.get("currency")
        if row_cur is not None and str(row_cur) != "CNY":
            return False
        cands = distinct_window_actions.get(ex_iso) or []
        matched = False
        for a in cands:
            if (
                _dec(a.get("cash_dividend_per_share")) == cash
                and _dec(a.get("bonus_share_ratio")) == expected_bonus
            ):
                # known_at must precede effective_date (panel mapped filter).
                ka_raw = a.get("known_at")
                try:
                    ka_date = (
                        ka_raw
                        if isinstance(ka_raw, date)
                        else (date.fromisoformat(str(ka_raw)) if ka_raw else None)
                    )
                except ValueError:
                    ka_date = None
                if ka_date is not None and ka_date < date.fromisoformat(ex_iso):
                    matched = True
        if not matched:
            return False

    # Reverse join: every in-window projected action must be backed by a
    # declared verified event; an orphan action row is a conflict.
    return all(eff_iso in declared_dates for eff_iso in distinct_window_actions)


def _session_dates(value: Any) -> list[str]:
    """Normalize a session list to ISO dates.

    Accepts either a list of ISO date strings (returns) or a list of objects
    with a 'date' key (scan).  Duplicates and order are preserved so the
    caller's verbatim list is checked, not a re-derived set.
    """
    out: list[str] = []
    for item in value or ():
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping):
            d = item.get("date")
            out.append(d.isoformat() if isinstance(d, date) else str(d))
    return out


def _sessions_have_effective_action(scan: Mapping[str, Any]) -> bool:
    """True when any scanned session has an adjustment_effective action.

    A scan session object carries 'adjustment_effective'; the raw
    WINDOW-USE shape may also list events with an 'adjustment_effective' or
    an effective 'ex_date' on a scanned session.  Any effective action on a
    scanned session blocks the new entry.
    """
    scanned = {s.get("date") for s in scan.get("scan_sessions") or () if isinstance(s, Mapping)}
    scanned_iso = {
        d.isoformat() if isinstance(d, date) else str(d) for d in scanned if d is not None
    }
    for s in scan.get("scan_sessions") or ():
        if isinstance(s, Mapping) and s.get("adjustment_effective") is True:
            return True
    for e in scan.get("events") or ():
        if not isinstance(e, Mapping):
            continue
        if e.get("adjustment_effective") is True:
            return True
        # An event row naming WHEN it applies (ex_date / effective_date /
        # session_date / date) that lands on a scanned session is an effective
        # adjustment on that day - the frozen gate blocks new entry on a real
        # T-1/T ex-date even when the row spells it as a date, not a bool.
        ex = e.get("ex_date") or e.get("effective_date") or e.get("session_date") or e.get("date")
        if ex is not None:
            ex_iso = ex.isoformat() if isinstance(ex, date) else str(ex)
            if ex_iso in scanned_iso:
                return True
    return False


def _scan_events_resolvable(events: Any, scan_sessions: Any) -> bool:
    """True when a scan verified_event names a resolvable adjustment bound to
    one of the two scanned sessions.

    A scan 'verified_event' must reach a concrete event row that either marks
    a scanned session adjustment_effective or names an ex/effective/session
    date ON one of the two scanned days.  A positive label whose rows only
    carry unrelated dates is evidence-bound-to-nothing - the security is
    isolated, never granted a free clean scan.  (A real on-session ex-date
    separately blocks the entry via _sessions_have_effective_action; this
    check catches the 'verified but for another day' contradiction.)
    """
    scanned: set[str] = set()
    for sess in scan_sessions or ():
        if isinstance(sess, Mapping):
            d = sess.get("date")
            if d is not None:
                scanned.add(d.isoformat() if isinstance(d, date) else str(d))
        elif isinstance(sess, str):
            scanned.add(sess)

    def _day(v: Any) -> str | None:
        if isinstance(v, date):
            return v.isoformat()
        try:
            return date.fromisoformat(str(v)).isoformat()
        except (ValueError, TypeError):
            return None

    evs = list(events or ())
    if not evs:
        return False
    bound = False
    for e in evs:
        if not isinstance(e, Mapping):
            return False
        if e.get("adjustment_effective") is True:
            bound = True
            continue
        raw = e.get("ex_date") or e.get("effective_date") or e.get("session_date") or e.get("date")
        if raw is None:
            return False
        day = _day(raw)
        if day is None:
            # An unparseable date on a declared verified row is a hard failure.
            return False
        if day in scanned:
            bound = True
    return bound


# Raw-term fields a positive returns event must carry verbatim, per
# P11-P10-ACTION-REGISTER-EVIDENCE-v1 / WINDOW-USE.  A 'verified_event' with
# missing or null original terms is not evidence the return series is clean.
RETURNS_EVENT_REQUIRED_FIELDS = (
    "ex_date",
    "implementation_sha256",
    "cash_gross_per_share",
    "bonus_share_ratio",
    "transfer_share_ratio",
    "register_row",
    "currency",
)


def _returns_events_have_full_terms(events: Any) -> bool:
    """True only when every declared returns event carries full raw terms."""
    evs = list(events or ())
    if not evs:
        return False
    for e in evs:
        if not isinstance(e, Mapping):
            return False
        for field in RETURNS_EVENT_REQUIRED_FIELDS:
            if e.get(field) is None:
                return False
    return True


def _facts_for_day(request: ReplayRequest, market_date: date) -> DailyFactsView:
    ft = dict(request.facts_template)
    next_session = _next_open_session(request.open_sessions, market_date)
    return DailyFactsView(
        batch_id=_text(ft.get("batch_id"), "facts_template.batch_id"),
        market_date=market_date,
        next_session=next_session,
        source_id=_text(ft.get("source_id"), "facts_template.source_id"),
        source_version=_text(ft.get("source_version"), "facts_template.source_version"),
        calendar_version=_text(ft.get("calendar_version"), "facts_template.calendar_version"),
        # The projection above allows only native bar inputs into the rule.
        price_basis="none",
        members=request.member_evidence,
        required_member_ids=request.required_member_ids,
        intake_receipt_id=_text(ft.get("intake_receipt_id"), "facts_template.intake_receipt_id"),
        intake_received_at=_isodatetime(ft.get("intake_received_at"), "intake_received_at"),
        intake_first_read_at=_isodatetime(ft.get("intake_first_read_at"), "intake_first_read_at"),
        intake_verified_at=_isodatetime(ft.get("intake_verified_at"), "intake_verified_at"),
    )


def _policy_for_day(request: ReplayRequest, market_date: date) -> AdvicePolicy:
    p = dict(request.policy)
    decision_cutoff = replay_decision_at(market_date)
    next_open = datetime.combine(
        _next_open_session(request.open_sessions, market_date), SESSION_OPEN, SHANGHAI
    )
    return AdvicePolicy(
        policy_id=_text(p.get("policy_id"), "policy.policy_id"),
        version=_text(p.get("version"), "policy.version"),
        decision_cutoff=decision_cutoff,
        # In replay the publish instant is the logical decision coordinate itself:
        # the day receipt records replay_observed_at separately, so published_at
        # stays bound to the simulated T-close timeline rather than the wall clock.
        published_at=decision_cutoff,
        window_ref=_text(p.get("window_ref"), "policy.window_ref"),
        eligible_at=next_open,
        expires_at=datetime.combine(
            _next_open_session(request.open_sessions, market_date),
            SESSION_CLOSE,
            SHANGHAI,
        ),
        candidate_rule_digest=_text(p.get("candidate_rule_digest"), "policy.candidate_rule_digest"),
        trusted_producer_ids=tuple(
            _text(pid, "trusted_producer_ids[]")
            for pid in _seq(p.get("trusted_producer_ids", ()), "trusted_producer_ids")
        ),
    )


def _account_for_day(request: ReplayRequest, market_date: date) -> AccountFactsView:
    a = dict(request.account)
    account_id = _text(a.get("account_id"), "account.account_id")
    if not account_id.startswith("replay-"):
        _fail(
            "invalid_account_isolation",
            "replay account_id must carry the replay- prefix and stay out of the daily ledger",
        )
    # account.as_of_overrides[date] supplies the scenario ledger's snapshot for
    # that signal day; the shared as_of is the fallback for unlisted days.
    overrides = a.get("as_of_overrides") or {}
    declared_as_of = (
        _isodatetime(overrides[market_date.isoformat()], "account.as_of_overrides[]")
        if market_date.isoformat() in _obj(overrides, "as_of_overrides")
        else _isodatetime(a.get("as_of"), "account.as_of")
    )
    # The account snapshot is the scenario ledger's state as of THIS signal day,
    # never a later day's read.  It is bound to the logical T coordinate so the
    # replay ledger stays isolated without pretending to be a live fact.
    bound_as_of = replay_decision_at(market_date)
    if declared_as_of > bound_as_of:
        _fail(
            "account_snapshot_after_signal",
            f"account.as_of {declared_as_of} is after the signal day's logical "
            f"coordinate {bound_as_of}; replay binds the T view, never a later read",
        )
    return AccountFactsView(
        account_id=account_id,
        as_of=bound_as_of,
        cash_available=(
            Decimal(str(a["cash_available"])) if a.get("cash_available") is not None else None
        ),
        active_or_reserved_security_codes=tuple(
            _text(c, "active_or_reserved_security_codes[]")
            for c in _seq(
                a.get("active_or_reserved_security_codes", ()),
                "active_or_reserved_security_codes",
            )
        ),
        active_industry_counts=tuple(
            (_text(pair[0], "industry"), int(pair[1]))
            for pair in _seq(a.get("active_industry_counts", ()), "active_industry_counts")
        ),
        new_buckets_today=int(a.get("new_buckets_today", 0)),
        pending_commitment_total=(
            Decimal(str(a["pending_commitment_total"]))
            if a.get("pending_commitment_total") is not None
            else None
        ),
    )


def _source_policy_for_day(request: ReplayRequest, market_date: date) -> P6Base24CandidatePolicy:
    next_open = datetime.combine(
        _next_open_session(request.open_sessions, market_date), SESSION_OPEN, SHANGHAI
    )
    # The declared observation end is the T close itself; per-member source
    # visibility stays in the envelope and is enforced by the H1 bind gate
    # (source_visibility_after_cutoff) so a late member is a named block here,
    # not a construction-time crash hiding which member was late.
    clock = AfterCloseDecisionClock(
        market_date=market_date,
        source_available_at=datetime.combine(market_date, SESSION_CLOSE, SHANGHAI),
        decision_at=replay_decision_at(market_date),
        next_session_open=next_open,
    )
    return P6Base24CandidatePolicy(
        clock=clock,
        # Scan scope floor is 2026-01-01 (P6Base24CandidatePolicy gate); bars
        # older than that still feed warmup through the panel, not the scan.
        history_start=max(min(request.open_sessions), date(2026, 1, 1)),
        inputs_manifest_sha256=request.manifest_sha256,
    )


def run_replay_day(
    request: ReplayRequest,
    market_date: date,
    *,
    run_daily_advice_from_history: Callable[..., Any],
    runner: Any,
    resources: Any,
    card: Any,
    split_policy: Any,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    clock: Callable[[], datetime] | None = None,
    observed_clock: Callable[[], datetime] | None = None,
    source_read_at: datetime | None = None,
) -> dict[str, Any]:
    """Run ONE replay session through the real chain; return a day receipt.

    ``clock`` drives the domain chain run/complete instants and is bound to the
    logical T-close timeline (callers pass replay_decision_at(T)); it is never
    the wall clock pretending to be T. ``observed_clock`` records the actual job
    instant into replay_observed_at (defaults to the real wall clock).
    """
    if market_date not in set(request.open_sessions):
        _fail("market_date_not_open", f"{market_date} is not a bound open session")
    observed_at = (observed_clock or (lambda: datetime.now(timezone.utc)))()
    if observed_at.tzinfo is None:
        _fail("invalid_replay_clock", "replay observed clock must be aware")
    domain_clock = clock or (lambda: replay_decision_at(market_date))

    if not _has_next_session(request, market_date):
        if not _is_final_display_day(request, market_date):
            _fail(
                "missing_next_session",
                f"no open session follows {market_date} and no final_day_policy "
                "declares it; the T+1 open quote is never fabricated",
            )
        _, excluded, _w, _c = build_replay_envelope(request, market_date)
        return {
            "schema_version": REPLAY_DAY_SCHEMA,
            "scenario_id": request.scenario_id,
            "ledger_namespace": request.ledger_namespace,
            "market_date": market_date.isoformat(),
            "input_grade": INPUT_GRADE,
            "replay_observed_at": observed_at.isoformat(),
            "logical_decision_at": replay_decision_at(market_date).isoformat(),
            "next_session": None,
            "final_day_disposition": "final_display_only",
            "request_digest": request.request_digest,
            "package_id": request.package_id,
            "manifest_sha256": request.manifest_sha256,
            "excluded_security_codes": list(excluded),
            "disposition": "final_display_only",
            "package_present": False,
            "candidates": [],
            "held_signals": [],
            "daily_live_use": False,
            "real_account_use": False,
            "automatic_trading_use": False,
            "strategy_performance_evaluation": False,
            "note": (
                "Sealed calendar has no next session; the day replays T-only "
                "for display/intent review. No T+1 quote exists or is fabricated."
            ),
        }

    envelope, excluded, window_info, coverage_exclusions = build_replay_envelope(
        request, market_date
    )
    facts = _facts_for_day(request, market_date)
    policy = _policy_for_day(request, market_date)
    account = _account_for_day(request, market_date)
    source_policy = _source_policy_for_day(request, market_date)

    day_block: dict[str, Any] | None = None
    output = None
    try:
        output = run_daily_advice_from_history(
            envelope,
            runner=runner,
            resources=resources,
            facts=facts,
            policy=policy,
            account=account,
            run_id=f"{request.run_id}-{market_date.isoformat()}",
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
            card=card,
            split_policy=split_policy,
            source_policy=source_policy,
            clock=domain_clock,
            input_exclusions=list(coverage_exclusions),
            # The as-observed replay's actual-read instant: each sealed source
            # version's readability is judged against THIS run's real read
            # (knowledge_from <= observed_at); a version only readable later
            # still blocks.  Bound on its own named field, never the domain
            # (logical T) clock.
            as_observed_at=source_read_at or observed_at,
        )
    except ProductDailyError as exc:
        day_block = {
            "reason": getattr(exc, "reason_code", "replay_day_failed"),
            "detail": str(exc),
            "scope": "whole_day",
        }
        if isinstance(exc, NoQualifiedRawInputs):
            day_block["input_exclusions"] = list(exc.input_exclusions)

    receipt: dict[str, Any] = {
        "schema_version": REPLAY_DAY_SCHEMA,
        "scenario_id": request.scenario_id,
        "ledger_namespace": request.ledger_namespace,
        "market_date": market_date.isoformat(),
        "input_grade": INPUT_GRADE,
        "replay_observed_at": observed_at.isoformat(),
        "logical_decision_at": replay_decision_at(market_date).isoformat(),
        "next_session": facts.next_session.isoformat(),
        "request_digest": request.request_digest,
        "package_id": request.package_id,
        "manifest_sha256": request.manifest_sha256,
        "excluded_security_codes": list(excluded),
        "source_id": facts.source_id,
        "account_id": account.account_id,
        "daily_live_use": False,
        "real_account_use": False,
        "automatic_trading_use": False,
        "strategy_performance_evaluation": False,
    }
    receipt["bar_context_sessions"] = window_info["bar_context_sessions"]
    receipt["window_first"] = window_info["window_first"]
    receipt["window_last"] = window_info["window_last"]
    receipt["delivered"] = window_info["delivered"]
    receipt["coverage_isolated"] = window_info["coverage_isolated"]
    receipt["coverage_exclusions"] = list(coverage_exclusions)
    if day_block is not None:
        receipt["disposition"] = "blocked"
        receipt["day_block"] = day_block
        receipt["candidates"] = []
        receipt["held_signals"] = []
        return receipt

    receipt.update(_rule_day_projection(output, window_info))
    return receipt


def _signal_fields(signal: Any) -> dict[str, Any]:
    """Project a producer signal using only fields that actually exist."""

    def first(*names: str) -> Any:
        for name in names:
            value = getattr(signal, name, None)
            if value is not None:
                return value
        return None

    return {
        "security_code": getattr(signal, "security_code", None),
        "action": first("action", "kind"),
        "opportunity_id": first("opportunity_id"),
        "fraction": (str(f) if (f := first("fraction")) is not None else None),
        "target_maximum_position": (
            str(f)
            if (f := first("target_maximum_position", "target_remaining_fraction")) is not None
            else None
        ),
        # The v2 wire field a downstream executable reduce consumes; held
        # signals surface it so the projection keeps real reduce/full targets.
        "target_position_fraction": (
            str(f) if (f := first("target_position_fraction")) is not None else None
        ),
        "exit_kind": first("exit_kind"),
        "expert_decision_digest": first("expert_decision_digest"),
        "reason_codes": [str(c) for c in (getattr(signal, "reason_codes", None) or ())],
    }


def _rule_day_projection(output: Any, window_info: Mapping[str, Any]) -> dict[str, Any]:
    """Carry the real RuleDayOutput fields P12 consumes, never a re-summary."""
    seam = output.seam
    result = output.result
    pkg = result.package
    # The release's registered wire is the versioned P12 surface, not the
    # package's v1 to_dict (which drops the reduce/full exit fields by
    # design).  Serialize decision_sets on the bound held_reduction_wire
    # version so a reduce keeps exit_kind/target_position_fraction.
    wire = None
    if pkg is not None:
        from quanthus.product_daily.recommendation.publish import (
            DECISION_SET_SCHEMA_FIELD,
            P11_ENVELOPE_V2,
            V2_DS_SCHEMA,
        )

        sets = []
        for item in pkg.decision_sets:
            raw = item.to_wire_dict()
            raw[DECISION_SET_SCHEMA_FIELD] = V2_DS_SCHEMA
            if (
                result.publishable
                and item.audit.admission_disposition == "empty_advice"
                and not output.candidates
                and not output.held_signals
                and output.receipt is not None
                and seam.scan_result is not None
            ):
                # A quiet day comes from the scan that actually evaluated T.
                # The P12 receipt keeps the full signed input closure; carry the
                # qualified rows into the package so it can explain its own date.
                t = item.market_date.isoformat()
                verdicts = {
                    sec_id: verdict
                    for sec_id, session, verdict in seam.scan_result.signal_verdicts
                    if session == t
                }
                scans = [
                    {
                        "security_code": r["security_code"],
                        "security_id": r["security_id"],
                        "verdict": verdicts.get(r["security_id"]),
                        "gates": r["gates"],
                        "transition": seam.scan_result.transition_evidence.get(r["security_id"]),
                    }
                    for r in output.source_closure.get("history_receipts", ())
                    if r["status"] == "qualified"
                ]
                if scans and all(row["verdict"] == "not_triggered" for row in scans):
                    raw["audit"]["replay_no_action"] = {
                        "schema_version": "p11.replay-no-action.v1",
                        "market_date": t,
                        "run_id": output.receipt.run_id,
                        "source_closure_digest": output.receipt.source_closure_digest,
                        "candidate_count": 0,
                        "scans": scans,
                        "message": "今日无操作",
                    }
                    raw["decisions"][0]["explanations"][0] = {
                        **raw["decisions"][0]["explanations"][0],
                        "text": "今日无操作",
                    }
            sets.append(raw)
        wire = pkg.to_dict()
        wire["decision_sets"] = sets
        wire["schema_version"] = P11_ENVELOPE_V2
    assessment = getattr(result, "assessment", None)
    if assessment is not None and hasattr(assessment, "to_dict"):
        assessment = assessment.to_dict()
    elif assessment is not None and not isinstance(assessment, Mapping):
        assessment = {
            "disposition": getattr(assessment, "disposition", None),
            "admitted": len(getattr(assessment, "admitted_members", ()) or ()),
            "blocked_required": len(getattr(assessment, "blocked_required", ()) or ()),
            "blocked_optional": len(getattr(assessment, "blocked_optional", ()) or ()),
        }
    return {
        "disposition": result.disposition.value,
        "publishable": getattr(result, "publishable", None),
        "package_present": pkg is not None,
        "package_id": getattr(pkg, "package_id", None),
        "package_schema": getattr(pkg, "schema", None),
        "package": wire,
        "held_reduction_wire": RELEASE_WIRE_VERSION,
        "package_digest": sha256_digest(wire) if wire is not None else None,
        "diagnostics": [
            dict(d) if isinstance(d, Mapping) else {"reason": str(d)} for d in result.diagnostics
        ],
        "assessment": assessment,
        "seam": {
            "day_blocked": seam.day_blocked,
            "decisions": [
                {
                    "security_code": d.security_code,
                    "event_id": d.event_id,
                    "disposition": (
                        d.disposition.value
                        if hasattr(d.disposition, "value")
                        else str(d.disposition)
                    ),
                    "reason": getattr(d, "reason", None),
                }
                for d in seam.decisions
            ],
            "excluded": [dict(e) for e in getattr(seam, "excluded", ())],
            "evaluated_universe": list(getattr(seam, "evaluated_universe", ())),
        },
        "candidates": [_signal_fields(c) for c in output.candidates],
        "held_signals": [_signal_fields(h) for h in output.held_signals],
        "receipt": (
            output.receipt.to_dict()
            if output.receipt is not None and hasattr(output.receipt, "to_dict")
            else None
        ),
        "source_closure": output.source_closure,
        "stage_metrics": list(output.stage_metrics),
        "bar_context_sessions": window_info["bar_context_sessions"],
        "window_first": window_info["window_first"],
        "window_last": window_info["window_last"],
        "delivered": window_info["delivered"],
        "entry_decisions": [
            {
                "security_code": d.security_code,
                "event_id": d.event_id,
                "status": (
                    d.disposition.value if hasattr(d.disposition, "value") else str(d.disposition)
                ),
            }
            for d in seam.decisions
        ],
    }


def run_replay_request(
    raw_request: Mapping[str, Any],
    *,
    run_daily_advice_from_history: Callable[..., Any],
    runner: Any,
    resources: Any,
    card: Any,
    split_policy: Any,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    clock: Callable[[], datetime] | None = None,
    observed_clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Validate the request once, then run every declared market_date."""
    request = load_replay_request(raw_request)
    days = [
        run_replay_day(
            request,
            market_date,
            run_daily_advice_from_history=run_daily_advice_from_history,
            runner=runner,
            resources=resources,
            card=card,
            split_policy=split_policy,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
            clock=clock,
            observed_clock=observed_clock,
        )
        for market_date in request.market_dates
    ]
    return {
        "schema_version": "p11.paper-replay-run.v1",
        "scenario_id": request.scenario_id,
        "request_digest": request.request_digest,
        "input_grade": INPUT_GRADE,
        "ledger_namespace": request.ledger_namespace,
        "days": days,
    }


def preview_replay_plan(raw_request: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata-only replay plan P12 may call BEFORE reading any physical raw.

    Validates the request and reports the per-day execution/display and
    exclusion/coverage plan: which bound sessions become signal days, which
    securities are excluded or coverage-isolated on which days, and the
    authoritative bar window for each T.  It binds only declared identities
    and never reads the physical Base or market rows; a caller re-verifies
    the returned identities against its own frozen manifests on receipt.
    """
    request = load_replay_request(raw_request)
    days: list[dict[str, Any]] = []
    for market_date in request.market_dates:
        if not _has_next_session(request, market_date):
            days.append(
                {
                    "market_date": market_date.isoformat(),
                    "kind": "final_display_only",
                    "next_session": None,
                }
            )
            continue
        excluded = _excluded_codes(request, market_date)
        window = bar_context_window(request.open_sessions, market_date)
        coverage_blocked = [
            code
            for code, sec_o in (
                (
                    _text(
                        _obj(s, "securities[]").get("identity", {}).get("listing_code"),
                        "securities[].identity.listing_code",
                    ),
                    _obj(s, "securities[]"),
                )
                for s in request.securities
            )
            if code not in excluded and not _action_coverage_ok(request, sec_o, code, market_date)
        ]
        days.append(
            {
                "market_date": market_date.isoformat(),
                "kind": "signal_day",
                "next_session": _next_open_session(request.open_sessions, market_date).isoformat(),
                "window_first": window[0].isoformat(),
                "window_last": window[-1].isoformat(),
                "bar_context_sessions": len(window),
                "excluded_security_codes": sorted(excluded),
                "coverage_isolated_codes": sorted(coverage_blocked),
            }
        )
    return {
        "schema_version": "p11.paper-replay-plan.v1",
        "scenario_id": request.scenario_id,
        "request_digest": request.request_digest,
        "package_id": request.package_id,
        "manifest_sha256": request.manifest_sha256,
        "ledger_namespace": request.ledger_namespace,
        "signal_window": (
            [request.signal_window[0].isoformat(), request.signal_window[1].isoformat()]
            if request.signal_window is not None
            else None
        ),
        "open_sessions": [d.isoformat() for d in request.open_sessions],
        "days": days,
        "declared_venues": sorted(
            {
                _text(_obj(r, "listing_rows[]").get("venue_id"), "listing_rows[].venue_id")
                for r in request.listing_rows
            }
        ),
    }
