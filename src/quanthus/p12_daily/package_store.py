'''Immutable daily package store, retraction log and run receipts.

Layout under one state directory:

  daily-packages/<market_date>.json    one immutable published package
  daily-packages/retractions.jsonl     append-only retraction records
  daily-runs/<run_id>.json             one run receipt per job attempt

Frozen contract (2026-11-24): a market_date gets at most one package - never
a same-day replacement.  An error discovered after publication appends a
retraction record naming the original digest, reason and time; the source then
marks that package superseded so unconfirmed plans can no longer confirm while
filled facts stay untouched.  Blocked input writes only a run receipt, never
a package.
'''

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quanthus.shared.fact_contracts import FactError

PACKAGES_DIR = "daily-packages"
RETRACTIONS_FILE = "retractions.jsonl"
SKIPS_FILE = "skipped-plans.jsonl"
RUNS_DIR = "daily-runs"
PUBLISHER_REGISTRY_FILE = "publisher-registry.json"
# P12 publish envelope.  v2 (issue #723 first step) carries the same fields as
# v1 plus room for the v2 decision_set; readers admit both so existing v1
# packages keep loading with unchanged bytes and digest.
PACKAGE_SCHEMA = "quanthus.p12.daily-package.v2"
PACKAGE_SCHEMA_V1 = "quanthus.p12.daily-package.v1"
READABLE_PACKAGE_SCHEMAS = frozenset({PACKAGE_SCHEMA, PACKAGE_SCHEMA_V1})
# Inner decision_set schema markers the envelope may carry (issue #723 second
# step).  A v2 inner set may only ship inside the v2 envelope; a v1 envelope
# must never wrap a v2 inner set, and the legacy v1 reader must refuse that
# mix rather than silently drop the new fields.  A v2 envelope may carry a
# bare v1 inner set for back-compat.
DECISION_SET_SCHEMA_V2 = "p12.decision-set.v2"
ENVELOPE_INNER_MISMATCH = "ENVELOPE_INNER_MISMATCH"
RETRACTION_SCHEMA = "quanthus.p12.package-retraction.v1"
RUN_SCHEMA = "quanthus.p12.daily-run.v1"

PACKAGE_EXISTS_DIFFERS = "PACKAGE_EXISTS_DIFFERS"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid():d}.tmp")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactError("INVALID_PACKAGE", f"stored artifact unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise FactError("INVALID_PACKAGE", "stored artifact is not an object")
    return value


@dataclass(frozen=True)
class StoredPackage:
    '''One published package plus its on-disk identity.'''

    market_date: date
    package_id: str
    file_path: Path
    file_sha256: str
    decision_set: dict
    runner: dict
    input_evidence: dict
    published_at: datetime
    retracted: bool
    retraction: dict | None
    # Issue #722 r6-1: the run that produced this package, recorded on the
    # envelope.  None on a pre-provenance (v1-era) package; the workbench then
    # treats exit types as unverifiable.
    published_by_run_id: str | None = None


@dataclass(frozen=True)
class PublishOutcome:
    '''Result of one publish attempt.'''

    status: str
    package: StoredPackage | None
    reason: str | None


class DailyPackageStore:
    '''Append-only package directory the daily job writes and the source reads.'''

    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir)
        self._packages = self._root / PACKAGES_DIR
        self._runs = self._root / RUNS_DIR

    @property
    def packages_dir(self) -> Path:
        return self._packages

    @property
    def runs_dir(self) -> Path:
        return self._runs

    # ---- packages ------------------------------------------------------------
    def package_path(self, market_date: date) -> Path:
        return self._packages / (f"{market_date.isoformat()}.json")

    def _retractions_path(self) -> Path:
        return self._packages / RETRACTIONS_FILE

    def _skips_path(self) -> Path:
        return self._packages / SKIPS_FILE

    # ---- skips (issue #721 R14) ------------------------------------------------
    def skipped_plans(self) -> dict:
        path = self._skips_path()
        if not path.is_file():
            return {}
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = str(record.get("plan_id") or "")
            if pid:
                out[pid] = record
        return out

    def skip_plan(
        self,
        *,
        plan_id: str,
        skipped_at: datetime,
        reason: str | None = None,
        actor: str = "operator",
    ) -> dict:
        record = {
            "schema_version": "quanthus.p12.plan-skip.v1",
            "plan_id": str(plan_id),
            "skipped_at": skipped_at.isoformat(),
            "reason": reason,
            "actor": actor,
        }
        path = self._skips_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record


    def _registry_path(self) -> Path:
        return self._root / PUBLISHER_REGISTRY_FILE

    def publisher_registry(self) -> dict:
        '''The trusted publisher ledger: {(source_id, market_date) -> digest}.

        Audit #720 R13 (r2): registration is APPEND-ONLY per
        (source_id, market_date) - a second day's package must not overwrite
        the first day's digest or a restarted reader could no longer verify
        the older package.  Readers must not serve a package whose
        (source_id, market_date) pair is absent or whose digest differs.'''

        path = self._registry_path()
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out = {}
        for key, value in (raw.get("sources") or {}).items():
            if isinstance(value, dict):
                for market_date, digest_ in value.items():
                    out[(str(key), str(market_date))] = str(digest_)
            else:
                # Legacy single-digest shape: keep it under the wildcard key
                # so a pre-r2 registry still verifies its one package.
                out[(str(key), "*")] = str(value)
        return out

    def register_source(
        self, source_id: str, market_date: date | str, package_digest: str
    ) -> None:
        '''Append one (source_id, market_date) -> digest registration.'''

        path = self._registry_path()
        raw = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        sources = raw.setdefault("sources", {})
        day = str(market_date)
        entry = sources.get(str(source_id))
        if not isinstance(entry, dict):
            entry = {} if entry is None else {"*": str(entry)}
        # Never overwrite a registered digest for the same (source, date).
        existing = entry.get(day)
        if existing is not None and existing != str(package_digest):
            raise FactError(
                "PACKAGE_EXISTS_DIFFERS",
                "publisher registry already binds " + repr(source_id)
                + " " + day + " to a different digest",
            )
        entry[day] = str(package_digest)
        sources[str(source_id)] = entry
        _write_atomic(self._registry_path(), {"sources": sources})

    def retractions(self) -> tuple[dict, ...]:
        path = self._retractions_path()
        if not path.is_file():
            return ()
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return tuple(out)

    def retraction_for(self, market_date: date) -> dict | None:
        found = [
            item
            for item in self.retractions()
            if item.get("market_date") == market_date.isoformat()
        ]
        return found[-1] if found else None

    def _load_package_file(self, path: Path) -> StoredPackage:
        raw = _read_json(path)
        if raw.get("schema_version") not in READABLE_PACKAGE_SCHEMAS:
            raise FactError("INVALID_PACKAGE", "daily package schema differs")
        # Envelope/inner version consistency (issue #723 second step): a v1
        # envelope must never carry a v2 decision_set - the legacy reader
        # would silently drop the new exit fields.  Refuse the mix outright
        # rather than serve a downgraded package.
        inner_schema = (raw.get("decision_set") or {}).get("decision_set_schema")
        if (
            raw["schema_version"] == PACKAGE_SCHEMA_V1
            and inner_schema is not None
            and str(inner_schema) == DECISION_SET_SCHEMA_V2
        ):
            raise FactError(
                ENVELOPE_INNER_MISMATCH,
                "a v1 package envelope cannot carry a v2 decision_set",
            )
        market_date = date.fromisoformat(str(raw["market_date"]))
        retraction = self.retraction_for(market_date)
        return StoredPackage(
            market_date=market_date,
            package_id=str(raw["package_id"]),
            file_path=path,
            file_sha256=_sha256_bytes(path.read_bytes()),
            decision_set=raw["decision_set"],
            runner=raw.get("runner") or {},
            input_evidence=raw.get("input_evidence") or {},
            published_at=datetime.fromisoformat(str(raw["published_at"])),
            retracted=retraction is not None,
            retraction=retraction,
            published_by_run_id=raw.get("published_by_run_id"),
        )

    def packages(self) -> tuple[StoredPackage, ...]:
        if not self._packages.is_dir():
            return ()
        out = []
        for path in sorted(self._packages.glob("*.json")):
            out.append(self._load_package_file(path))
        return tuple(sorted(out, key=lambda item: item.market_date))

    def package_for(self, market_date: date) -> StoredPackage | None:
        path = self.package_path(market_date)
        if not path.is_file():
            return None
        return self._load_package_file(path)

    def publish(
        self,
        *,
        decision_set: dict,
        runner: dict,
        input_evidence: dict,
        published_at: datetime,
        published_by_run_id: str | None = None,
    ) -> PublishOutcome:
        '''Write the one allowed package for a market date; never overwrite.

        A rerun over the identical decision_set replays the existing package
        (idempotent), while different content for the same date is refused -
        errors after publication go through retraction, not replacement.
        '''

        inner_schema = decision_set.get("decision_set_schema")
        if (
            inner_schema is not None
            and str(inner_schema) == DECISION_SET_SCHEMA_V2
            and PACKAGE_SCHEMA != "quanthus.p12.daily-package.v2"
        ):
            raise FactError(
                ENVELOPE_INNER_MISMATCH,
                "a v2 decision_set cannot be wrapped in a "
                f"{PACKAGE_SCHEMA!r} envelope",
            )
        market_date = date.fromisoformat(str(decision_set["market_date"]))
        path = self.package_path(market_date)
        body = {
            "schema_version": PACKAGE_SCHEMA,
            "package_id": f"p12-daily-{market_date.isoformat()}",
            "market_date": market_date.isoformat(),
            "published_at": published_at.isoformat(),
            "runner": runner,
            "input_evidence": input_evidence,
            "decision_set": decision_set,
        }
        if published_by_run_id is not None:
            body["published_by_run_id"] = published_by_run_id
        if path.is_file():
            existing = self._load_package_file(path)
            if existing.decision_set == decision_set:
                return PublishOutcome("already_published", existing, None)
            raise FactError(
                PACKAGE_EXISTS_DIFFERS,
                f"a different package already covers {market_date.isoformat()}; append a "
                "retraction instead of replacing it",
            )
        _write_atomic(path, body)
        stored = self._load_package_file(path)
        # Audit R13: the publish gate writes the trusted publisher ledger.
        # The registered source_id is bound to this package's digest; a
        # package whose source_id is unregistered can never be read back.
        source_id = str(decision_set.get("source_id") or "")
        if source_id:
            self.register_source(
                source_id, market_date, stored.file_sha256
            )
        return PublishOutcome("published", stored, None)

    def retract(
        self,
        *,
        market_date: date,
        reason: str,
        retracted_at: datetime,
        actor: str,
    ) -> dict:
        '''Append one retraction record; the original package stays on disk.'''

        package = self.package_for(market_date)
        if package is None:
            raise FactError("PLAN_NOT_FOUND", f"no published package for {market_date}")
        record = {
            "schema_version": RETRACTION_SCHEMA,
            "market_date": market_date.isoformat(),
            "package_id": package.package_id,
            "package_file_sha256": package.file_sha256,
            "reason": reason,
            "retracted_at": retracted_at.isoformat(),
            "actor": actor,
        }
        path = self._retractions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record

    # ---- run receipts ----------------------------------------------------------
    def write_run(self, receipt: dict) -> Path:
        '''Persist one run receipt under a sortable run id.'''

        if receipt.get("schema_version") != RUN_SCHEMA:
            raise FactError("INVALID_PACKAGE", "run receipt schema differs")
        run_id = str(receipt["run_id"])
        path = self._runs / (f"{run_id}.json")
        _write_atomic(path, receipt)
        return path

    def runs(self) -> tuple[dict, ...]:
        if not self._runs.is_dir():
            return ()
        out = []
        for path in sorted(self._runs.glob("*.json")):
            out.append(_read_json(path))
        return tuple(out)

    def latest_run(self) -> dict | None:
        runs = self.runs()
        return runs[-1] if runs else None

    def latest_run_for_date(self, market_date: date) -> dict | None:
        found = [
            item
            for item in self.runs()
            if item.get("market_date") == market_date.isoformat()
        ]
        return found[-1] if found else None
