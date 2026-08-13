"""Versioned application-port contracts crossing into Quanthus authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

GATE_QUERY_PORT_VERSION = "quanthus.gate-query.v1"
ATTRIBUTION_VERIFICATION_PORT_VERSION = "quanthus.attribution-verification.v1"


@dataclass(frozen=True)
class GateFacts:
    port_version: str
    gate_version: str
    current_gate: str
    current_status: str
    gates: tuple[dict[str, str], ...]
    acceptance_results: dict[str, str]
    source_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "port_version": self.port_version,
            "gate_version": self.gate_version,
            "current_gate": self.current_gate,
            "current_status": self.current_status,
            "gates": list(self.gates),
            "acceptance_results": self.acceptance_results,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class AttributionVerification:
    port_version: str
    release_id: str
    release_digest: str
    run_id: str
    source_kind: str
    workspace_digest: str
    synthetic_fixture: bool
    formal_strategy_evidence: bool
    artifact_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "port_version": self.port_version,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "run_id": self.run_id,
            "source_kind": self.source_kind,
            "workspace_digest": self.workspace_digest,
            "synthetic_fixture": self.synthetic_fixture,
            "formal_strategy_evidence": self.formal_strategy_evidence,
            "artifact_ref": self.artifact_ref,
        }


class GateQueryPort(Protocol):
    version: str

    def read(self) -> GateFacts: ...


class AttributionVerificationPort(Protocol):
    version: str

    def verify(self, release_id: str) -> AttributionVerification: ...
