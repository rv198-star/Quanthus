"""Typed contracts for the P6 prospective-validation entry gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn


class ForwardValidationContractError(ValueError):
    """Fail-closed P6 contract error with a stable machine reason."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def fail(reason: str, detail: str) -> NoReturn:
    raise ForwardValidationContractError(reason, detail)


def plain_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            fail("unsupported_canonical_value", "mapping keys must be strings")
        return {str(key): plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain_json(item) for item in value]
    fail("unsupported_canonical_value", f"unsupported JSON value {type(value).__name__}")


def freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            fail("unsupported_canonical_value", "mapping keys must be strings")
        return MappingProxyType(
            {str(key): freeze_json(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item) for item in value)
    fail("unsupported_canonical_value", f"unsupported JSON value {type(value).__name__}")


@dataclass(frozen=True)
class P6EntryProposal:
    """One immutable prospective-MVP entry proposal.

    This object carries no activation authority. It only proves that the entry contract is complete,
    canonical, digest-bound, and consistent with the frozen repository evidence.
    """

    path: Path
    payload: Mapping[str, Any]
    proposal_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_json(self.payload))

    @property
    def proposal_id(self) -> str:
        return str(self.payload["proposal_id"])

    @property
    def state(self) -> str:
        return str(self.payload["state"])

    @property
    def official_cycle_authorized(self) -> bool:
        authority = self.payload["authority"]
        if not isinstance(authority, Mapping):
            fail("field_mismatch", "authority must be an object")
        return bool(authority["official_prospective_cycle_authorized"])
