"""Append-only persistence for execution events and strategy replay evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quanthus.backtest.contracts import (
    BacktestResult,
    DecisionDisposition,
    DecisionEvent,
    EventType,
    NotificationEvent,
    decimal_text,
)

if TYPE_CHECKING:
    from quanthus.backtest.strategy_replay import StrategyReplayResult

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class AuditIntegrityError(ValueError):
    """Raised when persisted audit evidence fails identity or checksum validation."""


@dataclass(frozen=True)
class PersistedRun:
    storage_manifest: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...]
    path: Path


@dataclass(frozen=True)
class PersistedNotification:
    identity: Mapping[str, Any]
    attempts: tuple[Mapping[str, Any], ...]
    attempt_checksums: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class PersistedDecision:
    event: Mapping[str, Any]
    event_checksum: str
    path: Path


@dataclass(frozen=True)
class PersistedStrategyReplay:
    storage_manifest: Mapping[str, Any]
    replay: Mapping[str, Any]
    run: PersistedRun
    path: Path


def _canonical_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _checksum(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuditIntegrityError(f"{field_name} must be an object")
    return value


def _validate_identity(value: str, field_name: str) -> None:
    if not _IDENTITY.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")


def _write_durable(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_json(payload: bytes, field_name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuditIntegrityError(f"{field_name} is not valid JSON") from exc
    return _mapping(value, field_name)


class AuditEventStore:
    """Publish immutable run streams and append immutable notification attempts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def persist_run(self, result: BacktestResult) -> PersistedRun:
        run_id = result.run.run_id
        _validate_identity(run_id, "run_id")
        event_ids: set[str] = set()
        records: list[bytes] = []
        previous_checksum: str | None = None
        for expected_sequence, event in enumerate(result.events, start=1):
            if event.sequence != expected_sequence:
                raise ValueError("run events must use contiguous one-based sequence numbers")
            if event.event_id in event_ids:
                raise ValueError(f"duplicate event_id: {event.event_id}")
            event_ids.add(event.event_id)
            event_payload = event.to_dict()
            event_checksum = _checksum(_canonical_bytes(event_payload))
            envelope = {
                "event": event_payload,
                "event_checksum": event_checksum,
                "previous_event_checksum": previous_checksum,
            }
            records.append(_canonical_bytes(envelope) + b"\n")
            previous_checksum = event_checksum

        events_payload = b"".join(records)
        storage_manifest: dict[str, Any] = {
            "format_version": 1,
            "run_id": run_id,
            "run_manifest": result.run.manifest(result.result_summary),
            "event_count": len(result.events),
            "event_stream_checksum": _checksum(events_payload),
            "last_event_checksum": previous_checksum,
        }
        manifest_payload = _canonical_bytes(storage_manifest)
        ready_payload = _canonical_bytes(
            {
                "manifest_checksum": _checksum(manifest_payload),
                "event_stream_checksum": storage_manifest["event_stream_checksum"],
            }
        )

        target = self.root / "runs" / run_id
        if target.exists():
            existing = self.load_run(run_id)
            if existing.storage_manifest == storage_manifest and existing.events == tuple(
                event.to_dict() for event in result.events
            ):
                return existing
            raise FileExistsError(f"run_id already exists with different contents: {run_id}")

        runs_root = target.parent
        runs_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=runs_root))
        _write_durable(staging / "manifest.json", manifest_payload)
        _write_durable(staging / "events.jsonl", events_payload)
        _write_durable(staging / "READY", ready_payload)
        _sync_directory(staging)
        try:
            staging.rename(target)
        except FileExistsError:
            existing = self.load_run(run_id)
            if existing.storage_manifest == storage_manifest:
                return existing
            raise
        _sync_directory(runs_root)
        return self.load_run(run_id)

    def load_run(self, run_id: str) -> PersistedRun:
        _validate_identity(run_id, "run_id")
        target = self.root / "runs" / run_id
        try:
            manifest_payload = (target / "manifest.json").read_bytes()
            events_payload = (target / "events.jsonl").read_bytes()
            ready = _load_json((target / "READY").read_bytes(), "READY marker")
        except FileNotFoundError as exc:
            raise KeyError(f"audit run not found or incomplete: {run_id}") from exc

        expected_manifest_checksum = ready.get("manifest_checksum")
        expected_stream_checksum = ready.get("event_stream_checksum")
        if not isinstance(expected_manifest_checksum, str) or not _SHA256.fullmatch(
            expected_manifest_checksum
        ):
            raise AuditIntegrityError("READY manifest_checksum is invalid")
        if not isinstance(expected_stream_checksum, str) or not _SHA256.fullmatch(
            expected_stream_checksum
        ):
            raise AuditIntegrityError("READY event_stream_checksum is invalid")
        if _checksum(manifest_payload) != expected_manifest_checksum:
            raise AuditIntegrityError("run manifest checksum mismatch")
        if _checksum(events_payload) != expected_stream_checksum:
            raise AuditIntegrityError("event stream checksum mismatch")

        manifest = _load_json(manifest_payload, "run storage manifest")
        if manifest.get("run_id") != run_id:
            raise AuditIntegrityError("run manifest identity does not match its path")
        if manifest.get("event_stream_checksum") != expected_stream_checksum:
            raise AuditIntegrityError("run manifest and READY event checksums disagree")

        events: list[Mapping[str, Any]] = []
        previous_checksum: str | None = None
        for expected_sequence, raw_line in enumerate(events_payload.splitlines(), start=1):
            envelope = _load_json(raw_line, f"event envelope {expected_sequence}")
            event = _mapping(envelope.get("event"), f"event {expected_sequence}")
            event_checksum = envelope.get("event_checksum")
            if not isinstance(event_checksum, str) or not _SHA256.fullmatch(event_checksum):
                raise AuditIntegrityError(f"event {expected_sequence} checksum is invalid")
            if event_checksum != _checksum(_canonical_bytes(event)):
                raise AuditIntegrityError(f"event {expected_sequence} checksum mismatch")
            if envelope.get("previous_event_checksum") != previous_checksum:
                raise AuditIntegrityError(f"event {expected_sequence} checksum chain is broken")
            if event.get("sequence") != expected_sequence:
                raise AuditIntegrityError("persisted event sequence is not contiguous")
            events.append(event)
            previous_checksum = event_checksum

        if manifest.get("event_count") != len(events):
            raise AuditIntegrityError("run manifest event_count mismatch")
        if manifest.get("last_event_checksum") != previous_checksum:
            raise AuditIntegrityError("run manifest last_event_checksum mismatch")
        return PersistedRun(storage_manifest=manifest, events=tuple(events), path=target)

    def persist_strategy_replay(self, result: StrategyReplayResult) -> PersistedStrategyReplay:
        """Atomically publish analysis evidence linked to its immutable P2 event stream."""

        persisted_run = self.persist_run(result.backtest)
        replay = result.audit_dict()
        replay_payload = _canonical_bytes(replay)
        storage_manifest: dict[str, Any] = {
            "format_version": 1,
            "run_id": result.backtest.run.run_id,
            "strategy_id": result.strategy_id,
            "strategy_version": result.strategy_version,
            "decision_count": len(result.decisions),
            "equity_point_count": len(result.equity_curve),
            "trade_count": len(result.trades),
            "replay_checksum": _checksum(replay_payload),
            "run_manifest_checksum": _checksum(_canonical_bytes(persisted_run.storage_manifest)),
            "event_stream_checksum": persisted_run.storage_manifest["event_stream_checksum"],
        }
        manifest_payload = _canonical_bytes(storage_manifest)
        ready_payload = _canonical_bytes(
            {
                "manifest_checksum": _checksum(manifest_payload),
                "replay_checksum": storage_manifest["replay_checksum"],
                "event_stream_checksum": storage_manifest["event_stream_checksum"],
            }
        )

        run_id = result.backtest.run.run_id
        _validate_identity(run_id, "run_id")
        target = self.root / "strategy_replays" / run_id
        if target.exists():
            existing = self.load_strategy_replay(run_id)
            if existing.storage_manifest == storage_manifest and existing.replay == replay:
                return existing
            raise FileExistsError(
                f"strategy replay run_id already exists with different contents: {run_id}"
            )

        replays_root = target.parent
        replays_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=replays_root))
        _write_durable(staging / "manifest.json", manifest_payload)
        _write_durable(staging / "replay.json", replay_payload)
        _write_durable(staging / "READY", ready_payload)
        _sync_directory(staging)
        try:
            staging.rename(target)
        except FileExistsError:
            existing = self.load_strategy_replay(run_id)
            if existing.storage_manifest == storage_manifest and existing.replay == replay:
                return existing
            raise
        _sync_directory(replays_root)
        return self.load_strategy_replay(run_id)

    def load_strategy_replay(self, run_id: str) -> PersistedStrategyReplay:
        _validate_identity(run_id, "run_id")
        target = self.root / "strategy_replays" / run_id
        try:
            manifest_payload = (target / "manifest.json").read_bytes()
            replay_payload = (target / "replay.json").read_bytes()
            ready = _load_json((target / "READY").read_bytes(), "strategy replay READY")
        except FileNotFoundError as exc:
            raise KeyError(f"strategy replay not found or incomplete: {run_id}") from exc

        expected_manifest_checksum = ready.get("manifest_checksum")
        expected_replay_checksum = ready.get("replay_checksum")
        expected_event_stream_checksum = ready.get("event_stream_checksum")
        for value, field_name in (
            (expected_manifest_checksum, "manifest_checksum"),
            (expected_replay_checksum, "replay_checksum"),
            (expected_event_stream_checksum, "event_stream_checksum"),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise AuditIntegrityError(f"strategy replay READY {field_name} is invalid")
        if _checksum(manifest_payload) != expected_manifest_checksum:
            raise AuditIntegrityError("strategy replay manifest checksum mismatch")
        if _checksum(replay_payload) != expected_replay_checksum:
            raise AuditIntegrityError("strategy replay payload checksum mismatch")

        manifest = _load_json(manifest_payload, "strategy replay manifest")
        replay = _load_json(replay_payload, "strategy replay payload")
        if manifest.get("run_id") != run_id or replay.get("run_id") != run_id:
            raise AuditIntegrityError("strategy replay identity does not match its path")
        for field_name in ("strategy_id", "strategy_version"):
            if manifest.get(field_name) != replay.get(field_name):
                raise AuditIntegrityError(
                    f"strategy replay {field_name} differs between manifest and payload"
                )
        if manifest.get("replay_checksum") != expected_replay_checksum:
            raise AuditIntegrityError("strategy replay manifest and READY checksums disagree")

        persisted_run = self.load_run(run_id)
        actual_run_manifest_checksum = _checksum(_canonical_bytes(persisted_run.storage_manifest))
        if manifest.get("run_manifest_checksum") != actual_run_manifest_checksum:
            raise AuditIntegrityError("strategy replay references a different run manifest")
        actual_event_stream_checksum = persisted_run.storage_manifest.get("event_stream_checksum")
        if (
            manifest.get("event_stream_checksum") != actual_event_stream_checksum
            or expected_event_stream_checksum != actual_event_stream_checksum
        ):
            raise AuditIntegrityError("strategy replay references a different event stream")

        count_fields = (
            ("decision_count", "decisions"),
            ("equity_point_count", "equity_curve"),
            ("trade_count", "trades"),
        )
        for manifest_field, replay_field in count_fields:
            values = replay.get(replay_field)
            if not isinstance(values, list) or manifest.get(manifest_field) != len(values):
                raise AuditIntegrityError(
                    f"strategy replay {manifest_field} does not match its payload"
                )
        return PersistedStrategyReplay(
            storage_manifest=manifest,
            replay=replay,
            run=persisted_run,
            path=target,
        )

    def persist_notification(self, event: NotificationEvent) -> PersistedNotification:
        _validate_identity(event.notification_id, "notification_id")
        self._validate_notification_signal(event)
        identity = self._notification_identity(event)
        target = self.root / "notifications" / event.notification_id
        if not target.exists():
            if event.attempt != 1:
                raise ValueError("the first notification attempt must be attempt 1")
            notifications_root = target.parent
            notifications_root.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=f".{event.notification_id}.", dir=notifications_root)
            )
            attempts_root = staging / "attempts"
            attempts_root.mkdir()
            identity_payload = _canonical_bytes(identity)
            _write_durable(staging / "identity.json", identity_payload)
            _write_durable(
                staging / "IDENTITY",
                _canonical_bytes({"identity_checksum": _checksum(identity_payload)}),
            )
            _write_durable(
                attempts_root / "000001.json",
                self._notification_envelope(event, previous_checksum=None),
            )
            _sync_directory(attempts_root)
            _sync_directory(staging)
            try:
                staging.rename(target)
            except FileExistsError:
                existing = self.load_notification(event.notification_id)
                return self._resolve_notification_replay(existing, event, identity)
            _sync_directory(notifications_root)
            return self.load_notification(event.notification_id)

        existing = self.load_notification(event.notification_id)
        replay = self._existing_notification_attempt(existing, event, identity)
        if replay is not None:
            return replay
        expected_attempt = len(existing.attempts) + 1
        if event.attempt != expected_attempt:
            raise ValueError(f"next notification attempt must be {expected_attempt}")
        attempts_root = target / "attempts"
        payload = self._notification_envelope(
            event,
            previous_checksum=existing.attempt_checksums[-1],
        )
        descriptor, staging_name = tempfile.mkstemp(
            prefix=f".{event.attempt:06d}.", dir=attempts_root
        )
        staging_path = Path(staging_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            attempt_path = attempts_root / f"{event.attempt:06d}.json"
            os.link(staging_path, attempt_path)
            _sync_directory(attempts_root)
        finally:
            staging_path.unlink(missing_ok=True)
        return self.load_notification(event.notification_id)

    def load_notification(self, notification_id: str) -> PersistedNotification:
        _validate_identity(notification_id, "notification_id")
        target = self.root / "notifications" / notification_id
        try:
            identity_payload = (target / "identity.json").read_bytes()
            identity_marker = _load_json(
                (target / "IDENTITY").read_bytes(), "notification identity marker"
            )
        except FileNotFoundError as exc:
            raise KeyError(f"notification not found or incomplete: {notification_id}") from exc
        if identity_marker.get("identity_checksum") != _checksum(identity_payload):
            raise AuditIntegrityError("notification identity checksum mismatch")
        identity = _load_json(identity_payload, "notification identity")
        if identity.get("notification_id") != notification_id:
            raise AuditIntegrityError("notification identity does not match its path")

        attempts: list[Mapping[str, Any]] = []
        checksums: list[str] = []
        previous_checksum: str | None = None
        attempts_root = target / "attempts"
        try:
            paths = sorted(attempts_root.glob("*.json"))
        except FileNotFoundError as exc:
            raise AuditIntegrityError("notification attempts directory is missing") from exc
        for expected_attempt, path in enumerate(paths, start=1):
            if path.name != f"{expected_attempt:06d}.json":
                raise AuditIntegrityError("notification attempt files are not contiguous")
            envelope = _load_json(path.read_bytes(), f"notification attempt {expected_attempt}")
            persisted_event = _mapping(
                envelope.get("event"), f"notification event {expected_attempt}"
            )
            event_checksum = envelope.get("event_checksum")
            if not isinstance(event_checksum, str) or not _SHA256.fullmatch(event_checksum):
                raise AuditIntegrityError("notification event checksum is invalid")
            if event_checksum != _checksum(_canonical_bytes(persisted_event)):
                raise AuditIntegrityError("notification event checksum mismatch")
            if envelope.get("previous_event_checksum") != previous_checksum:
                raise AuditIntegrityError("notification event checksum chain is broken")
            if persisted_event.get("attempt") != expected_attempt:
                raise AuditIntegrityError("notification attempt payload is not contiguous")
            attempts.append(persisted_event)
            checksums.append(event_checksum)
            previous_checksum = event_checksum
        if not attempts:
            raise AuditIntegrityError("notification must contain at least one attempt")
        return PersistedNotification(
            identity=identity,
            attempts=tuple(attempts),
            attempt_checksums=tuple(checksums),
            path=target,
        )

    def persist_decision(self, event: DecisionEvent) -> PersistedDecision:
        _validate_identity(event.decision_id, "decision_id")
        run = self.load_run(event.run_id)
        if event.disposition is DecisionDisposition.ACCEPTED:
            self._validate_accepted_decision(event, run)
        elif event.linked_order_ids:
            raise ValueError("a rejected or shadow decision cannot link execution orders")

        event_payload = _canonical_bytes(event.to_dict())
        marker_payload = _canonical_bytes({"event_checksum": _checksum(event_payload)})
        target = self.root / "decisions" / event.decision_id
        if target.exists():
            existing = self.load_decision(event.decision_id)
            if existing.event == event.to_dict():
                return existing
            raise FileExistsError(
                f"decision_id already exists with different contents: {event.decision_id}"
            )

        decisions_root = target.parent
        decisions_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{event.decision_id}.", dir=decisions_root))
        _write_durable(staging / "event.json", event_payload)
        _write_durable(staging / "READY", marker_payload)
        _sync_directory(staging)
        try:
            staging.rename(target)
        except FileExistsError:
            existing = self.load_decision(event.decision_id)
            if existing.event == event.to_dict():
                return existing
            raise
        _sync_directory(decisions_root)
        return self.load_decision(event.decision_id)

    def load_decision(self, decision_id: str) -> PersistedDecision:
        _validate_identity(decision_id, "decision_id")
        target = self.root / "decisions" / decision_id
        try:
            event_payload = (target / "event.json").read_bytes()
            marker = _load_json((target / "READY").read_bytes(), "decision READY marker")
        except FileNotFoundError as exc:
            raise KeyError(f"decision not found or incomplete: {decision_id}") from exc
        event_checksum = marker.get("event_checksum")
        if not isinstance(event_checksum, str) or not _SHA256.fullmatch(event_checksum):
            raise AuditIntegrityError("decision event checksum is invalid")
        if event_checksum != _checksum(event_payload):
            raise AuditIntegrityError("decision event checksum mismatch")
        event = _load_json(event_payload, "decision event")
        if event.get("decision_id") != decision_id:
            raise AuditIntegrityError("decision identity does not match its path")
        return PersistedDecision(event=event, event_checksum=event_checksum, path=target)

    def _validate_notification_signal(self, event: NotificationEvent) -> None:
        run = self.load_run(event.run_id)
        matches = [
            persisted
            for persisted in run.events
            if persisted.get("event_type") == EventType.SIGNAL_ACCEPTED.value
            and isinstance(persisted.get("payload"), Mapping)
            and persisted["payload"].get("signal_id") == event.signal_id
        ]
        if len(matches) != 1:
            raise KeyError("notification must reference exactly one persisted signal")
        payload = _mapping(matches[0].get("payload"), "persisted signal payload")
        if (
            payload.get("profile_id") != event.profile_id
            or payload.get("window_id") != event.window_id
        ):
            raise ValueError("notification profile/window must match the persisted signal")

    @staticmethod
    def _validate_accepted_decision(event: DecisionEvent, run: PersistedRun) -> None:
        signals = [
            persisted
            for persisted in run.events
            if persisted.get("event_type") == EventType.SIGNAL_ACCEPTED.value
            and isinstance(persisted.get("payload"), Mapping)
            and persisted["payload"].get("signal_id") == event.signal_id
        ]
        if len(signals) != 1:
            raise KeyError("accepted decision must resolve to exactly one persisted signal")
        signal = _mapping(signals[0].get("payload"), "accepted decision signal")
        expected = {
            "profile_id": event.profile_id,
            "window_id": event.window_id,
            "security_code": event.security_code,
            "unit_id": event.unit_id,
        }
        if any(signal.get(field) != value for field, value in expected.items()):
            raise ValueError("accepted decision identity must match its persisted signal")
        actual_order_ids = tuple(
            str(persisted["payload"]["order_id"])
            for persisted in run.events
            if persisted.get("event_type") == EventType.ORDER_CREATED.value
            and isinstance(persisted.get("payload"), Mapping)
            and persisted["payload"].get("signal_id") == event.signal_id
        )
        if actual_order_ids != event.linked_order_ids:
            raise ValueError("accepted decision linked_order_ids must match persisted orders")

    @staticmethod
    def _notification_identity(event: NotificationEvent) -> dict[str, Any]:
        return {
            "format_version": 1,
            "notification_id": event.notification_id,
            "run_id": event.run_id,
            "signal_id": event.signal_id,
            "profile_id": event.profile_id,
            "window_id": event.window_id,
            "content_hash": event.content_hash,
            "provider": event.provider,
        }

    @staticmethod
    def _notification_envelope(event: NotificationEvent, *, previous_checksum: str | None) -> bytes:
        payload = event.to_dict()
        return _canonical_bytes(
            {
                "event": payload,
                "event_checksum": _checksum(_canonical_bytes(payload)),
                "previous_event_checksum": previous_checksum,
            }
        )

    def _resolve_notification_replay(
        self,
        existing: PersistedNotification,
        event: NotificationEvent,
        identity: Mapping[str, Any],
    ) -> PersistedNotification:
        replay = self._existing_notification_attempt(existing, event, identity)
        if replay is None:
            raise FileExistsError("notification_id already exists with a different first attempt")
        return replay

    @staticmethod
    def _existing_notification_attempt(
        existing: PersistedNotification,
        event: NotificationEvent,
        identity: Mapping[str, Any],
    ) -> PersistedNotification | None:
        if existing.identity != identity:
            raise FileExistsError(
                f"notification_id already exists with different identity: {event.notification_id}"
            )
        if event.attempt <= len(existing.attempts):
            if existing.attempts[event.attempt - 1] == event.to_dict():
                return existing
            raise FileExistsError(
                f"notification attempt already exists with different contents: {event.attempt}"
            )
        return None
