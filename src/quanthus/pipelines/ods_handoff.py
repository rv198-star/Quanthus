"""Read-only, readiness-gated selection and transfer of immutable ODS releases."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class OdsHandoffError(RuntimeError):
    """Raised when an ODS handoff source cannot be selected or transferred safely."""


_SOURCE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_MARKET_CALENDAR_ARTIFACT = (
    "trading_calendar",
    "artifacts/ref_trading_calendar.parquet",
)


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OdsHandoffError(f"{key} must be a non-empty string")
    return value


def _safe_remote_path(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if not value or ".." in path.parts or path == PurePosixPath("."):
        raise OdsHandoffError(f"{field} must be a safe remote path")
    return path.as_posix().rstrip("/")


@dataclass(frozen=True)
class OdsSource:
    source_id: str
    priority: int
    selection: str
    key_environment: str
    known_hosts_environment: str
    host_environment: str
    port_environment: str
    user_environment: str
    release_root_environment: str
    release_root_default: str
    transfer_sla_basis: str | None
    observed_transfer_max_duration_seconds: float | None
    transfer_sla_seconds: int | None


@dataclass(frozen=True)
class OdsSourceConfig:
    source_path: Path
    sources: tuple[OdsSource, ...]


@dataclass(frozen=True)
class ResolvedOdsSource:
    source: OdsSource
    key_path: Path
    known_hosts: Path
    host: str
    port: int
    user: str
    release_root: str


@dataclass(frozen=True)
class OdsHandoffSelection:
    source_id: str
    schedule: str
    market_date: str
    selection: str
    attempt_id: str | None
    selected_at: datetime

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "source_id": self.source_id,
            "schedule": self.schedule,
            "market_date": self.market_date,
            "selection": self.selection,
            "selected_at": self.selected_at.isoformat(),
        }
        if self.attempt_id is not None:
            payload["attempt_id"] = self.attempt_id
        return payload


def load_ods_source_config(path: Path) -> OdsSourceConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsHandoffError(f"cannot load ODS handoff source config: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise OdsHandoffError("unsupported ODS handoff source config schema_version")
    values = payload.get("sources")
    if not isinstance(values, list) or not values:
        raise OdsHandoffError("ODS handoff sources must be a non-empty list")

    sources: list[OdsSource] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise OdsHandoffError("each ODS handoff source must be an object")
        source_id = _required_string(value, "id")
        if _SOURCE_ID.fullmatch(source_id) is None:
            raise OdsHandoffError("ODS handoff source id is unsafe")
        priority = value.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise OdsHandoffError(f"source {source_id} priority must be a non-negative integer")
        selection = _required_string(value, "selection")
        if selection not in {"current_or_flat", "latest_ready_attempt"}:
            raise OdsHandoffError(f"source {source_id} has an unsupported selection mode")
        connection = value.get("connection")
        if not isinstance(connection, Mapping):
            raise OdsHandoffError(f"source {source_id} connection must be an object")
        release_root = value.get("release_root")
        if not isinstance(release_root, Mapping):
            raise OdsHandoffError(f"source {source_id} release_root must be an object")
        transfer_sla = value.get("transfer_sla")
        observed_transfer_max_duration_seconds: float | None = None
        transfer_sla_seconds: int | None = None
        transfer_sla_basis: str | None = None
        if transfer_sla is not None:
            if not isinstance(transfer_sla, Mapping):
                raise OdsHandoffError(f"source {source_id} transfer_sla must be an object")
            observed = transfer_sla.get("observed_max_duration_seconds")
            if (
                not isinstance(observed, (int, float))
                or isinstance(observed, bool)
                or not math.isfinite(observed)
                or observed <= 0
            ):
                raise OdsHandoffError(
                    f"source {source_id} transfer_sla observed_max_duration_seconds is invalid"
                )
            if transfer_sla.get("safety_margin_percent") != 100:
                raise OdsHandoffError(
                    f"source {source_id} transfer_sla must use a 100% margin"
                )
            transfer_sla_basis = _required_string(transfer_sla, "basis")
            observed_transfer_max_duration_seconds = float(observed)
            transfer_sla_seconds = math.ceil(observed_transfer_max_duration_seconds * 2)
        sources.append(
            OdsSource(
                source_id=source_id,
                priority=priority,
                selection=selection,
                key_environment=_required_string(connection, "key_environment"),
                known_hosts_environment=_required_string(connection, "known_hosts_environment"),
                host_environment=_required_string(connection, "host_environment"),
                port_environment=_required_string(connection, "port_environment"),
                user_environment=_required_string(connection, "user_environment"),
                release_root_environment=_required_string(release_root, "environment"),
                release_root_default=_safe_remote_path(
                    _required_string(release_root, "default"),
                    field=f"source {source_id} release_root.default",
                ),
                transfer_sla_basis=transfer_sla_basis,
                observed_transfer_max_duration_seconds=observed_transfer_max_duration_seconds,
                transfer_sla_seconds=transfer_sla_seconds,
            )
        )
    if len({source.source_id for source in sources}) != len(sources):
        raise OdsHandoffError("ODS handoff source ids must be unique")
    if len({source.priority for source in sources}) != len(sources):
        raise OdsHandoffError("ODS handoff source priorities must be unique")
    return OdsSourceConfig(
        source_path=path.resolve(),
        sources=tuple(sorted(sources, key=lambda source: source.priority)),
    )


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value.strip():
        raise OdsHandoffError(f"set {name}")
    return value


def resolve_ods_source(source: OdsSource) -> ResolvedOdsSource:
    key_path = Path(_environment(source.key_environment)).expanduser()
    known_hosts = Path(_environment(source.known_hosts_environment)).expanduser()
    if not key_path.is_file() or not known_hosts.is_file():
        raise OdsHandoffError(f"source {source.source_id} key or known_hosts file is missing")
    host = _environment(source.host_environment)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        raise OdsHandoffError(f"source {source.source_id} host is unsafe")
    port_value = _environment(source.port_environment)
    try:
        port = int(port_value)
    except ValueError as exc:
        raise OdsHandoffError(f"source {source.source_id} port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise OdsHandoffError(f"source {source.source_id} port is out of range")
    user = _environment(source.user_environment)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise OdsHandoffError(f"source {source.source_id} user is unsafe")
    return ResolvedOdsSource(
        source=source,
        key_path=key_path,
        known_hosts=known_hosts,
        host=host,
        port=port,
        user=user,
        release_root=_safe_remote_path(
            _environment(source.release_root_environment, default=source.release_root_default),
            field=f"source {source.source_id} release_root",
        ),
    )


def _ssh_command(source: ResolvedOdsSource) -> str:
    return shlex.join(
        [
            "ssh",
            "-i",
            str(source.key_path),
            "-p",
            str(source.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={source.known_hosts}",
        ]
    )


def _remote(source: ResolvedOdsSource, path: str) -> str:
    return f"{source.user}@{source.host}:{path}"


def _rsync(
    source: ResolvedOdsSource,
    *arguments: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["rsync", "-e", _ssh_command(source), *arguments],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def _current_attempt(path: Path, *, schedule: str, market_date: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsHandoffError(f"invalid Hermes CURRENT.json: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise OdsHandoffError("invalid Hermes CURRENT.json schema_version")
    if payload.get("schedule") != schedule or payload.get("market_date") != market_date:
        raise OdsHandoffError("Hermes CURRENT.json does not match requested schedule/date")
    attempt_id = payload.get("attempt_id")
    manifest_sha256 = payload.get("manifest_sha256")
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise OdsHandoffError("Hermes CURRENT.json has an unsafe attempt_id")
    if not isinstance(manifest_sha256, str) or _SHA256.fullmatch(manifest_sha256) is None:
        raise OdsHandoffError("Hermes CURRENT.json has an invalid manifest_sha256")
    return attempt_id


def _probe_current(source: ResolvedOdsSource, *, schedule: str, market_date: str) -> str | None:
    remote_path = f"{source.release_root}/{schedule}/{market_date}/CURRENT.json"
    with tempfile.TemporaryDirectory(prefix="quanthus-ods-current-") as directory:
        local_path = Path(directory) / "CURRENT.json"
        completed = _rsync(
            source,
            "-a",
            "--timeout=20",
            _remote(source, remote_path),
            str(local_path),
            timeout_seconds=30,
        )
        if completed.returncode:
            return None
        return _current_attempt(local_path, schedule=schedule, market_date=market_date)


def _probe_flat(source: ResolvedOdsSource, *, schedule: str, market_date: str) -> bool:
    remote_root = f"{source.release_root}/{schedule}/{market_date}"
    with tempfile.TemporaryDirectory(prefix="quanthus-ods-ready-") as directory:
        local_root = Path(directory)
        ready = _rsync(
            source,
            "-a",
            "--timeout=20",
            _remote(source, f"{remote_root}/READY"),
            str(local_root / "READY"),
            timeout_seconds=30,
        )
        if ready.returncode:
            return False
        if schedule != "daily":
            return True
        manifest = _rsync(
            source,
            "-a",
            "--timeout=20",
            _remote(source, f"{remote_root}/release.json"),
            str(local_root / "release.json"),
            timeout_seconds=30,
        )
        return manifest.returncode == 0 and _is_market_daily_manifest(
            local_root / "release.json", market_date=market_date
        )


def _is_market_daily_manifest(path: Path, *, market_date: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    if (
        payload.get("schema_version") != 1
        or payload.get("schedule") != "daily"
        or payload.get("market_date") != market_date
        or payload.get("status") != "ready"
    ):
        return False
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    expected_name, expected_path = _MARKET_CALENDAR_ARTIFACT
    return any(
        isinstance(artifact, Mapping)
        and artifact.get("name") == expected_name
        and artifact.get("relative_path") == expected_path
        for artifact in artifacts
    )


def _probe_ready_attempt(
    source: ResolvedOdsSource,
    *,
    schedule: str,
    market_date: str,
    attempt_id: str,
) -> bool:
    remote_root = f"{source.release_root}/{schedule}/{market_date}/{attempt_id}"
    with tempfile.TemporaryDirectory(prefix="quanthus-ods-ready-") as directory:
        local_root = Path(directory)
        ready = _rsync(
            source,
            "-a",
            "--timeout=20",
            _remote(source, f"{remote_root}/READY"),
            str(local_root / "READY"),
            timeout_seconds=30,
        )
        if ready.returncode:
            return False
        if schedule != "daily":
            return True
        manifest = _rsync(
            source,
            "-a",
            "--timeout=20",
            _remote(source, f"{remote_root}/release.json"),
            str(local_root / "release.json"),
            timeout_seconds=30,
        )
        return manifest.returncode == 0 and _is_market_daily_manifest(
            local_root / "release.json", market_date=market_date
        )


def _attempt_directories(
    source: ResolvedOdsSource, *, schedule: str, market_date: str
) -> list[str]:
    remote_path = f"{source.release_root}/{schedule}/{market_date}/"
    completed = _rsync(
        source,
        "-a",
        "--list-only",
        "--timeout=20",
        _remote(source, remote_path),
        timeout_seconds=30,
    )
    if completed.returncode:
        return []
    attempt_ids = []
    for line in completed.stdout.splitlines():
        if not line.startswith("d"):
            continue
        candidate = line.rsplit(maxsplit=1)[-1]
        if _ATTEMPT_ID.fullmatch(candidate) is not None:
            attempt_ids.append(candidate)
    return sorted(set(attempt_ids), reverse=True)


def _probe_latest_ready_attempt(
    source: ResolvedOdsSource, *, schedule: str, market_date: str
) -> str | None:
    for attempt_id in _attempt_directories(source, schedule=schedule, market_date=market_date):
        if _probe_ready_attempt(
            source,
            schedule=schedule,
            market_date=market_date,
            attempt_id=attempt_id,
        ):
            return attempt_id
    return None


def probe_ods_source(
    source: ResolvedOdsSource, *, schedule: str, market_date: str
) -> tuple[str, str | None] | None:
    if source.source.selection == "current_or_flat":
        attempt_id = _probe_current(source, schedule=schedule, market_date=market_date)
        if attempt_id is not None and _probe_ready_attempt(
            source,
            schedule=schedule,
            market_date=market_date,
            attempt_id=attempt_id,
        ):
            return "attempt", attempt_id
        attempt_id = _probe_latest_ready_attempt(
            source, schedule=schedule, market_date=market_date
        )
        if attempt_id is not None:
            return "attempt", attempt_id
        if _probe_flat(source, schedule=schedule, market_date=market_date):
            return "flat", None
        return None
    attempt_id = _probe_latest_ready_attempt(source, schedule=schedule, market_date=market_date)
    return ("attempt", attempt_id) if attempt_id is not None else None


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def wait_for_ods_ready(
    *,
    source_config: OdsSourceConfig,
    schedule: str,
    market_date: str,
    selection_path: Path,
    timeout_seconds: int,
    poll_seconds: int,
) -> OdsHandoffSelection:
    if schedule not in {"daily", "weekly"}:
        raise OdsHandoffError("schedule must be daily or weekly")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", market_date):
        raise OdsHandoffError("market_date must use YYYY-MM-DD")
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise OdsHandoffError("timeout_seconds and poll_seconds must be positive")
    deadline = time.monotonic() + timeout_seconds
    unavailable: dict[str, str] = {}
    while time.monotonic() < deadline:
        for configured in source_config.sources:
            try:
                source = resolve_ods_source(configured)
            except OdsHandoffError as exc:
                unavailable[configured.source_id] = str(exc)
                continue
            try:
                candidate = probe_ods_source(source, schedule=schedule, market_date=market_date)
            except (OdsHandoffError, subprocess.TimeoutExpired) as exc:
                unavailable[configured.source_id] = str(exc)
                continue
            if candidate is None:
                continue
            selection, attempt_id = candidate
            result = OdsHandoffSelection(
                source_id=source.source.source_id,
                schedule=schedule,
                market_date=market_date,
                selection=selection,
                attempt_id=attempt_id,
                selected_at=datetime.now(timezone.utc),
            )
            _atomic_json(selection_path, result.to_dict())
            return result
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
    details = "; ".join(
        f"{source_id}: {message}" for source_id, message in sorted(unavailable.items())
    )
    suffix = f" ({details})" if details else ""
    raise OdsHandoffError(f"no ODS source became ready within {timeout_seconds}s{suffix}")


def _load_selection(path: Path, *, schedule: str, market_date: str) -> OdsHandoffSelection:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsHandoffError(f"cannot load ODS handoff selection: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise OdsHandoffError("unsupported ODS handoff selection schema_version")
    if payload.get("schedule") != schedule or payload.get("market_date") != market_date:
        raise OdsHandoffError("ODS handoff selection does not match requested schedule/date")
    source_id = payload.get("source_id")
    selection = payload.get("selection")
    if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
        raise OdsHandoffError("ODS handoff selection has an unsafe source_id")
    if selection not in {"attempt", "flat"}:
        raise OdsHandoffError("ODS handoff selection has an unsupported selection")
    attempt_id = payload.get("attempt_id")
    if selection == "attempt":
        if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise OdsHandoffError("ODS handoff selection has an unsafe attempt_id")
    elif attempt_id is not None:
        raise OdsHandoffError("flat ODS handoff selection must not include an attempt_id")
    selected_at = payload.get("selected_at")
    if not isinstance(selected_at, str):
        raise OdsHandoffError("ODS handoff selection is missing selected_at")
    try:
        parsed_selected_at = datetime.fromisoformat(selected_at)
    except ValueError as exc:
        raise OdsHandoffError("ODS handoff selection selected_at is invalid") from exc
    if parsed_selected_at.tzinfo is None or parsed_selected_at.utcoffset() is None:
        raise OdsHandoffError("ODS handoff selection selected_at must include a timezone")
    return OdsHandoffSelection(
        source_id=source_id,
        schedule=schedule,
        market_date=market_date,
        selection=selection,
        attempt_id=attempt_id if isinstance(attempt_id, str) else None,
        selected_at=parsed_selected_at,
    )


def pull_selected_ods_release(
    *,
    source_config: OdsSourceConfig,
    schedule: str,
    market_date: str,
    selection_path: Path,
    destination_root: Path,
) -> OdsHandoffSelection:
    selection = _load_selection(selection_path, schedule=schedule, market_date=market_date)
    sources = {source.source_id: source for source in source_config.sources}
    source_config_item = sources.get(selection.source_id)
    if source_config_item is None:
        raise OdsHandoffError("selected ODS source is not in the configured source set")
    source = resolve_ods_source(source_config_item)
    source_path = f"{source.release_root}/{schedule}/{market_date}"
    if selection.attempt_id is not None:
        source_path = f"{source_path}/{selection.attempt_id}"
    destination = destination_root / "releases" / schedule / market_date
    destination.mkdir(parents=True, exist_ok=True)
    remote_root = _remote(source, f"{source_path}/")
    transfer_started = time.monotonic()
    first = _rsync(
        source,
        "-a",
        "--delete-delay",
        "--delay-updates",
        "--partial-dir=.rsync-partial",
        "--timeout=300",
        "--exclude=/READY",
        remote_root,
        f"{destination}/",
        timeout_seconds=330,
    )
    if first.returncode:
        raise OdsHandoffError(
            f"ODS pull from {selection.source_id} failed: {first.stderr.strip()}"
        )
    ready = _rsync(
        source,
        "-a",
        "--delay-updates",
        "--timeout=300",
        _remote(source, f"{source_path}/READY"),
        str(destination / "READY"),
        timeout_seconds=330,
    )
    if ready.returncode:
        raise OdsHandoffError(
            f"ODS READY pull from {selection.source_id} failed: {ready.stderr.strip()}"
        )
    transfer_elapsed_seconds = time.monotonic() - transfer_started
    transfer: dict[str, Any] = {
        "elapsed_seconds": round(transfer_elapsed_seconds, 3),
        "sla_qualified": source.source.observed_transfer_max_duration_seconds is not None,
    }
    if source.source.observed_transfer_max_duration_seconds is not None:
        transfer.update(
            {
                "observed_max_duration_seconds": (
                    source.source.observed_transfer_max_duration_seconds
                ),
                "sla_seconds": source.source.transfer_sla_seconds,
                "sla_met": transfer_elapsed_seconds <= (source.source.transfer_sla_seconds or 0),
                "basis": source.source.transfer_sla_basis,
            }
        )
    receipt = {
        **selection.to_dict(),
        "verified_transfer_at": datetime.now(timezone.utc).isoformat(),
        "release_dir": str(destination),
        "transfer": transfer,
    }
    _atomic_json(selection_path, receipt)
    return selection
