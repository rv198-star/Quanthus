'''Read the CN02 base24 daily-cycle pointer and qualify it for P12 consumption.

The daily advice job reads the live pointer
CURRENT_DAILY_CORE_BASE24.json (kind cn02_base24_daily_cycle_v1,
selected_scope=core) - the generic CURRENT_DAILY_CORE.json no longer exists
and no new collection pipeline is built.  Qualification checks the pointer
kind, selected scope, entry.core manifest sha, the DATES index sha, the bound
parent Base manifest sha, the target market date and the entry cutoff; any
mismatch is a whole-day block recorded in the run receipt, never a package.

Only the 1d payload is admitted (projection/payload/1d.parquet and the
declared core tables); 5m is explicitly excluded.  consumer_admission stays
NOT_GRANTED verbatim - consumer use acceptance is a separate gate P12 does
not rewrite.  The receipt records what P12 actually read and when, never a
file mtime as a publish time.
'''

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quanthus.shared.fact_contracts import FactError

EXCHANGE_TZ = timezone(timedelta(hours=8))
EXPECTED_POINTER_KIND = "cn02_base24_daily_cycle_v1"
EXPECTED_SCOPE = "core"
EXPECTED_PRICE_BASIS = "none"
EXPECTED_INTERVAL = "1d"
POINTER_NAME = "CURRENT_DAILY_CORE_BASE24.json"
INDEX_NAME = "DATES.json"

INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
STALE_MARKET_INPUT = "STALE_MARKET_INPUT"

# Default pointer location on CN02; deployments may override via config or
# --pointer-path, and tests inject a local replay root.
DEFAULT_POINTER_PATH = Path(
    "/srv/quanthus-base22-data/jobs/cn02-standard-daily-base251-a5/"
    "CURRENT_DAILY_CORE_BASE24.json"
)


@dataclass(frozen=True)
class InputBlock:
    '''A named reason the daily input cannot feed advice; never a silent empty frame.'''

    reason_code: str
    detail: str
    checked_at: datetime
    expected_market_date: date | None
    observed_market_date: date | None
    missing_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyCoreSnapshot:
    '''Verified daily input: pointer identity, core manifest digest and payloads.'''

    pointer_path: Path
    core_root: Path
    package_id: str
    market_date: date
    core_manifest_sha256: str
    index_sha256: str
    base_manifest_sha256: str
    content_digest: str
    row_counts: dict[str, int]
    daily_payload: Path | None
    # Source knowledge_cutoff carried verbatim; null with a reason when the
    # input does not declare it.  Never inferred from completed_at.
    knowledge_cutoff: datetime | None
    # The job's own completion time, recorded on the receipt only; never
    # used as a knowledge or decision cutoff.
    completed_at: datetime | None
    consumer_admission: str
    checked_at: datetime
    authoritative_next_session: date | None = None
    history_gates: tuple = ()
    corporate_actions: dict | None = None
    # M2 v3: the volume member's scope and its declared full-day observation
    # end are carried verbatim - never inferred, never rewritten.  An UNKNOWN
    # scope stays UNKNOWN (it is never promoted to FULL_DAY), and a declared
    # FULL_DAY whose observation end is absent keeps FULL_DAY verbatim while
    # separately recording the member's contract breach and evidence gap.
    # The rule judges the value; P12 only records it on the receipt.
    volume_scope: str = "UNKNOWN"
    full_day_volume_amount_observation_end: datetime | None = None
    # Audit R1: a declared FULL_DAY without its observation_end is a member
    # contract breach - the scope value is preserved verbatim and the breach
    # is recorded here, never silently degraded to UNKNOWN.
    volume_member_breach: str | None = None
    # Audit R5: the raw security codes actually read from the verified 1d
    # payload (the listing facts the port consumes).  Empty when the payload
    # cannot be read; the runner may only evaluate what is actually present.
    listed_securities: tuple[str, ...] = ()
    # Audit R5 (r2): the verified 1d payload MUST carry a security-code
    # column; a payload without one is a block, not an empty universe.
    listed_securities_status: str = "UNKNOWN"
    # Issue #743 (r2): the forward calendar's BSE venue unavailability,
    # carried verbatim for the rule - drops BSE new candidates, alerts BSE
    # holdings, never a whole-day block.
    bse_unavailable: str | None = None


VOLUME_SCOPE_FULL_DAY = "FULL_DAY"
VOLUME_SCOPE_REGULAR_ONLY = "REGULAR_ONLY"
VOLUME_SCOPE_UNKNOWN = "UNKNOWN"
VOLUME_SCOPES = (
    VOLUME_SCOPE_FULL_DAY,
    VOLUME_SCOPE_REGULAR_ONLY,
    VOLUME_SCOPE_UNKNOWN,
)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactError("INPUT_UNAVAILABLE", f"daily pointer unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise FactError("INPUT_UNAVAILABLE", "daily pointer is not a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def qualify_daily_input(
    pointer_path: Path,
    *,
    expected_market_date: date | None = None,
    checked_at: datetime | None = None,
) -> DailyCoreSnapshot | InputBlock:
    '''Verify the base24 daily pointer and its bound members; block on any drift.'''

    now = checked_at or datetime.now(EXCHANGE_TZ)

    def block(reason: str, detail: str, missing: tuple[str, ...] = ()) -> InputBlock:
        return InputBlock(
            reason_code=reason,
            detail=detail,
            checked_at=now,
            expected_market_date=expected_market_date,
            observed_market_date=None,
            missing_members=missing,
        )

    pointer_path = Path(pointer_path)
    if not pointer_path.is_file():
        return block(INPUT_UNAVAILABLE, f"daily pointer missing: {pointer_path}", (POINTER_NAME,))
    try:
        pointer = _read_json(pointer_path)
    except FactError as exc:
        return block(INPUT_UNAVAILABLE, str(exc), (POINTER_NAME,))

    if pointer.get("kind") != EXPECTED_POINTER_KIND:
        return block(
            INPUT_UNAVAILABLE,
            "pointer kind {!r} is not {!r}".format(pointer.get("kind"), EXPECTED_POINTER_KIND),
        )
    if pointer.get("selected_scope") != EXPECTED_SCOPE:
        return block(
            INPUT_UNAVAILABLE,
            "pointer selected_scope {!r} is not {!r}".format(
                pointer.get("selected_scope"), EXPECTED_SCOPE
            ),
        )
    entry = pointer.get("entry")
    if not isinstance(entry, dict):
        return block(INPUT_UNAVAILABLE, "pointer entry missing")
    if entry.get("kind") != EXPECTED_POINTER_KIND:
        return block(
            INPUT_UNAVAILABLE,
            "entry kind {!r} is not {!r}".format(entry.get("kind"), EXPECTED_POINTER_KIND),
        )
    for field in ("core", "core_manifest_sha256", "base_manifest_sha256", "market_date"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            return block(INPUT_UNAVAILABLE, f"pointer entry field {field} missing")
    index_sha = pointer.get("index_sha256")
    if not isinstance(index_sha, str) or not index_sha.startswith("sha256:"):
        return block(INPUT_UNAVAILABLE, "pointer index_sha256 missing")
    consumer_admission = str(pointer.get("consumer_admission") or "NOT_GRANTED")

    observed = date.fromisoformat(str(entry["market_date"]))
    pointer_day = pointer.get("market_date")
    if pointer_day != entry["market_date"]:
        return block(
            INPUT_UNAVAILABLE,
            "pointer market_date {!r} differs from entry market_date {!r}".format(
                pointer_day, entry["market_date"]
            ),
        )
    if expected_market_date is not None and observed != expected_market_date:
        return InputBlock(
            reason_code=(
                STALE_MARKET_INPUT if observed < expected_market_date else INPUT_UNAVAILABLE
            ),
            detail=(
                f"daily input market_date {observed.isoformat()} does not match the requested "
                f"session {expected_market_date.isoformat()}"
            ),
            checked_at=now,
            expected_market_date=expected_market_date,
            observed_market_date=observed,
            missing_members=("market_date",),
        )

    # The index file the pointer binds must hash to index_sha256.
    job_root = pointer_path.parent
    index_path = job_root / INDEX_NAME
    if not index_path.is_file():
        return block(INPUT_UNAVAILABLE, f"daily index missing: {index_path}", (INDEX_NAME,))
    if _sha256_file(index_path) != index_sha:
        return block(
            INPUT_UNAVAILABLE,
            "daily index sha256 differs from the pointer's index_sha256",
            (INDEX_NAME,),
        )

    # The core directory the entry names must stay under the job root, its
    # manifest must hash to entry.core_manifest_sha256, and the manifest's
    # declared base must match entry.base_manifest_sha256.
    core_root = Path(str(entry["core"])).resolve()
    try:
        core_root.relative_to(job_root.resolve())
    except ValueError:
        return block(INPUT_UNAVAILABLE, "entry.core escapes the job root")
    manifest_path = core_root / "manifest.json"
    if not manifest_path.is_file():
        return block(
            INPUT_UNAVAILABLE,
            f"core manifest missing: {manifest_path}",
            ("core/manifest.json",),
        )
    core_sha = _sha256_file(manifest_path)
    if core_sha != entry["core_manifest_sha256"]:
        return block(
            INPUT_UNAVAILABLE,
            "core manifest sha256 differs from entry.core_manifest_sha256",
            ("core/manifest.json",),
        )
    manifest = _read_json(manifest_path)
    base_sha = (manifest.get("base") or {}).get("manifest_sha256")
    if base_sha != entry["base_manifest_sha256"]:
        return block(
            INPUT_UNAVAILABLE,
            "core base manifest sha256 differs from entry.base_manifest_sha256",
            ("base_manifest_sha256",),
        )
    if manifest.get("market_date") != entry["market_date"]:
        return block(
            INPUT_UNAVAILABLE,
            "core manifest market_date {!r} differs from the entry".format(
                manifest.get("market_date")
            ),
        )

    # Audit R4: re-hash every member file the manifest declares, not only the
    # descriptor fields.  A consumed member whose bytes no longer match its
    # declared sha256 blocks the run.  The parent Base manifest is re-hashed
    # from its own file, never trusted by description alone.  Audit R4 (r2):
    # a REQUIRED member that declares no sha256 blocks - skipping it would
    # publish over unverified bytes.  Optional members without a declared
    # digest are left unread (never consumed).
    required_member_paths = {
        "payload/1d.parquet",
    }
    for member in manifest.get("files") or []:
        rel = str(member.get("path") or "")
        declared = str(member.get("sha256") or "")
        if not rel:
            continue
        if not declared.startswith("sha256:"):
            if rel in required_member_paths:
                return block(
                    INPUT_UNAVAILABLE,
                    "required manifest member " + repr(rel)
                    + " declares no sha256; its bytes cannot be verified",
                    ("core/" + rel,),
                )
            continue
        candidate = (core_root / rel).resolve()
        try:
            candidate.relative_to(core_root)
        except ValueError:
            return block(
                INPUT_UNAVAILABLE,
                "manifest member " + repr(rel) + " escapes the core root",
                ("core/" + rel,),
            )
        if not candidate.is_file():
            return block(
                INPUT_UNAVAILABLE,
                "manifest member " + repr(rel) + " missing",
                ("core/" + rel,),
            )
        actual = _sha256_file(candidate)
        if actual != declared:
            return block(
                INPUT_UNAVAILABLE,
                "manifest member " + repr(rel) + " sha256 differs from the declared digest",
                ("core/" + rel,),
            )
    # Re-hash the parent Base manifest.  Audit R4 (r2): the manifest MUST
    # name the base manifest's file so its declared sha can be recomputed on
    # the bytes - a description-only base reference that cannot be located
    # blocks the run rather than skipping verification.
    base_member = manifest.get("base") or {}
    base_ref = base_member.get("path") or base_member.get("manifest")
    if not isinstance(base_ref, str) or not base_ref:
        return block(
            INPUT_UNAVAILABLE,
            "core manifest declares a base manifest sha but no file location "
            "to recompute it from",
            ("base_manifest",),
        )
    base_path = Path(base_ref)
    if not base_path.is_absolute():
        base_path = (core_root / base_ref).resolve()
    if not base_path.is_file():
        return block(
            INPUT_UNAVAILABLE,
            "parent base manifest missing: " + str(base_path),
            ("base_manifest",),
        )
    if _sha256_file(base_path) != str(base_member.get("manifest_sha256") or ""):
        return block(
            INPUT_UNAVAILABLE,
            "parent base manifest sha256 differs from the declared digest",
            ("base_manifest",),
        )

    # 1d payload only: find the declared daily parquet and exclude 5m.
    files = manifest.get("files") or []
    daily_payload: Path | None = None
    for member in files:
        rel = str(member.get("path") or "")
        if rel.endswith("payload/1d.parquet") or (
            "dwd_security_bar" in rel and "interval_size=1d" in rel
        ):
            candidate = (core_root / rel).resolve()
            try:
                candidate.relative_to(core_root)
            except ValueError:
                continue
            daily_payload = candidate
            break
    # Audit R5: read the verified 1d payload's security codes so the port
    # receives the real listed-securities facts, not only file metadata.
    # Audit R5 (r2): three outcomes are distinct - PRESENT (codes read),
    # NO_CODE_COLUMN (a readable parquet with no security-code column is a
    # BLOCK, never an open universe), UNREADABLE (pyarrow missing or bytes
    # that cannot parse; the runner treats it as fail-closed too - an
    # unreadable verified member is a contradiction the gate resolves by
    # blocking evaluation, not by trusting nothing).
    listed_securities: tuple[str, ...] = ()
    listed_status = "ABSENT"
    if daily_payload is not None and daily_payload.is_file():
        try:
            import pyarrow.parquet as pq

            frame = pq.read_table(daily_payload)
            listed_status = "NO_CODE_COLUMN"
            for name in ("security_code", "code", "symbol"):
                if name in frame.column_names:
                    listed_securities = tuple(
                        sorted({str(v) for v in frame[name].to_pylist() if v})
                    )
                    listed_status = "PRESENT"
                    break
        except Exception:  # noqa: BLE001 - unreadable payload stays fail-closed
            listed_status = "UNREADABLE"
    row_counts = {
        str(k): int(v)
        for k, v in (entry.get("core_row_counts") or manifest.get("row_counts") or {}).items()
    }
    def _aware_ts(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    # The source knowledge_cutoff is read under its own name and carried
    # verbatim; a missing or non-tz value stays null, never back-filled from
    # completed_at.  completed_at is only the job's completion timestamp.
    cutoff = _aware_ts(manifest.get("knowledge_cutoff") or entry.get("knowledge_cutoff"))
    completed_at = _aware_ts(manifest.get("completed_at") or entry.get("completed_at"))
    # Issue #743 (r2): the manifest calendar.next_session member and the
    # dwd_trading_calendar fallback are RETIRED - next_session comes only
    # from the P10 forward-calendar product (forward_calendar.py).  The
    # snapshot no longer carries a calendar answer.
    authoritative_next_session = None
    history_gates = _history_gates(manifest.get("history"))
    corporate_actions = manifest.get("corporate_actions")
    if corporate_actions is not None and not isinstance(corporate_actions, dict):
        corporate_actions = None
    # M2 v3: read the volume member verbatim from the manifest (or entry).
    # volume_scope is one of FULL_DAY / REGULAR_ONLY / UNKNOWN; any other or
    # absent value reads as UNKNOWN and is never promoted.  The declared
    # full-day observation end is carried as its own tz-aware value; a missing
    # end keeps the scope UNKNOWN - P12 never derives completeness from 15:00.
    volume_member = (
        manifest.get("volume") if isinstance(manifest.get("volume"), dict)
        else entry.get("volume") if isinstance(entry.get("volume"), dict)
        else {}
    )
    volume_scope_raw = str(volume_member.get("volume_scope") or "UNKNOWN")
    volume_scope = volume_scope_raw if volume_scope_raw in VOLUME_SCOPES else "UNKNOWN"
    volume_end = _aware_ts(
        volume_member.get("full_day_volume_amount_observation_end")
    )
    # Audit R1: preserve the source's volume_scope verbatim.  A declared
    # FULL_DAY with a missing observation_end is a member contract breach and
    # evidence gap - recorded, never rewritten to UNKNOWN and never proven by
    # the 15:30 session anchor.  The rule then drops the affected candidate or,
    # when the member is a hard prerequisite, blocks the day.
    volume_member_breach = None
    if volume_scope == VOLUME_SCOPE_FULL_DAY and volume_end is None:
        volume_member_breach = (
            "volume member declared FULL_DAY but full_day_volume_amount_observation_end "
            "is absent; the full-day volume claim is unproven (member contract breach)"
        )
    return DailyCoreSnapshot(
        pointer_path=pointer_path,
        core_root=core_root,
        package_id=str(manifest.get("package_id") or entry.get("package_id") or "daily-core"),
        market_date=observed,
        core_manifest_sha256=core_sha,
        index_sha256=index_sha,
        base_manifest_sha256=str(entry["base_manifest_sha256"]),
        content_digest=str(manifest.get("content_digest") or ""),
        row_counts=row_counts,
        daily_payload=daily_payload,
        knowledge_cutoff=cutoff,
        completed_at=completed_at,
        consumer_admission=consumer_admission,
        checked_at=now,
        authoritative_next_session=authoritative_next_session,
        history_gates=history_gates,
        corporate_actions=corporate_actions,
        volume_scope=volume_scope,
        full_day_volume_amount_observation_end=volume_end,
        volume_member_breach=volume_member_breach,
        listed_securities=listed_securities,
        listed_securities_status=listed_status,
    )


HISTORY_GATE_EVENT_WINDOW = "history.event_window_22"
HISTORY_GATE_FEATURE_WARMUP = "history.feature_warmup_61"
HISTORY_GATE_LISTING_FLOOR = "history.listing_floor_120"


def _history_gates(node) -> tuple:
    # Build the three independent history floors from the manifest member.
    # Each gate is checked on its own; a member that cannot be read yields an
    # unavailable gate with a reason, never a silent pass.
    from quanthus.p12_daily.rule_runner import HistoryGateResult

    node = node if isinstance(node, dict) else {}

    def gate(gate_id, key, required):
        raw = node.get(key)
        if raw is None:
            return HistoryGateResult(
                gate_id=gate_id,
                satisfied=False,
                observed_sessions=None,
                required_sessions=required,
                unavailable_reason="history member " + repr(key) + " absent from manifest",
            )
        try:
            observed = int(raw.get("observed_sessions", raw))
        except (TypeError, ValueError, AttributeError):
            return HistoryGateResult(
                gate_id=gate_id,
                satisfied=False,
                observed_sessions=None,
                required_sessions=required,
                unavailable_reason="history member " + repr(key) + " not an integer count",
            )
        reason = raw.get("unavailable_reason") if isinstance(raw, dict) else None
        satisfied = observed >= required and not reason
        return HistoryGateResult(
            gate_id=gate_id,
            satisfied=satisfied,
            observed_sessions=observed,
            required_sessions=required,
            unavailable_reason=reason or (None if satisfied else "observed below required floor"),
        )

    return (
        gate(HISTORY_GATE_EVENT_WINDOW, "event_window_sessions", 22),
        gate(HISTORY_GATE_FEATURE_WARMUP, "feature_warmup_sessions", 61),
        gate(HISTORY_GATE_LISTING_FLOOR, "listing_sessions", 120),
    )
