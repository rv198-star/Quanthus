"""Additive formal-strategy context without changing the frozen v1 protocol."""

from __future__ import annotations

from dataclasses import dataclass

from quanthus.expert_protocols import (
    ExpertContext,
    ExpertEvidenceRef,
    ExpertProtocolError,
)


@dataclass(frozen=True)
class FormalExpertContext(ExpertContext):
    """Carry domain observations as evidence on the additive strategy path."""

    additional_evidence_refs: tuple[ExpertEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        additional_keys = [item.key for item in self.additional_evidence_refs]
        if len(additional_keys) != len(set(additional_keys)):
            raise ExpertProtocolError(
                "invalid_context", "additional evidence identities must be unique"
            )

    @property
    def evidence_refs(self) -> tuple[ExpertEvidenceRef, ...]:
        refs = (*super().evidence_refs, *self.additional_evidence_refs)
        keys = [item.key for item in refs]
        if len(keys) != len(set(keys)):
            raise ExpertProtocolError("invalid_context", "context evidence identities overlap")
        return tuple(sorted(refs, key=lambda item: item.key))
