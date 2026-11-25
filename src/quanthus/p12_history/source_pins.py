"""Pinned source members for the bounded history reader (issue #749).

Every member the reader may open is declared in the frozen P10 H1
mapping package (BASE-MEMBERS.json for the parent Base,
DAILY-MEMBERS.json for the 2026-11 daily increments).  The reader
resolves a member against these maps, re-hashes its bytes, and refuses
an unmapped or mismatched path - it never guesses a location.

Members carry a member_id (the package-relative member path) so the
same id is used in the raw_history source_members[] list and in each
calendar/listing row's source_member_id back-reference.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from quanthus.p12_history.clocks import VerificationClock, iso

BASE_ROOT = Path(
    "/srv/quanthus-base22-data/build/base25-20261031/work/"
    "paired-base251-adce892e-a1/published/DWD"
)
BASE_PACKAGE_ID = "standard-base-2.5.1-8cd966be87fc"
BASE_VERSION = "2.5.1"
BASE_MANIFEST_SHA256 = (
    "sha256:8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
)
BASE_BUSINESS_THROUGH = "2026-10-31"
# Members are addressed payload/<table>/... under the published DWD root.
BASE_PACKAGE_ROOT = BASE_ROOT

H1_BASE_MEMBERS = Path(
    "/Users/william/Documents/Codex/2026-11-24/"
    "quanthus-p10-h1-history-supply/v1/BASE-MEMBERS.json"
)
H1_DAILY_MEMBERS = Path(
    "/Users/william/Documents/Codex/2026-11-24/"
    "quanthus-p10-h1-history-supply/v1/DAILY-MEMBERS.json"
)

# P1 factor-PIT visibility exception ledger (audit 749-05): the 4,173-row
# ODS member the P1 conditional acceptance preserved as fail-closed.  It
# is NOT a dwd_quality_exception row - a separate canonical ledger that the
# reader projects per security + dependency window with the original keys.
P1_EXCEPTION_MEMBER = Path(
    "/srv/quanthus-base22-data/build/base25-20261031/work/"
    "paired-base251-adce892e-a1/published/ODS/members/base-ods/"
    "payload/canonical/ref_adjustment_factor_pit_exceptions.parquet"
)
P1_EXCEPTION_SHA256 = (
    "sha256:9a2158a55e7e351049f242e66b21a01e96d3cf2af55e7517c0aa4bf507577be2"
)


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            d.update(chunk)
    return "sha256:" + d.hexdigest()


@dataclass(frozen=True)
class ResolvedMember:
    """One named source member, resolved to an absolute path + pinned sha.

    member_id is the package-relative member path, used both as the
    raw_history source_members[] key and as calendar/listing rows'
    source_member_id back-reference.
    """

    member_id: str  # package-relative member path
    package_id: str
    version: str
    abs_path: Path
    sha256: str
    size_bytes: int
    readability: str
    package_kind: str  # "base" | "daily-core"
    market_date: str | None = None  # daily-core only


@dataclass(frozen=True)
class DailyCorePackage:
    """A pinned 2026-11 daily-core package for one OPEN market date."""

    market_date: str
    package_id: str
    manifest_path: Path
    manifest_sha256: str
    members: tuple[ResolvedMember, ...]


class SourceMap:
    """Resolve pinned members from the frozen H1 mapping package."""

    def __init__(
        self,
        base_members_path: Path = H1_BASE_MEMBERS,
        daily_members_path: Path = H1_DAILY_MEMBERS,
        base_package_root: Path = BASE_PACKAGE_ROOT,
        calendar_members_path: Path | None = None,
    ) -> None:
        self.base_package_root = Path(base_package_root)
        self._base_doc = json.loads(Path(base_members_path).read_text())
        self._daily_doc = json.loads(Path(daily_members_path).read_text())
        self._base_tables = self._base_doc.get("tables") or {}
        self._daily_entries = self._daily_doc.get("entries") or {}
        # Optional P10 full-history calendar map (history-calendar-*).
        # When present it OVERRIDES the Base table's calendar members so
        # the reader can prove listing age back to 1990 (the H1 map only
        # pins the 2026 partition).  Format: {"base":[{path,sha256,...}],
        # "daily":[{path,sha256,...}]} with absolute member paths.
        self._calendar_members = None
        if calendar_members_path is not None:
            doc = json.loads(Path(calendar_members_path).read_text())
            self._calendar_members = (
                doc.get("base") or [],
                doc.get("daily") or doc.get("november") or [],
            )

    # ---- Base -----------------------------------------------------------
    def base_members(self, table: str) -> list[ResolvedMember]:
        """All pinned members of one Base table (every partition file)."""
        if table == "dwd_trading_calendar" and self._calendar_members:
            base, daily = self._calendar_members
            out = []
            # Base history partitions are "base"; the 17 November Daily
            # calendar partitions are "daily-core" (audit 749-05) - the
            # coverage counts them under their real package kind, never
            # rolled into the Base total.
            for e in base:
                out.append(
                    ResolvedMember(
                        member_id=str(e["path"]).split("/published/DWD/")[-1]
                        if "/published/DWD/" in str(e["path"])
                        else str(e["path"]),
                        package_id=BASE_PACKAGE_ID,
                        version=BASE_VERSION,
                        abs_path=Path(e["path"]),
                        sha256="sha256:" + str(e["sha256"]).replace("sha256:", ""),
                        size_bytes=int(e.get("size_bytes") or 0),
                        readability="READABLE",
                        package_kind="base",
                        market_date=e.get("target"),
                    )
                )
            for e in daily:
                out.append(
                    ResolvedMember(
                        member_id=str(e["path"]).split("/published/DWD/")[-1]
                        if "/published/DWD/" in str(e["path"])
                        else str(e["path"]),
                        package_id=str(e.get("package_id") or "dwd-daily-calendar"),
                        version=BASE_VERSION,
                        abs_path=Path(e["path"]),
                        sha256="sha256:" + str(e["sha256"]).replace("sha256:", ""),
                        size_bytes=int(e.get("size_bytes") or 0),
                        readability="READABLE",
                        package_kind="daily-core",
                        market_date=e.get("target"),
                    )
                )
            return out
        entry = self._base_tables.get(table) or {}
        out = []
        for tup in entry.get("members") or []:
            rel = tup[0]
            out.append(
                ResolvedMember(
                    member_id=rel,
                    package_id=BASE_PACKAGE_ID,
                    version=BASE_VERSION,
                    abs_path=self.base_package_root / rel,
                    sha256=tup[1],
                    size_bytes=int(tup[2]),
                    readability=tup[3] if len(tup) > 3 else "UNKNOWN",
                    package_kind="base",
                    market_date=None,
                )
            )
        return out

    # ---- Daily core ---------------------------------------------------------
    def daily_open_days(self) -> list[str]:
        return sorted(
            d for d, e in self._daily_entries.items()
            if e.get("session_class") == "OPEN"
        )

    def daily_session_class(self, market_date: str) -> str | None:
        e = self._daily_entries.get(market_date)
        return None if e is None else e.get("session_class")

    def daily_core(self, market_date: str) -> DailyCorePackage | None:
        """The pinned daily-core package for one market date, or None."""
        e = self._daily_entries.get(market_date)
        if e is None:
            return None
        core = e.get("core_manifest") or {}
        manifest_path = core.get("path")
        members = []
        for tup in core.get("declared_members") or []:
            rel = tup[0]
            members.append(
                ResolvedMember(
                    member_id=rel,
                    package_id=core.get("package_id") or "",
                    version=str(core.get("schema_version") or "2"),
                    abs_path=(
                        Path(manifest_path).parent / rel if manifest_path else Path(rel)
                    ),
                    sha256=tup[1],
                    size_bytes=int(tup[2]),
                    readability=tup[3] if len(tup) > 3 else "UNKNOWN",
                    package_kind="daily-core",
                    market_date=market_date,
                )
            )
        return DailyCorePackage(
            market_date=market_date,
            package_id=core.get("package_id") or "",
            manifest_path=Path(manifest_path) if manifest_path else Path(""),
            manifest_sha256=core.get("sha256") or "",
            members=tuple(members),
        )

    def daily_member(
        self, market_date: str, name_fragment: str
    ) -> ResolvedMember | None:
        """One daily-core member whose relative path contains the fragment."""
        pkg = self.daily_core(market_date)
        if pkg is None:
            return None
        for m in pkg.members:
            if name_fragment in m.member_id:
                return m
        return None

    # ---- real member knowledge clocks (audit 749-02) ----------------------
    def daily_knowledge_at(self, market_date: str):
        """The daily package's REAL source knowledge clock.

        The reader never stamps a fabricated day+16:00Z.  The true
        knowledge instant is the producer's index completion - the moment
        the day's core manifest was sealed - falling back to the named
        acquisition warehouse_observed_at.  Returns an aware UTC datetime,
        or None when the package carries no proven clock (a named gap,
        never a guess).
        """
        e = self._daily_entries.get(market_date) or {}
        core = e.get("core_manifest") or {}
        acq = (core.get("acquisition_universe_metadata") or {}).get(
            "warehouse_observed_at"
        )
        from quanthus.p12_history.clocks import parse_ts
        for candidate in (e.get("producer_index_completed_at"), acq):
            ts = parse_ts(candidate)
            if ts is not None:
                return ts
        return None

    def daily_producer_completed_at(self, market_date: str):
        """The producer's index/manifest completion instant for a daily
        package (audit 749 R4 second segment).  Distinct from the
        knowledge clock: production-complete is when the day's core
        manifest was sealed, which may precede the channel-visible
        instant.  Returns an aware UTC datetime or None when the source
        carries no such clock (a named gap, never a guess)."""
        e = self._daily_entries.get(market_date) or {}
        core = e.get("core_manifest") or {}
        acq = (core.get("acquisition_universe_metadata") or {}).get(
            "warehouse_observed_at"
        )
        from quanthus.p12_history.clocks import parse_ts
        for candidate in (e.get("producer_index_completed_at"), acq):
            ts = parse_ts(candidate)
            if ts is not None:
                return ts
        return None

    def daily_visible_at(self, market_date: str):
        """The channel-visible instant for a daily package.

        Equals the package's knowledge clock unless a purpose-bound
        publication/withdrawal chain names a distinct visible instant
        (the October-replay rule consumes the real clock verbatim)."""
        e = self._daily_entries.get(market_date) or {}
        chain = e.get("qualified_publication_or_withdrawal_chain") or {}
        from quanthus.p12_history.clocks import parse_ts
        ts = parse_ts(chain.get("value"))
        if ts is not None:
            return ts
        return self.daily_knowledge_at(market_date)

    # ---- verification ---------------------------------------------------------
    def p1_exception_member(self) -> ResolvedMember:
        """The pinned P1 factor-PIT visibility exception ledger member."""
        return ResolvedMember(
            member_id="ODS/canonical/ref_adjustment_factor_pit_exceptions.parquet",
            package_id="base-ods",
            version="P1",
            abs_path=P1_EXCEPTION_MEMBER,
            sha256=P1_EXCEPTION_SHA256,
            size_bytes=(
                P1_EXCEPTION_MEMBER.stat().st_size
                if P1_EXCEPTION_MEMBER.exists() else 0
            ),
            readability="READABLE",
            package_kind="base",
            market_date=None,
        )

    def verify_member(self, member: ResolvedMember) -> dict:
        """Re-hash a member's bytes and compare to its pinned sha.

        verified_at stamps the instant the hash FINISHED (the member's
        own verification clock), never the run's shared read_at - the
        two clocks stay distinguishable on the consumption closure.

        A member whose bytes cannot be opened at all is recorded as an
        UNREADABLE verification result (matches=False, error named),
        never raised - the caller decides whether that member is a
        shared hard dependency (whole-day block) or an isolated
        per-security fault."""
        hash_started_at = iso(VerificationClock.now())
        try:
            actual = sha256_file(member.abs_path)
        except Exception as exc:  # noqa: BLE001
            return {
                "member_id": member.member_id,
                "path": str(member.abs_path),
                "declared_sha256": member.sha256,
                "actual_sha256": None,
                "matches": False,
                "unreadable": True,
                "error": f"{type(exc).__name__}: {exc}",
                # verified_at stamps the hash START instant (audit 749-05) -
                # the member's own verification clock begins when the bytes
                # are opened for hashing, recorded separately from the
                # hash-finish and run-start clocks.
                "verified_at": hash_started_at,
                "hash_started_at": hash_started_at,
                "size_bytes": None,
            }
        return {
            "member_id": member.member_id,
            "path": str(member.abs_path),
            "declared_sha256": member.sha256,
            "actual_sha256": actual,
            "matches": actual == member.sha256,
            "verified_at": hash_started_at,
            "hash_started_at": hash_started_at,
            "size_bytes": (
                member.abs_path.stat().st_size if member.abs_path.exists() else None
            ),
        }

    def manifest_chain(self) -> list[dict]:
        """Re-hash the fixed summary chain (audit 749-01).

        The reader never trusts a member merely because a map names it -
        the package MANIFEST, the DATES index and each day's standard
        descriptor are themselves re-hashed against the frozen declared
        sha before/while members are read.  Returns one record per
        artifact: {artifact, path, declared_sha256, actual_sha256,
        status, verified_at}.  A link whose bytes are unreadable or whose
        digest does not match is a named chain fault (the caller turns a
        hard link into a whole-day block receipt).
        """
        out = []

        def _probe(artifact, path, declared):
            verified_at = iso(VerificationClock.now())
            if not path:
                out.append({"artifact": artifact, "path": None,
                            "declared_sha256": declared, "actual_sha256": None,
                            "status": "NO_PATH", "verified_at": verified_at})
                return
            try:
                actual = sha256_file(Path(path))
            except Exception as exc:  # noqa: BLE001
                out.append({"artifact": artifact, "path": str(path),
                            "declared_sha256": declared, "actual_sha256": None,
                            "status": "UNREADABLE",
                            "error": f"{type(exc).__name__}: {exc}",
                            "verified_at": verified_at})
                return
            ok = declared is None or actual == declared
            out.append({"artifact": artifact, "path": str(path),
                        "declared_sha256": declared, "actual_sha256": actual,
                        "status": "MATCH" if ok else "SHA_MISMATCH",
                        "verified_at": verified_at})

        _probe("base_manifest", self._base_doc.get("manifest_path"),
               self._base_doc.get("manifest_sha256"))
        for day in self.daily_open_days():
            e = self._daily_entries.get(day) or {}
            desc = e.get("descriptor") or {}
            if isinstance(desc, dict):
                _probe(f"dates_index:{day}", desc.get("path"),
                       desc.get("index_sha256") or desc.get("sha256"))
            sd = e.get("standard_descriptor") or {}
            if isinstance(sd, dict):
                _probe(f"daily_manifest:{day}", sd.get("path"),
                       sd.get("sha256"))
            core = e.get("core_manifest") or {}
            if isinstance(core, dict) and core.get("path"):
                _probe(f"core_manifest:{day}", core.get("path"),
                       core.get("sha256"))
        return out

    def resolve(self, member_id: str) -> ResolvedMember | None:
        """Resolve one member_id back to its pinned ResolvedMember.

        Base members use the package-relative `payload/...` id; daily-core
        members were registered under the absolute path that was actually
        opened, so both forms are matched.  Returns None for an id the
        frozen maps do not cover (a named gap, never a guess).
        """
        for table in self._base_tables:
            for m in self.base_members(table):
                if m.member_id == member_id:
                    return m
        if self._calendar_members:
            for m in self.base_members("dwd_trading_calendar"):
                if m.member_id == member_id:
                    return m
        p1 = self.p1_exception_member()
        if (
            member_id == p1.member_id
            or str(p1.abs_path) == member_id
            or member_id == f"{p1.package_id}::{p1.member_id}"
        ):
            return p1
        for day in self.daily_open_days():
            pkg = self.daily_core(day)
            if pkg is None:
                continue
            for m in pkg.members:
                composite = f"{m.package_id}::{m.member_id}"
                if (
                    m.member_id == member_id
                    or str(m.abs_path) == member_id
                    or composite == member_id
                ):
                    return m
        return None
