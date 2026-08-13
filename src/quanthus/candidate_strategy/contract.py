"""Versioned contract for external strategies awaiting Quanthus validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


class CandidateContractError(ValueError):
    """Raised before an unsupported or ambiguous candidate reaches replay."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateContractError(f"{field} must be a non-empty string")
    return value.strip()


def _object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateContractError(f"{field} must be an object")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CandidateContractError(f"{field} must be a positive integer")
    return value


def _ratio(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise CandidateContractError(f"{field} must be decimal-compatible") from exc
    lower_ok = parsed >= 0 if allow_zero else parsed > 0
    if not parsed.is_finite() or not lower_ok or parsed > 1:
        raise CandidateContractError(f"{field} must be within the supported ratio range")
    return parsed


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    candidate_version: str
    display_name: str
    source_repository: str
    source_commit: str
    source_license: str
    source_template_key: str
    adapter_contract: str
    adapter_template: str
    source_symbol: str
    security_code: str
    short_period: int
    mid_period: int
    long_period: int
    min_body_pct: Decimal
    target_pct: Decimal
    lot_size: int
    pipeline_state: str
    execution_authorized: bool
    canonical_payload: Mapping[str, Any]

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_candidate_manifest(path: Path) -> CandidateManifest:
    try:
        raw = json.loads(path.resolve().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidateContractError(f"candidate manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CandidateContractError(f"candidate manifest is invalid JSON: {exc}") from exc
    root = _object(raw, "candidate manifest")
    if root.get("schema_version") != "quanthus.candidate-strategy.v1":
        raise CandidateContractError("unsupported candidate manifest schema_version")

    source = _object(root.get("source"), "source")
    adapter = _object(root.get("adapter"), "adapter")
    universe = _object(root.get("universe"), "universe")
    parameters = _object(root.get("parameters"), "parameters")
    governance = _object(root.get("governance"), "governance")
    symbols = universe.get("symbols")
    source_symbols = universe.get("source_symbols")
    if (
        universe.get("kind") != "static"
        or not isinstance(symbols, list)
        or len(symbols) != 1
        or not isinstance(source_symbols, list)
        or len(source_symbols) != 1
    ):
        raise CandidateContractError("pilot candidates require exactly one static security")
    if adapter.get("contract") != "quanthus.quantdinger-signal-adapter.v1":
        raise CandidateContractError("unsupported candidate adapter contract")
    if adapter.get("template") != "bullish-three-lines":
        raise CandidateContractError("unsupported QuantDinger template")
    if adapter.get("frequency") != "1d" or adapter.get("direction") != "long-only":
        raise CandidateContractError("pilot adapter supports daily long-only candidates")
    if adapter.get("execution_profile") != "close-to-next-open-v1":
        raise CandidateContractError("candidate cannot bypass the Quanthus execution profile")
    if source.get("system") != "QuantDinger" or source.get("license") != "Apache-2.0":
        raise CandidateContractError("candidate source or license is unsupported")
    if source.get("template_key") != "strategy_v2_bullish_three_lines":
        raise CandidateContractError("candidate template key does not match the adapter")
    if governance.get("research_only") is not True:
        raise CandidateContractError("external candidates must remain research-only")
    if governance.get("execution_authorized") is not False:
        raise CandidateContractError("candidate manifests cannot grant execution authority")

    short = _positive_int(parameters.get("short_period"), "parameters.short_period")
    mid = _positive_int(parameters.get("mid_period"), "parameters.mid_period")
    long = _positive_int(parameters.get("long_period"), "parameters.long_period")
    if not short < mid < long:
        raise CandidateContractError("candidate periods must be strictly increasing")

    return CandidateManifest(
        candidate_id=_text(root.get("candidate_id"), "candidate_id"),
        candidate_version=_text(root.get("candidate_version"), "candidate_version"),
        display_name=_text(root.get("display_name"), "display_name"),
        source_repository=_text(source.get("repository"), "source.repository"),
        source_commit=_text(source.get("commit"), "source.commit"),
        source_license=_text(source.get("license"), "source.license"),
        source_template_key=_text(source.get("template_key"), "source.template_key"),
        adapter_contract=_text(adapter.get("contract"), "adapter.contract"),
        adapter_template=_text(adapter.get("template"), "adapter.template"),
        source_symbol=_text(source_symbols[0], "universe.source_symbols[0]"),
        security_code=_text(symbols[0], "universe.symbols[0]"),
        short_period=short,
        mid_period=mid,
        long_period=long,
        min_body_pct=_ratio(
            parameters.get("min_body_pct"), "parameters.min_body_pct", allow_zero=True
        ),
        target_pct=_ratio(parameters.get("target_pct"), "parameters.target_pct"),
        lot_size=_positive_int(parameters.get("lot_size"), "parameters.lot_size"),
        pipeline_state=_text(governance.get("pipeline_state"), "governance.pipeline_state"),
        execution_authorized=False,
        canonical_payload=dict(root),
    )
