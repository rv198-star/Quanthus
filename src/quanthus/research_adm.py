"""Small governed application-data-mart contract for P4 research inputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXPECTED_ASSETS = {
    "candidate_daily": "candidate_daily.parquet",
    "static_industry_mapping": "static_industry_mapping.json",
    "static_industry_coverage_audit": "static_industry_coverage_audit.json",
}
_EXPECTED_ALLOWED_USES = (
    "candidate_signal_input",
    "eligibility_input",
    "static_non_pit_concentration_only",
    "coverage_gate_evidence",
)
_EXPECTED_FORBIDDEN_CLAIMS = (
    "canonical_dwd_fact",
    "historical_industry_pit",
    "generic_dws",
    "operational_ads",
    "execution_authority",
)


class ResearchADMError(ValueError):
    """Raised when the P4 Research ADM policy is incomplete or overclaims authority."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchADMError(f"{field} must be a non-empty string")
    return value


def _exact_keys(raw: Mapping[str, Any], expected: set[str]) -> None:
    missing = sorted(expected.difference(raw))
    unexpected = sorted(set(raw).difference(expected))
    if missing or unexpected:
        raise ResearchADMError(
            f"P4 Research ADM policy keys differ; missing={missing}, unexpected={unexpected}"
        )


@dataclass(frozen=True)
class P4ResearchADMPolicy:
    path: Path
    schema_version: str
    adm_id: str
    adm_version: str
    status: str
    research_only: bool
    execution_authorized: bool
    upstream_authority: str
    purpose: str
    assets: Mapping[str, str]
    allowed_uses: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ResearchADMError("P4 Research ADM schema must be 1.0.0")
        if self.adm_id != "p4-event-research-adm" or self.adm_version != "1.0.0":
            raise ResearchADMError("unexpected P4 Research ADM identity")
        if self.status != "approved_development_only":
            raise ResearchADMError("P4 Research ADM must remain development-only")
        if self.research_only is not True or self.execution_authorized is not False:
            raise ResearchADMError("P4 Research ADM cannot carry execution authority")
        if self.upstream_authority != "dwd_v2_1_plus_correction":
            raise ResearchADMError("P4 Research ADM must consume the accepted DWD authority")
        if self.purpose != "prepare_p4_event_library_inputs":
            raise ResearchADMError("unexpected P4 Research ADM purpose")
        if dict(self.assets) != _EXPECTED_ASSETS:
            raise ResearchADMError("P4 Research ADM assets differ from the frozen contract")
        if self.allowed_uses != _EXPECTED_ALLOWED_USES:
            raise ResearchADMError("P4 Research ADM allowed uses differ from the frozen contract")
        if self.forbidden_claims != _EXPECTED_FORBIDDEN_CLAIMS:
            raise ResearchADMError(
                "P4 Research ADM forbidden claims differ from the frozen contract"
            )
        if _SHA256.fullmatch(self.semantic_digest) is None:
            raise ResearchADMError("P4 Research ADM semantic digest must use sha256")

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "adm_id": self.adm_id,
            "adm_version": self.adm_version,
            "status": self.status,
            "research_only": self.research_only,
            "execution_authorized": self.execution_authorized,
            "upstream_authority": self.upstream_authority,
            "purpose": self.purpose,
            "assets": dict(self.assets),
            "allowed_uses": list(self.allowed_uses),
            "forbidden_claims": list(self.forbidden_claims),
        }

    @property
    def computed_semantic_digest(self) -> str:
        return _digest(self.semantic_payload())

    def to_dict(self) -> dict[str, object]:
        return {**self.semantic_payload(), "semantic_digest": self.semantic_digest}


def load_p4_research_adm_policy(path: Path) -> P4ResearchADMPolicy:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResearchADMError(f"cannot read P4 Research ADM policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ResearchADMError(f"invalid P4 Research ADM JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchADMError("P4 Research ADM policy must be an object")
    _exact_keys(
        value,
        {
            "schema_version",
            "adm_id",
            "adm_version",
            "status",
            "research_only",
            "execution_authorized",
            "upstream_authority",
            "purpose",
            "assets",
            "allowed_uses",
            "forbidden_claims",
            "semantic_digest",
        },
    )
    assets = value["assets"]
    if not isinstance(assets, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in assets.items()
    ):
        raise ResearchADMError("P4 Research ADM assets must be a string mapping")
    allowed = value["allowed_uses"]
    forbidden = value["forbidden_claims"]
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ResearchADMError("P4 Research ADM allowed_uses must be an array of strings")
    if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
        raise ResearchADMError("P4 Research ADM forbidden_claims must be an array of strings")
    result = P4ResearchADMPolicy(
        path=path.resolve(),
        schema_version=_text(value["schema_version"], "schema_version"),
        adm_id=_text(value["adm_id"], "adm_id"),
        adm_version=_text(value["adm_version"], "adm_version"),
        status=_text(value["status"], "status"),
        research_only=value["research_only"] is True,
        execution_authorized=value["execution_authorized"] is True,
        upstream_authority=_text(value["upstream_authority"], "upstream_authority"),
        purpose=_text(value["purpose"], "purpose"),
        assets=dict(assets),
        allowed_uses=tuple(allowed),
        forbidden_claims=tuple(forbidden),
        semantic_digest=_text(value["semantic_digest"], "semantic_digest"),
    )
    if result.semantic_digest != result.computed_semantic_digest:
        raise ResearchADMError("P4 Research ADM semantic digest mismatch")
    return result
