"""Read-only Quanthus adapters; domain implementation stays outside the platform."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quanthus.platform.ports import (
    ATTRIBUTION_VERIFICATION_PORT_VERSION,
    GATE_QUERY_PORT_VERSION,
    AttributionVerification,
    GateFacts,
)
from quanthus.policy import ConfigurationError
from quanthus.strategy_attribution_publication import verify_attribution_release

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class QuanthusGateQueryAdapter:
    version = GATE_QUERY_PORT_VERSION

    def __init__(self, gate_path: Path) -> None:
        self._path = gate_path.resolve()

    def read(self) -> GateFacts:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"cannot read platform gate: {self._path}") from exc
        if not isinstance(raw, Mapping):
            raise ConfigurationError("platform gate root must be an object")
        gates_raw = raw.get("gates")
        results_raw = raw.get("acceptance_results")
        if not isinstance(gates_raw, list) or not isinstance(results_raw, Mapping):
            raise ConfigurationError("platform gate facts are incomplete")
        gates: list[dict[str, str]] = []
        for item in gates_raw:
            if not isinstance(item, Mapping):
                raise ConfigurationError("platform gate entry must be an object")
            values = {field: item.get(field) for field in ("id", "name", "status")}
            if any(not isinstance(value, str) or not value for value in values.values()):
                raise ConfigurationError("platform gate entry has invalid identity or status")
            gates.append({field: str(value) for field, value in values.items()})
        results = dict(results_raw)
        if any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in results.items()
        ):
            raise ConfigurationError("platform acceptance results must be string pairs")
        gate_version = raw.get("gate_version")
        current_gate = raw.get("current_gate")
        current_status = raw.get("current_status")
        if any(
            not isinstance(value, str) or not value
            for value in (gate_version, current_gate, current_status)
        ):
            raise ConfigurationError("platform current gate facts are invalid")
        return GateFacts(
            port_version=self.version,
            gate_version=str(gate_version),
            current_gate=str(current_gate),
            current_status=str(current_status),
            gates=tuple(gates),
            acceptance_results=results,
            source_ref=self._path.as_posix(),
        )


class QuanthusAttributionVerificationAdapter:
    version = ATTRIBUTION_VERIFICATION_PORT_VERSION

    def __init__(self, publication_root: Path) -> None:
        self._root = publication_root.resolve()

    def verify(self, release_id: str) -> AttributionVerification:
        if not _RELEASE_ID.fullmatch(release_id):
            raise ValueError("invalid release id")
        release_dir = self._root / release_id
        # The domain verifier performs the remaining path, checksum, and lineage checks.
        published = verify_attribution_release(release_dir)
        if published.root.resolve().parent != self._root:
            raise ValueError("release resolved outside the configured publication root")
        return AttributionVerification(
            port_version=self.version,
            release_id=published.release_id,
            release_digest=published.manifest.digest,
            run_id=published.manifest.run_id,
            source_kind=published.manifest.source_kind,
            workspace_digest=published.manifest.workspace_digest,
            synthetic_fixture=published.manifest.synthetic_fixture,
            formal_strategy_evidence=published.manifest.formal_strategy_evidence,
            artifact_ref=published.root.as_posix(),
        )
