"""Small governed application-data-mart contract for P4 research inputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXPECTED_ASSETS = {
    "candidate_daily": "candidate_daily.parquet",
    "static_industry_mapping": "static_industry_mapping.json",
    "static_industry_source_receipt": "static_industry_source_receipt.json",
    "static_industry_coverage_audit": "static_industry_coverage_audit.json",
}
_PRIMARY_INDUSTRY_QUERY_DATE = date(2021, 9, 30)
_LEGACY_INDUSTRY_QUERY_DATE = date(2012, 12, 31)
_EXPECTED_INDUSTRY_CLASSIFICATION = "证监会行业分类"
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


def _normalize_industry_name(value: str) -> str:
    return re.sub(r"[\s、，,（）()]", "", value).replace("及", "和")


def _load_industry_hierarchy(path: Path) -> Mapping[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchADMError(f"cannot read CSRC industry hierarchy: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("leaf_nodes"), dict):
        raise ResearchADMError("CSRC industry hierarchy leaf_nodes are invalid")
    leaves = raw["leaf_nodes"]
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in leaves.items()):
        raise ResearchADMError("CSRC industry hierarchy leaf rows are invalid")
    return dict(leaves)


def _provider_rows(result: Any) -> list[list[str]]:
    if str(result.error_code) != "0":
        raise ResearchADMError(f"BaoStock industry query failed: {result.error_msg}")
    rows: list[list[str]] = []
    while result.next():
        row = result.get_row_data()
        if len(row) < 5:
            raise ResearchADMError("BaoStock industry row is incomplete")
        rows.append([str(value) for value in row])
    return rows


def _rows_by_code(rows: Sequence[Sequence[str]]) -> Mapping[str, Sequence[str]]:
    result: dict[str, Sequence[str]] = {}
    for row in rows:
        code = str(row[1]).split(".")[-1]
        if not re.fullmatch(r"\d{6}", code):
            continue
        if code in result:
            raise ResearchADMError(f"BaoStock industry snapshot duplicates security {code}")
        result[code] = row
    return result


def collect_p4_historical_industry_supplement(
    security_codes: Sequence[str],
    *,
    hierarchy_path: Path,
    binding: HistoricalIndustryReference | None = None,
    bs_module: Any | None = None,
) -> tuple[Mapping[str, Mapping[str, str]], Mapping[str, Any]]:
    """Collect deterministic CSRC-industry labels for historical-only P4 securities.

    The primary source is the fixed 2021-09-30 BaoStock CSRC snapshot. Securities whose
    classification is blank there are resolved from the fixed 2012-12-31 legacy CSRC snapshot by
    matching an old classification-name segment to the CSRC-2012 hierarchy. The output is static,
    non-PIT and concentration-only; it never asserts historical membership intervals.
    """

    ordered_codes = tuple(sorted(security_codes))
    if not ordered_codes or len(ordered_codes) != len(set(ordered_codes)):
        raise ResearchADMError("historical industry supplement requires unique security codes")
    if any(re.fullmatch(r"\d{6}", code) is None for code in ordered_codes):
        raise ResearchADMError("historical industry supplement codes must be six digits")
    leaves = _load_industry_hierarchy(hierarchy_path)
    hierarchy_sha256 = f"sha256:{hashlib.sha256(hierarchy_path.read_bytes()).hexdigest()}"
    if binding is not None:
        if len(ordered_codes) != binding.expected_security_count:
            raise ResearchADMError(
                "historical industry supplement security count differs from policy"
            )
        if hierarchy_sha256 != binding.hierarchy_sha256:
            raise ResearchADMError("historical industry hierarchy checksum differs from policy")
    normalized_leaf_names: dict[str, list[str]] = {}
    for code, name in leaves.items():
        normalized_leaf_names.setdefault(_normalize_industry_name(name), []).append(code)

    if bs_module is None:
        try:
            import baostock as loaded_bs  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ResearchADMError(
                "BaoStock is required for P4 historical industry supplement"
            ) from exc
        bs_module = loaded_bs

    provider_version = getattr(bs_module, "__version__", None)
    if not isinstance(provider_version, str) or not provider_version.strip():
        try:
            provider_version = importlib.metadata.version("baostock")
        except importlib.metadata.PackageNotFoundError as exc:
            raise ResearchADMError("cannot resolve BaoStock provider version") from exc
    if binding is not None and provider_version != binding.provider_version:
        raise ResearchADMError("BaoStock provider version differs from ADM policy")

    primary_query_date = (
        binding.primary_query_date if binding is not None else _PRIMARY_INDUSTRY_QUERY_DATE
    )
    legacy_query_date = (
        binding.legacy_query_date if binding is not None else _LEGACY_INDUSTRY_QUERY_DATE
    )
    login = bs_module.login()
    if str(login.error_code) != "0":
        raise ResearchADMError(f"BaoStock login failed: {login.error_msg}")
    try:
        primary_rows = _provider_rows(
            bs_module.query_stock_industry(date=primary_query_date.isoformat())
        )
        legacy_rows = _provider_rows(
            bs_module.query_stock_industry(date=legacy_query_date.isoformat())
        )
    finally:
        bs_module.logout()
    primary = _rows_by_code(primary_rows)
    legacy = _rows_by_code(legacy_rows)

    memberships: dict[str, Mapping[str, str]] = {}
    source_rows: list[dict[str, str]] = []
    for security_code in ordered_codes:
        row = primary.get(security_code)
        if row is None:
            raise ResearchADMError(
                f"BaoStock primary industry snapshot lacks historical security {security_code}"
            )
        if str(row[4]).strip() != _EXPECTED_INDUSTRY_CLASSIFICATION:
            raise ResearchADMError(
                f"BaoStock industry classification differs for {security_code}: {row[4]}"
            )
        industry = str(row[3]).strip()
        resolution = "primary_csrc_2012_snapshot"
        query_date = primary_query_date
        source_row = row
        match = re.fullmatch(r"([A-Z]\d+)(.+)", industry) if industry else None
        if match is not None:
            industry_code = match.group(1)
            if industry_code not in leaves:
                raise ResearchADMError(
                    "BaoStock CSRC industry code is outside hierarchy: "
                    f"{security_code}={industry_code}"
                )
            industry_name = leaves[industry_code]
        else:
            source_row = legacy.get(security_code)
            if source_row is None or not str(source_row[3]).strip():
                raise ResearchADMError(
                    f"BaoStock legacy industry snapshot cannot resolve {security_code}"
                )
            if str(source_row[4]).strip() != _EXPECTED_INDUSTRY_CLASSIFICATION:
                raise ResearchADMError(
                    f"BaoStock legacy classification differs for {security_code}"
                )
            candidates: set[str] = set()
            for segment in str(source_row[3]).split("-"):
                candidates.update(
                    normalized_leaf_names.get(_normalize_industry_name(segment), ())
                )
            if len(candidates) != 1:
                raise ResearchADMError(
                    "legacy CSRC industry crosswalk is ambiguous for "
                    f"{security_code}: {sorted(candidates)}"
                )
            industry_code = next(iter(candidates))
            industry_name = leaves[industry_code]
            resolution = "legacy_csrc_name_crosswalk_to_2012"
            query_date = legacy_query_date
        if date.fromisoformat(str(source_row[0])) > query_date:
            raise ResearchADMError(
                f"BaoStock industry update is after requested snapshot for {security_code}"
            )
        memberships[security_code] = {
            "industry_code": industry_code,
            "industry_name": industry_name,
        }
        source_rows.append(
            {
                "security_code": security_code,
                "security_name": str(source_row[2]),
                "industry_code": industry_code,
                "industry_name": industry_name,
                "provider_industry": str(source_row[3]),
                "provider_update_date": str(source_row[0]),
                "query_date": query_date.isoformat(),
                "resolution": resolution,
            }
        )

    receipt_payload: dict[str, Any] = {
        "schema_version": "quanthus.p4-adm-industry-source-receipt.v1",
        "provider": "baostock.query_stock_industry",
        "provider_version": provider_version,
        "classification": _EXPECTED_INDUSTRY_CLASSIFICATION,
        "primary_query_date": primary_query_date.isoformat(),
        "legacy_query_date": legacy_query_date.isoformat(),
        "hierarchy_sha256": hierarchy_sha256,
        "primary_provider_row_count": len(primary_rows),
        "legacy_provider_row_count": len(legacy_rows),
        "selected_security_count": len(source_rows),
        "source_rows": source_rows,
    }
    receipt = {**receipt_payload, "content_digest": _digest(receipt_payload)}
    if binding is not None and receipt["content_digest"] != binding.source_content_digest:
        raise ResearchADMError("historical industry source digest differs from ADM policy")
    return memberships, receipt


@dataclass(frozen=True)
class HistoricalIndustryReference:
    provider: str
    provider_version: str
    classification: str
    primary_query_date: date
    legacy_query_date: date
    expected_security_count: int
    hierarchy_path: str
    hierarchy_sha256: str
    source_content_digest: str

    def __post_init__(self) -> None:
        if self.provider != "baostock.query_stock_industry":
            raise ResearchADMError("unexpected P4 historical industry provider")
        if self.provider_version != "0.9.3":
            raise ResearchADMError("P4 historical industry provider version must be 0.9.3")
        if self.classification != _EXPECTED_INDUSTRY_CLASSIFICATION:
            raise ResearchADMError("P4 historical industry classification differs")
        if self.primary_query_date != _PRIMARY_INDUSTRY_QUERY_DATE:
            raise ResearchADMError("P4 historical industry primary query date differs")
        if self.legacy_query_date != _LEGACY_INDUSTRY_QUERY_DATE:
            raise ResearchADMError("P4 historical industry legacy query date differs")
        if (
            not isinstance(self.expected_security_count, int)
            or isinstance(self.expected_security_count, bool)
            or self.expected_security_count <= 0
        ):
            raise ResearchADMError(
                "P4 historical industry supplement count must be a positive integer"
            )
        if self.hierarchy_path != "config/pipelines/csrc-industry-hierarchy.v1.json":
            raise ResearchADMError("P4 historical industry hierarchy path differs")
        if _SHA256.fullmatch(self.hierarchy_sha256) is None:
            raise ResearchADMError("P4 historical industry hierarchy sha256 is invalid")
        if _SHA256.fullmatch(self.source_content_digest) is None:
            raise ResearchADMError("P4 historical industry source digest is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "classification": self.classification,
            "primary_query_date": self.primary_query_date.isoformat(),
            "legacy_query_date": self.legacy_query_date.isoformat(),
            "expected_security_count": self.expected_security_count,
            "hierarchy_path": self.hierarchy_path,
            "hierarchy_sha256": self.hierarchy_sha256,
            "source_content_digest": self.source_content_digest,
        }


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
    historical_industry_reference: HistoricalIndustryReference
    allowed_uses: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ResearchADMError("P4 Research ADM schema must be 1.0.0")
        if self.adm_id != "p4-event-research-adm" or self.adm_version != "1.1.0":
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
            "historical_industry_reference": self.historical_industry_reference.to_dict(),
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
            "historical_industry_reference",
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
    historical = value["historical_industry_reference"]
    if not isinstance(historical, dict):
        raise ResearchADMError("P4 historical industry reference must be an object")
    _exact_keys(
        historical,
        {
            "provider",
            "provider_version",
            "classification",
            "primary_query_date",
            "legacy_query_date",
            "expected_security_count",
            "hierarchy_path",
            "hierarchy_sha256",
            "source_content_digest",
        },
    )
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
        historical_industry_reference=HistoricalIndustryReference(
            provider=_text(historical["provider"], "historical_industry_reference.provider"),
            provider_version=_text(
                historical["provider_version"], "historical_industry_reference.provider_version"
            ),
            classification=_text(
                historical["classification"], "historical_industry_reference.classification"
            ),
            primary_query_date=date.fromisoformat(
                _text(
                    historical["primary_query_date"],
                    "historical_industry_reference.primary_query_date",
                )
            ),
            legacy_query_date=date.fromisoformat(
                _text(
                    historical["legacy_query_date"],
                    "historical_industry_reference.legacy_query_date",
                )
            ),
            expected_security_count=int(historical["expected_security_count"]),
            hierarchy_path=_text(
                historical["hierarchy_path"], "historical_industry_reference.hierarchy_path"
            ),
            hierarchy_sha256=_text(
                historical["hierarchy_sha256"], "historical_industry_reference.hierarchy_sha256"
            ),
            source_content_digest=_text(
                historical["source_content_digest"],
                "historical_industry_reference.source_content_digest",
            ),
        ),
        allowed_uses=tuple(allowed),
        forbidden_claims=tuple(forbidden),
        semantic_digest=_text(value["semantic_digest"], "semantic_digest"),
    )
    if result.semantic_digest != result.computed_semantic_digest:
        raise ResearchADMError("P4 Research ADM semantic digest mismatch")
    return result
