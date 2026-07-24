"""Versioned P1 universe policy for curated daily data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError, find_project_root


@dataclass(frozen=True)
class UniversePolicy:
    path: Path
    version: str
    allowed_exchanges: tuple[str, ...]
    excluded_security_ids: Mapping[str, str]
    out_of_scope_code_prefixes: Mapping[str, str]
    invalid_bar_handling: str

    @property
    def digest(self) -> str:
        payload = {
            "version": self.version,
            "allowed_exchanges": self.allowed_exchanges,
            "excluded_security_ids": dict(sorted(self.excluded_security_ids.items())),
            "out_of_scope_code_prefixes": dict(sorted(self.out_of_scope_code_prefixes.items())),
            "invalid_bar_handling": self.invalid_bar_handling,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def includes(self, security_id: str, exchange: str) -> bool:
        return exchange in self.allowed_exchanges and security_id not in self.excluded_security_ids

    def is_out_of_scope_code(self, stock_code: str) -> bool:
        return any(stock_code.startswith(prefix) for prefix in self.out_of_scope_code_prefixes)


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field_name} must be a non-empty string")
    return value


def _load_reason_map(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field_name} must be an object")
    result: dict[str, str] = {}
    for key_value, item in value.items():
        key = _require_string(key_value, f"{field_name} key")
        if not isinstance(item, dict):
            raise ConfigurationError(f"{field_name}.{key} must be an object")
        result[key] = _require_string(item.get("reason"), f"{field_name}.{key}.reason")
    return result


def load_universe_policy(path: Path | None = None) -> UniversePolicy:
    resolved = (path or find_project_root() / "config" / "p1-universe.v2.json").resolve()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"universe policy does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in universe policy: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("universe policy root must be an object")
    if raw.get("schema_version") != "1.0.0":
        raise ConfigurationError("universe policy schema_version must be 1.0.0")
    if raw.get("status") != "frozen":
        raise ConfigurationError("universe policy status must be frozen")

    exchanges = raw.get("allowed_exchanges")
    if not isinstance(exchanges, list) or not exchanges:
        raise ConfigurationError("allowed_exchanges must be a non-empty array")
    allowed_exchanges = tuple(_require_string(item, "allowed_exchanges item") for item in exchanges)
    if len(allowed_exchanges) != len(set(allowed_exchanges)):
        raise ConfigurationError("allowed_exchanges must be unique")
    if set(allowed_exchanges) != {"SSE", "SZSE"}:
        raise ConfigurationError("P1 allowed_exchanges must be exactly SSE and SZSE")

    invalid_bar_handling = _require_string(raw.get("invalid_bar_handling"), "invalid_bar_handling")
    if invalid_bar_handling != "retain_with_data_valid_false":
        raise ConfigurationError("invalid_bar_handling must preserve rows with data_valid=false")
    return UniversePolicy(
        path=resolved,
        version=_require_string(raw.get("policy_version"), "policy_version"),
        allowed_exchanges=allowed_exchanges,
        excluded_security_ids=_load_reason_map(
            raw.get("excluded_security_ids"),
            "excluded_security_ids",
        ),
        out_of_scope_code_prefixes=_load_reason_map(
            raw.get("out_of_scope_code_prefixes"),
            "out_of_scope_code_prefixes",
        ),
        invalid_bar_handling=invalid_bar_handling,
    )
