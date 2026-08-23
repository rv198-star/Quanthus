"""Deterministic P4 daily group comparison report.

The report consumes one sealed Event Library plus its sealed label corpus, rebuilds the accepted
P3 baseline feature panel from the same governed development provider, aligns seven dimensionless
pre-entry comparison features at T through T-20, and publishes descriptive group statistics plus
predeclared pairwise effect/robustness evidence.  It is research evidence, not a trading rule.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    ReplayDataSession,
    load_replay_data_provider_config,
)
from quanthus.event_library.contracts import require_sha256_digest, sha256_digest
from quanthus.event_library.job_local import (
    JobLocalArtifactError,
    JobLocalFeaturePanel,
    require_job_local_session,
)
from quanthus.event_library.label_corpus import verify_physical_label_corpus
from quanthus.event_library.physical_scan import verify_physical_event_scan
from quanthus.feature_registry import load_feature_registry

REPORT_SCHEMA = "quanthus.p4-event-comparison-report.v1"
_POLICY_SCHEMA = "1.0.0"
_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_ALLOWED_LIFECYCLES = {"draft", "approved", "deprecated"}
_ALLOWED_OUTCOMES = ("positive", "neutral", "failure")
_ALLOWED_FEATURES = (
    "price.price_to_sma20",
    "price.sma20_to_sma60",
    "price.momentum20",
    "price.breakout20",
    "price.rsi14",
    "liquidity.volume_ratio20",
    "risk.realized_volatility20",
)
_FEATURE_FORMULAS = {
    "price.price_to_sma20": "total_return_index / sma20 - 1",
    "price.sma20_to_sma60": "sma20 / sma60 - 1",
    "price.momentum20": "momentum20",
    "price.breakout20": "total_return_index / prior_high20 - 1",
    "price.rsi14": "rsi14",
    "liquidity.volume_ratio20": "volume_ratio20",
    "risk.realized_volatility20": "realized_volatility20",
}
_DEVELOPMENT_START = date(2012, 1, 4)
_DEVELOPMENT_END = date(2021, 12, 30)


class ComparisonReportError(RuntimeError):
    """Raised when a governed P4 comparison report cannot be built or verified."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ComparisonReportError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ComparisonReportError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComparisonReportError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ComparisonReportError(f"{path} must be a positive integer")
    return value


def _float_string(value: object, path: str) -> float:
    if not isinstance(value, str):
        raise ComparisonReportError(f"{path} must be a decimal string")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ComparisonReportError(f"{path} must be a decimal string") from exc
    if not math.isfinite(parsed):
        raise ComparisonReportError(f"{path} must be finite")
    return parsed


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise ComparisonReportError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _safe_relative(value: object) -> PurePosixPath:
    text = _text(value, "file.path")
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ComparisonReportError("report file inventory contains unsafe path")
    return relative


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(
                json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                + "\n"
            )


def _load_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ComparisonReportError(
                        f"JSONL row must be an object: {path}:{line_number}"
                    )
                rows.append(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonReportError(f"cannot read JSONL: {path}") from exc
    return tuple(rows)


@dataclass(frozen=True)
class ReportFeature:
    feature_id: str
    formula: str
    unit: str

    def __post_init__(self) -> None:
        if self.feature_id not in _ALLOWED_FEATURES:
            raise ComparisonReportError(f"unsupported comparison feature: {self.feature_id}")
        if self.formula != _FEATURE_FORMULAS[self.feature_id]:
            raise ComparisonReportError(f"comparison feature formula differs: {self.feature_id}")
        _text(self.unit, f"features.{self.feature_id}.unit")

    def to_dict(self) -> dict[str, str]:
        return {"feature_id": self.feature_id, "formula": self.formula, "unit": self.unit}


@dataclass(frozen=True)
class ReportGovernance:
    approval_state: str
    approval_authority: str
    decision_reference: str

    def __post_init__(self) -> None:
        if self.approval_authority != "human":
            raise ComparisonReportError("report policy approval authority must be human")
        _text(self.decision_reference, "governance.decision_reference")

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_state": self.approval_state,
            "approval_authority": self.approval_authority,
            "decision_reference": self.decision_reference,
        }


@dataclass(frozen=True)
class ComparisonReportPolicy:
    path: Path
    schema_version: str
    policy_id: str
    policy_version: str
    lifecycle: str
    owner: str
    research_question: str
    outcomes: tuple[str, ...]
    primary_contrast: tuple[str, str]
    lookback_sessions: int
    features: tuple[ReportFeature, ...]
    effect_size: str
    uncertainty: str
    multiple_testing: str
    fdr_alpha: float
    reliable_consecutive_lags: int
    adjacent_q_max: float
    minimum_group_count: int
    minimum_perturbation_group_count: int
    perturbations: tuple[str, ...]
    leading_feature_count: int
    feature_registry_version: str
    feature_registry_digest: str
    outcome_card_digest: str
    structural_break_card_digest: str
    governance: ReportGovernance
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != _POLICY_SCHEMA:
            raise ComparisonReportError("report policy schema_version must be 1.0.0")
        if _SEMVER.fullmatch(self.policy_version) is None:
            raise ComparisonReportError("report policy version must use semantic versioning")
        if self.lifecycle not in _ALLOWED_LIFECYCLES:
            raise ComparisonReportError("unsupported report policy lifecycle")
        if self.lifecycle == "approved" and self.governance.approval_state != "approved":
            raise ComparisonReportError("approved report policy requires approved governance")
        if self.lifecycle == "draft" and self.governance.approval_state != "pending_human_approval":
            raise ComparisonReportError("draft report policy requires pending approval")
        if self.outcomes != _ALLOWED_OUTCOMES:
            raise ComparisonReportError("report outcomes must be positive, neutral, failure")
        if self.primary_contrast != ("positive", "neutral"):
            raise ComparisonReportError("primary report contrast must be positive vs neutral")
        if self.lookback_sessions != 21:
            raise ComparisonReportError("P4 report lookback must be T through T-20")
        if tuple(item.feature_id for item in self.features) != _ALLOWED_FEATURES:
            raise ComparisonReportError("comparison feature order/set differs")
        if self.effect_size != "hedges_g":
            raise ComparisonReportError("report effect size must be hedges_g")
        if self.uncertainty != "normal_95pct_ci_mean_difference":
            raise ComparisonReportError("report uncertainty contract differs")
        if self.multiple_testing != "benjamini_hochberg_by_contrast":
            raise ComparisonReportError("report multiple-testing contract differs")
        if not 0 < self.fdr_alpha < 1 or not 0 < self.adjacent_q_max < 1:
            raise ComparisonReportError("report q thresholds must be in (0,1)")
        if self.reliable_consecutive_lags != 2:
            raise ComparisonReportError("reliable divergence must require two consecutive lags")
        if self.leading_feature_count != 3:
            raise ComparisonReportError("P4 report must expose exactly three leading features")
        if self.perturbations != (
            "deterministic_event_id_hash_halves",
            "adjacent_lag_sign_consistency",
        ):
            raise ComparisonReportError("report perturbation contract differs")
        for field_name, value in (
            ("feature_registry_digest", self.feature_registry_digest),
            ("outcome_card_digest", self.outcome_card_digest),
            ("structural_break_card_digest", self.structural_break_card_digest),
            ("semantic_digest", self.semantic_digest),
        ):
            require_sha256_digest(value, field_name)

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def require_reporting_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance.approval_state != "approved":
            raise ComparisonReportError("real comparison reporting requires approved policy")

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "research_question": self.research_question,
            "outcomes": list(self.outcomes),
            "primary_contrast": list(self.primary_contrast),
            "lookback_sessions": self.lookback_sessions,
            "features": [item.to_dict() for item in self.features],
            "statistics": {
                "effect_size": self.effect_size,
                "uncertainty": self.uncertainty,
                "multiple_testing": self.multiple_testing,
                "fdr_alpha": format(self.fdr_alpha, ".12g"),
            },
            "selection": {
                "reliable_consecutive_lags": self.reliable_consecutive_lags,
                "adjacent_q_max": format(self.adjacent_q_max, ".12g"),
                "minimum_group_count": self.minimum_group_count,
                "minimum_perturbation_group_count": self.minimum_perturbation_group_count,
                "perturbations": list(self.perturbations),
                "leading_feature_count": self.leading_feature_count,
            },
            "bindings": {
                "feature_registry_version": self.feature_registry_version,
                "feature_registry_digest": self.feature_registry_digest,
                "outcome_card_digest": self.outcome_card_digest,
                "structural_break_card_digest": self.structural_break_card_digest,
            },
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.semantic_payload(),
            "lifecycle": self.lifecycle,
            "owner": self.owner,
            "governance": self.governance.to_dict(),
            "semantic_digest": self.semantic_digest,
        }


def _parse_report_policy(path: Path) -> ComparisonReportPolicy:
    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ComparisonReportError(f"cannot read comparison report policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ComparisonReportError(f"invalid comparison report policy JSON: {exc}") from exc
    raw = _mapping(raw_value, "policy")
    _exact_keys(
        raw,
        {
            "schema_version",
            "policy_id",
            "policy_version",
            "lifecycle",
            "owner",
            "research_question",
            "outcomes",
            "primary_contrast",
            "lookback_sessions",
            "features",
            "statistics",
            "selection",
            "bindings",
            "governance",
            "semantic_digest",
        },
        "policy",
    )
    features: list[ReportFeature] = []
    for index, value in enumerate(_sequence(raw["features"], "features")):
        item = _mapping(value, f"features[{index}]")
        _exact_keys(item, {"feature_id", "formula", "unit"}, f"features[{index}]")
        features.append(
            ReportFeature(
                feature_id=_text(item["feature_id"], f"features[{index}].feature_id"),
                formula=_text(item["formula"], f"features[{index}].formula"),
                unit=_text(item["unit"], f"features[{index}].unit"),
            )
        )
    statistics = _mapping(raw["statistics"], "statistics")
    _exact_keys(
        statistics,
        {"effect_size", "uncertainty", "multiple_testing", "fdr_alpha"},
        "statistics",
    )
    selection = _mapping(raw["selection"], "selection")
    _exact_keys(
        selection,
        {
            "reliable_consecutive_lags",
            "adjacent_q_max",
            "minimum_group_count",
            "minimum_perturbation_group_count",
            "perturbations",
            "leading_feature_count",
        },
        "selection",
    )
    bindings = _mapping(raw["bindings"], "bindings")
    _exact_keys(
        bindings,
        {
            "feature_registry_version",
            "feature_registry_digest",
            "outcome_card_digest",
            "structural_break_card_digest",
        },
        "bindings",
    )
    governance = _mapping(raw["governance"], "governance")
    _exact_keys(
        governance,
        {"approval_state", "approval_authority", "decision_reference"},
        "governance",
    )
    outcomes = tuple(_text(item, "outcomes[]") for item in _sequence(raw["outcomes"], "outcomes"))
    primary = tuple(
        _text(item, "primary_contrast[]")
        for item in _sequence(raw["primary_contrast"], "primary_contrast")
    )
    if len(primary) != 2:
        raise ComparisonReportError("primary_contrast must contain two outcomes")
    perturbations = tuple(
        _text(item, "selection.perturbations[]")
        for item in _sequence(selection["perturbations"], "selection.perturbations")
    )
    return ComparisonReportPolicy(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        policy_id=_text(raw["policy_id"], "policy_id"),
        policy_version=_text(raw["policy_version"], "policy_version"),
        lifecycle=_text(raw["lifecycle"], "lifecycle"),
        owner=_text(raw["owner"], "owner"),
        research_question=_text(raw["research_question"], "research_question"),
        outcomes=outcomes,
        primary_contrast=(primary[0], primary[1]),
        lookback_sessions=_positive_int(raw["lookback_sessions"], "lookback_sessions"),
        features=tuple(features),
        effect_size=_text(statistics["effect_size"], "statistics.effect_size"),
        uncertainty=_text(statistics["uncertainty"], "statistics.uncertainty"),
        multiple_testing=_text(statistics["multiple_testing"], "statistics.multiple_testing"),
        fdr_alpha=_float_string(statistics["fdr_alpha"], "statistics.fdr_alpha"),
        reliable_consecutive_lags=_positive_int(
            selection["reliable_consecutive_lags"], "selection.reliable_consecutive_lags"
        ),
        adjacent_q_max=_float_string(selection["adjacent_q_max"], "selection.adjacent_q_max"),
        minimum_group_count=_positive_int(
            selection["minimum_group_count"], "selection.minimum_group_count"
        ),
        minimum_perturbation_group_count=_positive_int(
            selection["minimum_perturbation_group_count"],
            "selection.minimum_perturbation_group_count",
        ),
        perturbations=perturbations,
        leading_feature_count=_positive_int(
            selection["leading_feature_count"], "selection.leading_feature_count"
        ),
        feature_registry_version=_text(
            bindings["feature_registry_version"], "bindings.feature_registry_version"
        ),
        feature_registry_digest=require_sha256_digest(
            _text(bindings["feature_registry_digest"], "bindings.feature_registry_digest"),
            "bindings.feature_registry_digest",
        ),
        outcome_card_digest=require_sha256_digest(
            _text(bindings["outcome_card_digest"], "bindings.outcome_card_digest"),
            "bindings.outcome_card_digest",
        ),
        structural_break_card_digest=require_sha256_digest(
            _text(
                bindings["structural_break_card_digest"],
                "bindings.structural_break_card_digest",
            ),
            "bindings.structural_break_card_digest",
        ),
        governance=ReportGovernance(
            approval_state=_text(governance["approval_state"], "governance.approval_state"),
            approval_authority=_text(
                governance["approval_authority"], "governance.approval_authority"
            ),
            decision_reference=_text(
                governance["decision_reference"], "governance.decision_reference"
            ),
        ),
        semantic_digest=require_sha256_digest(
            _text(raw["semantic_digest"], "semantic_digest"), "semantic_digest"
        ),
    )


def load_comparison_report_policy(path: Path) -> ComparisonReportPolicy:
    policy = _parse_report_policy(path)
    if policy.semantic_digest != policy.computed_semantic_digest:
        raise ComparisonReportError("comparison report semantic_digest does not match content")
    return policy


def calculate_comparison_report_policy_digest(path: Path) -> str:
    return _parse_report_policy(path).computed_semantic_digest


@dataclass(frozen=True)
class ComparisonReportResult:
    root: Path
    manifest_sha256: str
    content_digest: str
    candidate_events: int
    three_class_events: int
    leading_features: tuple[str, ...]
    acceptance_pass: bool


@dataclass(frozen=True)
class _ReportEventInput:
    event_id: str
    panel_security_id: str
    signal_date: str
    outcome: str
    hash_half: int

    def as_row(self) -> tuple[object, ...]:
        return (
            self.event_id,
            self.panel_security_id,
            self.signal_date,
            self.outcome,
            self.hash_half,
        )


@dataclass(frozen=True)
class _Stats:
    count: int
    mean: float | None
    stddev: float | None
    q25: float | None = None
    median: float | None = None
    q75: float | None = None


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _effect(
    left: _Stats,
    right: _Stats,
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    if (
        left.count < 2
        or right.count < 2
        or left.mean is None
        or right.mean is None
        or left.stddev is None
        or right.stddev is None
    ):
        return None, None, None, None, None
    var_left = left.stddev * left.stddev
    var_right = right.stddev * right.stddev
    pooled_denom = left.count + right.count - 2
    if pooled_denom <= 0:
        return None, None, None, None, None
    pooled_var = (
        (left.count - 1) * var_left + (right.count - 1) * var_right
    ) / pooled_denom
    mean_diff = left.mean - right.mean
    hedges_g: float | None
    if pooled_var <= 0:
        hedges_g = 0.0 if mean_diff == 0 else math.copysign(float("inf"), mean_diff)
    else:
        correction = 1.0 - 3.0 / (4.0 * (left.count + right.count) - 9.0)
        hedges_g = correction * mean_diff / math.sqrt(pooled_var)
    se = math.sqrt(var_left / left.count + var_right / right.count)
    if se <= 0:
        z_value = 0.0 if mean_diff == 0 else math.copysign(float("inf"), mean_diff)
        p_value = 1.0 if mean_diff == 0 else 0.0
        ci_low = mean_diff
        ci_high = mean_diff
    else:
        z_value = mean_diff / se
        p_value = math.erfc(abs(z_value) / math.sqrt(2.0))
        ci_low = mean_diff - 1.959963984540054 * se
        ci_high = mean_diff + 1.959963984540054 * se
    if not math.isfinite(hedges_g):
        hedges_g = None
    return hedges_g, mean_diff, ci_low, ci_high, p_value


def _benjamini_hochberg(rows: list[dict[str, Any]]) -> None:
    indexed = [
        (index, float(row["p_value"]))
        for index, row in enumerate(rows)
        if row.get("p_value") is not None
    ]
    if not indexed:
        return
    ordered = sorted(indexed, key=lambda item: (item[1], item[0]))
    total = len(ordered)
    adjusted = [1.0] * total
    running = 1.0
    for position in range(total, 0, -1):
        _, p_value = ordered[position - 1]
        candidate = min(1.0, p_value * total / position)
        running = min(running, candidate)
        adjusted[position - 1] = running
    for (original_index, _), q_value in zip(ordered, adjusted, strict=True):
        rows[original_index]["q_value"] = q_value


def _sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _stats_from_row(row: Mapping[str, Any]) -> _Stats:
    return _Stats(
        count=int(row["sample_count"]),
        mean=_finite_or_none(row.get("mean")),
        stddev=_finite_or_none(row.get("stddev")),
        q25=_finite_or_none(row.get("q25")),
        median=_finite_or_none(row.get("median")),
        q75=_finite_or_none(row.get("q75")),
    )


def _pairwise_rows(
    group_rows: Sequence[Mapping[str, Any]],
    contrasts: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    by_key = {
        (str(row["feature_id"]), int(row["lag"]), str(row["outcome"])): _stats_from_row(row)
        for row in group_rows
    }
    result: list[dict[str, Any]] = []
    for left_outcome, right_outcome in contrasts:
        contrast = f"{left_outcome}_vs_{right_outcome}"
        contrast_rows: list[dict[str, Any]] = []
        for feature_id in _ALLOWED_FEATURES:
            for lag in range(21):
                left = by_key[(feature_id, lag, left_outcome)]
                right = by_key[(feature_id, lag, right_outcome)]
                hedges_g, mean_diff, ci_low, ci_high, p_value = _effect(left, right)
                contrast_rows.append(
                    {
                        "feature_id": feature_id,
                        "lag": lag,
                        "contrast": contrast,
                        "left_outcome": left_outcome,
                        "right_outcome": right_outcome,
                        "left_count": left.count,
                        "right_count": right.count,
                        "hedges_g": hedges_g,
                        "mean_difference": mean_diff,
                        "mean_difference_ci95_low": ci_low,
                        "mean_difference_ci95_high": ci_high,
                        "p_value": p_value,
                        "q_value": None,
                    }
                )
        _benjamini_hochberg(contrast_rows)
        result.extend(contrast_rows)
    return result


def _half_effects(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int, int], float | None]:
    by_key = {
        (
            str(row["feature_id"]),
            int(row["lag"]),
            int(row["hash_half"]),
            str(row["outcome"]),
        ): _stats_from_row(row)
        for row in rows
    }
    result: dict[tuple[str, int, int], float | None] = {}
    for feature_id in _ALLOWED_FEATURES:
        for lag in range(21):
            for half in (0, 1):
                left = by_key[(feature_id, lag, half, "positive")]
                right = by_key[(feature_id, lag, half, "neutral")]
                result[(feature_id, lag, half)] = _effect(left, right)[0]
    return result


def _feature_ranking(
    pairwise: Sequence[Mapping[str, Any]],
    half_rows: Sequence[Mapping[str, Any]],
    policy: ComparisonReportPolicy,
) -> list[dict[str, Any]]:
    primary = {
        (str(row["feature_id"]), int(row["lag"])): row
        for row in pairwise
        if row["contrast"] == "positive_vs_neutral"
    }
    half_effect = _half_effects(half_rows)
    rankings: list[dict[str, Any]] = []
    for feature_id in _ALLOWED_FEATURES:
        eligible = [
            primary[(feature_id, lag)]
            for lag in range(21)
            if primary[(feature_id, lag)]["hedges_g"] is not None
            and primary[(feature_id, lag)]["q_value"] is not None
            and float(primary[(feature_id, lag)]["q_value"]) <= policy.fdr_alpha
            and int(primary[(feature_id, lag)]["left_count"]) >= policy.minimum_group_count
            and int(primary[(feature_id, lag)]["right_count"]) >= policy.minimum_group_count
        ]
        peak = (
            max(
                eligible,
                key=lambda row: (abs(float(row["hedges_g"])), -int(row["lag"])),
            )
            if eligible
            else None
        )
        first_reliable_lag: int | None = None
        for lag in range(20, 0, -1):
            current = primary[(feature_id, lag)]
            next_row = primary[(feature_id, lag - 1)]
            current_effect = _finite_or_none(current.get("hedges_g"))
            next_effect = _finite_or_none(next_row.get("hedges_g"))
            if (
                current_effect is not None
                and next_effect is not None
                and _sign(current_effect) != 0
                and _sign(current_effect) == _sign(next_effect)
                and current.get("q_value") is not None
                and next_row.get("q_value") is not None
                and float(current["q_value"]) <= policy.fdr_alpha
                and float(next_row["q_value"]) <= policy.fdr_alpha
                and int(current["left_count"]) >= policy.minimum_group_count
                and int(current["right_count"]) >= policy.minimum_group_count
                and int(next_row["left_count"]) >= policy.minimum_group_count
                and int(next_row["right_count"]) >= policy.minimum_group_count
            ):
                first_reliable_lag = lag
                break
        if peak is None:
            rankings.append(
                {
                    "feature_id": feature_id,
                    "peak_lag": None,
                    "peak_hedges_g": None,
                    "peak_q_value": None,
                    "first_reliable_lag": first_reliable_lag,
                    "hash_half_0_hedges_g": None,
                    "hash_half_1_hedges_g": None,
                    "hash_half_stable": False,
                    "adjacent_lag_stable": False,
                    "survives_declared_checks": False,
                }
            )
            continue
        peak_lag = int(peak["lag"])
        peak_effect = float(peak["hedges_g"])
        half0 = half_effect[(feature_id, peak_lag, 0)]
        half1 = half_effect[(feature_id, peak_lag, 1)]
        half_counts_ok = True
        for half in (0, 1):
            half_group = {
                str(row["outcome"]): int(row["sample_count"])
                for row in half_rows
                if row["feature_id"] == feature_id
                and int(row["lag"]) == peak_lag
                and int(row["hash_half"]) == half
            }
            half_counts_ok = half_counts_ok and all(
                half_group.get(outcome, 0) >= policy.minimum_perturbation_group_count
                for outcome in ("positive", "neutral")
            )
        hash_stable = (
            half_counts_ok
            and _sign(peak_effect) != 0
            and _sign(half0) == _sign(peak_effect)
            and _sign(half1) == _sign(peak_effect)
        )
        adjacent_candidates = [lag for lag in (peak_lag - 1, peak_lag + 1) if 0 <= lag <= 20]
        adjacent_stable = any(
            primary[(feature_id, lag)]["hedges_g"] is not None
            and _sign(float(primary[(feature_id, lag)]["hedges_g"])) == _sign(peak_effect)
            and primary[(feature_id, lag)]["q_value"] is not None
            and float(primary[(feature_id, lag)]["q_value"]) <= policy.adjacent_q_max
            for lag in adjacent_candidates
        )
        rankings.append(
            {
                "feature_id": feature_id,
                "peak_lag": peak_lag,
                "peak_hedges_g": peak_effect,
                "peak_q_value": float(peak["q_value"]),
                "first_reliable_lag": first_reliable_lag,
                "hash_half_0_hedges_g": half0,
                "hash_half_1_hedges_g": half1,
                "hash_half_stable": hash_stable,
                "adjacent_lag_stable": adjacent_stable,
                "survives_declared_checks": (
                    first_reliable_lag is not None and hash_stable and adjacent_stable
                ),
            }
        )
    rankings.sort(
        key=lambda row: (
            not bool(row["survives_declared_checks"]),
            -abs(float(row["peak_hedges_g"])) if row["peak_hedges_g"] is not None else math.inf,
            str(row["feature_id"]),
        )
    )
    for index, row in enumerate(rankings, 1):
        row["rank"] = index
    return rankings


def _files(root: Path) -> tuple[dict[str, Any], ...]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"manifest.json", "READY"}:
            continue
        files.append(
            {"path": relative, "sha256": _sha256(path), "size_bytes": path.stat().st_size}
        )
    return tuple(files)


def _event_label_inputs(event_root: Path, label_root: Path) -> tuple[_ReportEventInput, ...]:
    event_rows = _load_jsonl(event_root / "events.jsonl")
    label_rows = _load_jsonl(label_root / "labels.jsonl")
    event_identity: dict[str, tuple[str, str]] = {}
    for row in event_rows:
        event = _mapping(row.get("event"), "event")
        event_id = _text(event.get("event_id"), "event.event_id")
        # EventRecord.security_id is the governed DWD identity. The accepted direct-DWD replay
        # adapter deliberately keys the P3 feature panel by listing_code instead. Keep those
        # namespaces distinct and bind the report to the replay-panel identity explicitly.
        panel_security_id = _text(row.get("listing_code"), "listing_code")
        signal_date = _text(event.get("signal_date"), "event.signal_date")
        event_identity[event_id] = (panel_security_id, signal_date)
    inputs: list[_ReportEventInput] = []
    for row in label_rows:
        if row.get("settlement_status") != "settled" or row.get("outcome") not in _ALLOWED_OUTCOMES:
            continue
        event_id = _text(row.get("event_id"), "label.event_id")
        identity = event_identity.get(event_id)
        if identity is None:
            raise ComparisonReportError("label corpus contains unknown event identity")
        panel_security_id, signal_date = identity
        hash_half = hashlib.sha256(event_id.encode("utf-8")).digest()[0] & 1
        inputs.append(
            _ReportEventInput(
                event_id=event_id,
                panel_security_id=panel_security_id,
                signal_date=signal_date,
                outcome=str(row["outcome"]),
                hash_half=hash_half,
            )
        )
    inputs.sort(key=lambda row: row.event_id)
    if len({row.event_id for row in inputs}) != len(inputs):
        raise ComparisonReportError("report input contains duplicate event ids")
    return tuple(inputs)


def _configure_deterministic_report_connection(connection: Any) -> None:
    """Freeze DuckDB reduction order for byte-identical report publication."""

    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = true")


def _aggregate_feature_rows(
    feature_panel_path: Path,
    event_inputs: Sequence[_ReportEventInput],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ComparisonReportError("P1 dependencies are required for P4 reporting") from exc
    connection = duckdb.connect(":memory:")
    try:
        _configure_deterministic_report_connection(connection)
        connection.execute(
            "CREATE TABLE event_labels (event_id VARCHAR, panel_security_id VARCHAR, "
            "signal_date DATE, outcome VARCHAR, hash_half INTEGER)"
        )
        connection.executemany(
            "INSERT INTO event_labels VALUES (?, ?, CAST(? AS DATE), ?, ?)",
            [item.as_row() for item in event_inputs],
        )
        escaped = str(feature_panel_path).replace("'", "''")
        connection.execute(
            "CREATE VIEW panel_indexed AS SELECT *, row_number() OVER ("
            "PARTITION BY security_id ORDER BY market_date) AS session_seq "
            f"FROM read_parquet('{escaped}')"
        )
        derived = """
            WITH signal_rows AS (
                SELECT e.event_id, e.panel_security_id, e.signal_date, e.outcome, e.hash_half,
                       p.session_seq AS signal_seq
                FROM event_labels e
                JOIN panel_indexed p
                  ON p.security_id = e.panel_security_id
                 AND p.market_date = e.signal_date
            ),
            aligned AS (
                SELECT e.event_id, e.outcome, e.hash_half,
                       CAST(e.signal_seq - h.session_seq AS INTEGER) AS lag,
                       CASE WHEN h.sma20 > 0 THEN h.total_return_index / h.sma20 - 1 END
                           AS price_to_sma20,
                       CASE WHEN h.sma60 > 0 THEN h.sma20 / h.sma60 - 1 END
                           AS sma20_to_sma60,
                       h.momentum20 AS momentum20,
                       CASE WHEN h.prior_high20 > 0
                            THEN h.total_return_index / h.prior_high20 - 1 END AS breakout20,
                       h.rsi14 AS rsi14,
                       h.volume_ratio20 AS volume_ratio20,
                       h.realized_volatility20 AS realized_volatility20
                FROM signal_rows e
                JOIN panel_indexed h
                  ON h.security_id = e.panel_security_id
                 AND h.session_seq BETWEEN e.signal_seq - 20 AND e.signal_seq
            ),
            long_features AS (
                SELECT event_id, outcome, hash_half, lag,
                       'price.price_to_sma20' AS feature_id, price_to_sma20 AS value FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'price.sma20_to_sma60', sma20_to_sma60 FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'price.momentum20', momentum20 FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'price.breakout20', breakout20 FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'price.rsi14', rsi14 FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'liquidity.volume_ratio20', volume_ratio20 FROM aligned
                UNION ALL SELECT event_id, outcome, hash_half, lag,
                       'risk.realized_volatility20', realized_volatility20 FROM aligned
            )
        """
        aligned_count = connection.execute(
            "WITH signal_rows AS (SELECT e.event_id FROM event_labels e JOIN panel_indexed p "
            "ON p.security_id=e.panel_security_id AND p.market_date=e.signal_date) "
            "SELECT count(*) FROM signal_rows"
        ).fetchone()
        if aligned_count is None or int(aligned_count[0]) != len(event_inputs):
            raise ComparisonReportError("not every three-class event aligns to its T feature row")
        group_cursor = connection.execute(
            derived
            + """
            SELECT feature_id, lag, outcome,
                   count(value) AS sample_count,
                   avg(value) AS mean,
                   stddev_samp(value) AS stddev,
                   quantile_cont(value, 0.25) AS q25,
                   quantile_cont(value, 0.50) AS median,
                   quantile_cont(value, 0.75) AS q75
            FROM long_features
            GROUP BY feature_id, lag, outcome
            ORDER BY feature_id, lag, outcome
            """
        )
        group_names = [str(item[0]) for item in group_cursor.description]
        group_rows = [dict(zip(group_names, row, strict=True)) for row in group_cursor.fetchall()]
        half_cursor = connection.execute(
            derived
            + """
            SELECT feature_id, lag, hash_half, outcome,
                   count(value) AS sample_count,
                   avg(value) AS mean,
                   stddev_samp(value) AS stddev
            FROM long_features
            WHERE outcome IN ('positive', 'neutral')
            GROUP BY feature_id, lag, hash_half, outcome
            ORDER BY feature_id, lag, hash_half, outcome
            """
        )
        half_names = [str(item[0]) for item in half_cursor.description]
        half_rows = [dict(zip(half_names, row, strict=True)) for row in half_cursor.fetchall()]
        return group_rows, half_rows
    finally:
        connection.close()


def _json_number(value: object) -> object:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return format(value, ".15g")
    return value


def _normalized_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: _json_number(value) for key, value in row.items()}
        for row in rows
    ]


def build_physical_comparison_report(
    *,
    event_root: Path,
    label_root: Path,
    provider_config_path: Path,
    feature_registry_path: Path,
    report_policy_path: Path,
    output: Path,
    code_revision: str,
    provider_session: ReplayDataSession | None = None,
    feature_panel: JobLocalFeaturePanel | None = None,
) -> ComparisonReportResult:
    """Build the sealed development daily group report from accepted immutable inputs."""

    if not code_revision.strip():
        raise ComparisonReportError("code_revision must not be empty")
    event_result = verify_physical_event_scan(event_root)
    label_result = verify_physical_label_corpus(label_root)
    policy = load_comparison_report_policy(report_policy_path)
    policy.require_reporting_authority()
    registry = load_feature_registry(feature_registry_path)
    if (
        registry.registry_version != policy.feature_registry_version
        or registry.digest != policy.feature_registry_digest
    ):
        raise ComparisonReportError("feature registry differs from report policy binding")
    try:
        event_manifest = json.loads((event_root / "manifest.json").read_text(encoding="utf-8"))
        label_manifest = json.loads((label_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonReportError("source Event/Label manifest is unreadable") from exc
    if not isinstance(event_manifest, dict) or not isinstance(label_manifest, dict):
        raise ComparisonReportError("source Event/Label manifest is invalid")
    outcome = _mapping(label_manifest.get("outcome_card"), "label.outcome_card")
    structural = _mapping(
        label_manifest.get("structural_break_card"),
        "label.structural_break_card",
    )
    if outcome.get("digest") != policy.outcome_card_digest:
        raise ComparisonReportError("label outcome digest differs from report policy")
    if structural.get("digest") != policy.structural_break_card_digest:
        raise ComparisonReportError("label structural digest differs from report policy")
    event_binding = _mapping(label_manifest.get("event_library"), "label.event_library")
    if event_binding.get("manifest_sha256") != event_result.manifest_sha256:
        raise ComparisonReportError("label corpus does not bind supplied Event Library")
    if event_binding.get("content_digest") != event_result.content_digest:
        raise ComparisonReportError("label corpus Event content digest differs")
    event_inputs = _event_label_inputs(event_root, label_root)
    if len(event_inputs) != label_result.three_class_events:
        raise ComparisonReportError("three-class report input count differs from label corpus")
    provider_config = load_replay_data_provider_config(provider_config_path)
    if provider_config.mode != "direct-dwd-job-local":
        raise ComparisonReportError("P4 reporting requires direct DWD job-local provider")
    provider = ReplayDataProvider(provider_config)
    if provider_session is not None:
        try:
            require_job_local_session(
                provider_session,
                provider_config,
                scope_start=_DEVELOPMENT_START,
                scope_end=_DEVELOPMENT_END,
            )
        except JobLocalArtifactError as exc:
            raise ComparisonReportError(str(exc)) from exc
    session_context = (
        provider.open(scope_start=_DEVELOPMENT_START, scope_end=_DEVELOPMENT_END)
        if provider_session is None
        else nullcontext(provider_session)
    )
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output_parent))
    panel_path = staging / ".feature-panel.parquet"
    try:
        with session_context as session:
            owns_feature_panel = feature_panel is None
            if feature_panel is None:
                panel_artifact = BaselineFeaturePanel.materialize(
                    session.reader.bundle,
                    registry,
                    scope_start=_DEVELOPMENT_START,
                    scope_end=_DEVELOPMENT_END,
                    output=panel_path,
                    data_content_digest=session.reader.data_content_digest,
                    data_manifest_sha256=session.reader.data_manifest_sha256,
                )
                active_panel_path = panel_artifact.path
                panel_report = panel_artifact.report
            else:
                try:
                    feature_panel.require_compatible(
                        session=session,
                        provider_config=provider_config,
                        registry=registry,
                        scope_start=_DEVELOPMENT_START,
                        scope_end=_DEVELOPMENT_END,
                    )
                except JobLocalArtifactError as exc:
                    raise ComparisonReportError(str(exc)) from exc
                active_panel_path = feature_panel.path
                panel_report = feature_panel.report
            group_rows_raw, half_rows_raw = _aggregate_feature_rows(
                active_panel_path, event_inputs
            )
        if owns_feature_panel and panel_path.exists():
            panel_path.unlink()
        expected_groups = len(_ALLOWED_FEATURES) * 21 * len(_ALLOWED_OUTCOMES)
        if len(group_rows_raw) != expected_groups:
            raise ComparisonReportError("group distribution matrix is incomplete")
        expected_halves = len(_ALLOWED_FEATURES) * 21 * 2 * 2
        if len(half_rows_raw) != expected_halves:
            raise ComparisonReportError("hash-half perturbation matrix is incomplete")
        contrasts = (("positive", "neutral"), ("positive", "failure"), ("neutral", "failure"))
        pairwise_raw = _pairwise_rows(group_rows_raw, contrasts)
        ranking_raw = _feature_ranking(pairwise_raw, half_rows_raw, policy)
        survivors = [row for row in ranking_raw if row["survives_declared_checks"]]
        leading = tuple(
            str(row["feature_id"]) for row in survivors[: policy.leading_feature_count]
        )
        acceptance_pass = len(leading) == policy.leading_feature_count
        group_rows = _normalized_rows(group_rows_raw)
        pairwise_rows = _normalized_rows(pairwise_raw)
        ranking_rows = _normalized_rows(ranking_raw)
        group_path = staging / "group-distributions.jsonl"
        pairwise_path = staging / "pairwise-effects.jsonl"
        ranking_path = staging / "feature-ranking.jsonl"
        summary_path = staging / "summary.json"
        _write_jsonl(group_path, group_rows)
        _write_jsonl(pairwise_path, pairwise_rows)
        _write_jsonl(ranking_path, ranking_rows)
        summary = {
            "schema_version": REPORT_SCHEMA,
            "candidate_to_report_attrition": {
                "candidate_events": event_result.candidate_events,
                "executable_events": event_result.executable_events,
                "not_executable_events": event_result.not_executable_events,
                "three_class_events": label_result.three_class_events,
                "unavailable_events": label_result.unavailable_events,
                "censored_events": label_result.censored_events,
            },
            "matrix": {
                "features": len(_ALLOWED_FEATURES),
                "lags": 21,
                "outcomes": len(_ALLOWED_OUTCOMES),
                "group_distribution_rows": len(group_rows),
                "pairwise_effect_rows": len(pairwise_rows),
            },
            "leading_features": list(leading),
            "surviving_feature_count": len(survivors),
            "acceptance_pass": acceptance_pass,
        }
        _write_json(summary_path, summary)
        files = _files(staging)
        content_digest = _digest(files)
        manifest = {
            "schema_version": REPORT_SCHEMA,
            "package_id": f"p4-comparison-report-{content_digest.removeprefix('sha256:')[:24]}",
            "content_digest": content_digest,
            "code_revision": code_revision,
            "research_only": True,
            "execution_authorized": False,
            "scope": {"partition": "development", "start": "2012-01-04", "end": "2021-12-30"},
            "report_policy": {
                "id": policy.policy_id,
                "version": policy.policy_version,
                "digest": policy.semantic_digest,
            },
            "event_library": {
                "manifest_sha256": event_result.manifest_sha256,
                "content_digest": event_result.content_digest,
            },
            "label_corpus": {
                "manifest_sha256": label_result.manifest_sha256,
                "content_digest": label_result.content_digest,
            },
            "feature_panel": {
                "registry_version": registry.registry_version,
                "registry_digest": registry.digest,
                "report_digest": panel_report.digest,
                "bundle_snapshot_id": panel_report.bundle_snapshot_id,
                "bundle_digest": panel_report.bundle_digest,
            },
            "leading_features": list(leading),
            "acceptance_pass": acceptance_pass,
            "files": list(files),
        }
        manifest_path = staging / "manifest.json"
        _write_json(manifest_path, manifest)
        manifest_sha = _sha256(manifest_path)
        (staging / "READY").write_text(manifest_sha + "\n", encoding="ascii")
        if output.exists():
            raise FileExistsError(f"refusing to overwrite P4 comparison report: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, output)
        return verify_physical_comparison_report(output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_physical_comparison_report(root: Path) -> ComparisonReportResult:
    """Verify one sealed report package without trusting manifest hashes or summary counts."""

    if root.is_symlink() or not root.is_dir():
        raise ComparisonReportError("P4 comparison report must be a physical directory")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonReportError("P4 comparison report manifest/summary is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != REPORT_SCHEMA:
        raise ComparisonReportError("P4 comparison report manifest schema differs")
    if not isinstance(summary, dict) or summary.get("schema_version") != REPORT_SCHEMA:
        raise ComparisonReportError("P4 comparison report summary schema differs")
    manifest_sha = _sha256(root / "manifest.json")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ComparisonReportError("P4 comparison report READY is unreadable") from exc
    if ready != manifest_sha:
        raise ComparisonReportError("P4 comparison report READY does not bind its manifest")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise ComparisonReportError("P4 comparison report file inventory is invalid")
    observed: list[dict[str, Any]] = []
    expected_paths = {"manifest.json", "READY"}
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise ComparisonReportError("P4 comparison report file row is invalid")
        relative = _safe_relative(raw.get("path"))
        path = root.joinpath(*relative.parts)
        expected_paths.add(relative.as_posix())
        if not path.is_file() or path.is_symlink():
            raise ComparisonReportError("P4 comparison report file is missing or symlinked")
        observed.append(
            {
                "path": relative.as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    actual_paths = {
        item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()
    }
    if actual_paths != expected_paths:
        raise ComparisonReportError("P4 comparison report physical inventory differs")
    if observed != raw_files:
        raise ComparisonReportError("P4 comparison report file hashes differ")
    content_digest = _digest(observed)
    if manifest.get("content_digest") != content_digest:
        raise ComparisonReportError("P4 comparison report content digest differs")
    group_rows = _load_jsonl(root / "group-distributions.jsonl")
    pairwise_rows = _load_jsonl(root / "pairwise-effects.jsonl")
    ranking_rows = _load_jsonl(root / "feature-ranking.jsonl")
    if len(group_rows) != len(_ALLOWED_FEATURES) * 21 * 3:
        raise ComparisonReportError("P4 report group matrix row count differs")
    if len(pairwise_rows) != len(_ALLOWED_FEATURES) * 21 * 3:
        raise ComparisonReportError("P4 report pairwise matrix row count differs")
    if len(ranking_rows) != len(_ALLOWED_FEATURES):
        raise ComparisonReportError("P4 report feature ranking row count differs")
    survivors = [row for row in ranking_rows if row.get("survives_declared_checks") is True]
    expected_leading = [str(row["feature_id"]) for row in survivors[:3]]
    if summary.get("leading_features") != expected_leading:
        raise ComparisonReportError("P4 report summary leading features differ from ranking")
    if manifest.get("leading_features") != expected_leading:
        raise ComparisonReportError("P4 report manifest leading features differ from ranking")
    attrition = _mapping(summary.get("candidate_to_report_attrition"), "summary.attrition")
    candidate_events = int(attrition.get("candidate_events", -1))
    three_class_events = int(attrition.get("three_class_events", -1))
    acceptance_pass = len(expected_leading) == 3
    if bool(summary.get("acceptance_pass")) != acceptance_pass:
        raise ComparisonReportError("P4 report summary acceptance flag differs")
    if bool(manifest.get("acceptance_pass")) != acceptance_pass:
        raise ComparisonReportError("P4 report manifest acceptance flag differs")
    return ComparisonReportResult(
        root=root.resolve(),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        candidate_events=candidate_events,
        three_class_events=three_class_events,
        leading_features=tuple(expected_leading),
        acceptance_pass=acceptance_pass,
    )
