"""Development-only continuous morphology profile for third-group Stage 0/1/2/8/9/12.

This runner emits no stage labels and performs no parameter search. It reads the frozen 512-security
2020-2021 development cohort once and measures T-only order/compression, downside lifecycle and
high-side acceleration/divergence axes. The result is descriptive evidence for a later semantic
review; it does not formalize B6/B7 or choose stage thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
)
from quanthus.structure_v2_inputs import Admission, DailyInput, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/third-group-profile.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageThirdGroupProfileError(ValueError):
    """Raised when the third-group development profile contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageThirdGroupProfileError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageThirdGroupProfileError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageThirdGroupProfileError(f"invalid third-group date: {key}") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-third-group-profile-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_third_group_profile_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "third-group profile config")
    expected = {
        "schema_version",
        "profile_id",
        "work_item",
        "purpose",
        "training_node_config",
        "authority",
        "development_window",
        "population",
        "semantic_subgroups",
        "observation_contract",
        "measurements",
        "artifact",
    }
    if set(raw) != expected or raw.get("schema_version") != "quanthus.stage-third-group-profile.v1":
        raise StageThirdGroupProfileError("third-group profile config schema differs")
    if raw.get("work_item") != "P3-STAGE-003-B":
        raise StageThirdGroupProfileError("third-group profile work item differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageThirdGroupProfileError("third-group authority missing")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
        "continuous_profile_output_authorized",
    }
    required_false = set(authority) - required_true
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageThirdGroupProfileError("third-group authority differs")
    window = raw.get("development_window")
    population = raw.get("population")
    measurements = raw.get("measurements")
    subgroups = raw.get("semantic_subgroups")
    if not all(isinstance(item, Mapping) for item in (window, population, measurements, subgroups)):
        raise StageThirdGroupProfileError("third-group profile sections missing")
    scan = _date(window, "cohort_scan_start")
    cohort = _date(window, "cohort_date")
    warmup = _date(window, "warmup_start")
    first = _date(window, "report_start")
    last = _date(window, "report_end")
    if not (date(2012, 1, 1) <= scan <= warmup <= cohort < first <= last <= date(2021, 12, 31)):
        raise StageThirdGroupProfileError("third-group profile must stay in development dates")
    if (
        population.get("cohort_size") != 512
        or population.get("minimum_contiguous_history_sessions") != 61
    ):
        raise StageThirdGroupProfileError("third-group frozen population/history differs")
    expected_codes = {
        "order_consolidation": [0, 2],
        "downside_lifecycle": [1, 12],
        "high_side_modifiers": [8, 9],
    }
    observed_codes = {
        key: value.get("candidate_stage_codes") for key, value in subgroups.items()
    }
    if observed_codes != expected_codes:
        raise StageThirdGroupProfileError("third-group semantic decomposition differs")
    quantiles = measurements.get("quantiles")
    if (
        not isinstance(quantiles, list)
        or not quantiles
        or quantiles != sorted(set(quantiles))
        or any(not isinstance(item, (int, float)) or not 0 < float(item) < 1 for item in quantiles)
        or measurements.get("quantile_method") != "nearest_rank"
        or measurements.get("no_stage_threshold_is_derived_inside_this_job") is not True
        or measurements.get("no_state_or_modifier_is_selected_inside_this_job") is not True
    ):
        raise StageThirdGroupProfileError("third-group quantile/selection contract differs")
    return raw


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise StageThirdGroupProfileError("profile ratio denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise StageThirdGroupProfileError("profile mean requires values")
    with localcontext(Context(prec=28)):
        return sum(values, Decimal(0)) / Decimal(len(values))


def _return(rows: Sequence[DailyInput], sessions: int) -> Decimal:
    assert rows[-1].close is not None and rows[-sessions - 1].close is not None
    return _ratio(rows[-1].close, rows[-sessions - 1].close)


def _signed_efficiency(rows: Sequence[DailyInput], sessions: int) -> Decimal:
    sample = rows[-sessions - 1 :]
    closes = [item.close for item in sample]
    if any(item is None for item in closes):
        raise StageThirdGroupProfileError("efficiency requires admitted closes")
    exact = [item for item in closes if item is not None]
    path = sum(
        (abs(right - left) for left, right in zip(exact[:-1], exact[1:], strict=True)),
        Decimal(0),
    )
    if path == 0:
        return Decimal(0)
    with localcontext(Context(prec=28)):
        return (exact[-1] - exact[0]) / path


def _range(rows: Sequence[DailyInput]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    highs = [item.high for item in rows]
    lows = [item.low for item in rows]
    if any(item is None for item in highs) or any(item is None for item in lows):
        raise StageThirdGroupProfileError("range requires admitted OHLC")
    high = max(item for item in highs if item is not None)
    low = min(item for item in lows if item is not None)
    midpoint = (high + low) / Decimal(2)
    if midpoint <= 0:
        raise StageThirdGroupProfileError("range midpoint must be positive")
    width = (high - low) / midpoint
    return high, low, midpoint, width


def _position(close: Decimal, high: Decimal, low: Decimal) -> Decimal | None:
    if high == low:
        return None
    with localcontext(Context(prec=28)):
        return (close - low) / (high - low)


def _volume_ratio(current: Sequence[DailyInput], baseline: Sequence[DailyInput]) -> Decimal | None:
    current_values = [item.volume for item in current]
    baseline_values = [item.volume for item in baseline]
    if any(item is None for item in current_values + baseline_values):
        return None
    current_exact = [item for item in current_values if item is not None]
    baseline_exact = [item for item in baseline_values if item is not None]
    prior = _mean(baseline_exact)
    if prior <= 0:
        return None
    return _ratio(_mean(current_exact), prior) + Decimal(1)


def build_observation_metrics(rows: Sequence[DailyInput]) -> dict[str, Decimal | None]:
    """Compute one T-only third-group morphology row from >=61 contiguous admitted sessions."""
    if len(rows) < 61:
        raise StageThirdGroupProfileError("third-group observation requires 61 sessions")
    rows = rows[-61:]
    current = rows[-1]
    if current.close is None:
        raise StageThirdGroupProfileError("profile observation lacks current close")
    ret5 = _return(rows, 5)
    ret10 = _return(rows, 10)
    ret20 = _return(rows, 20)
    ret60 = _return(rows, 60)
    h10, l10, m10, w10 = _range(rows[-10:])
    h20, l20, m20, w20 = _range(rows[-20:])
    h60, l60, _m60, w60 = _range(rows[-60:])
    _ph10, _pl10, pm10, pw10 = _range(rows[-20:-10])
    _ph20, _pl20, pm20, pw20 = _range(rows[-40:-20])
    if pw10 == 0 or pw20 == 0:
        width_change10 = None if pw10 == 0 else _ratio(w10, pw10)
        width_change20 = None if pw20 == 0 else _ratio(w20, pw20)
    else:
        width_change10 = _ratio(w10, pw10)
        width_change20 = _ratio(w20, pw20)
    velocity5 = ret5 / Decimal(5)
    velocity20 = ret20 / Decimal(20)
    return {
        "return_5_sessions": ret5,
        "return_10_sessions": ret10,
        "return_20_sessions": ret20,
        "return_60_sessions": ret60,
        "signed_directional_efficiency_5": _signed_efficiency(rows, 5),
        "signed_directional_efficiency_20": _signed_efficiency(rows, 20),
        "range_width_10": w10,
        "range_width_20": w20,
        "range_width_60": w60,
        "range_width_change_10_vs_prior10": width_change10,
        "range_width_change_20_vs_prior20": width_change20,
        "center_shift_10_vs_prior10": _ratio(m10, pm10),
        "center_shift_20_vs_prior20": _ratio(m20, pm20),
        "position_in_range_20": _position(current.close, h20, l20),
        "position_in_range_60": _position(current.close, h60, l60),
        "drawdown_from_high_20": _ratio(current.close, h20),
        "drawdown_from_high_60": _ratio(current.close, h60),
        "rebound_from_low_20": _ratio(current.close, l20),
        "rebound_from_low_60": _ratio(current.close, l60),
        "return_velocity_5_per_session": velocity5,
        "return_velocity_20_per_session": velocity20,
        "velocity_delta_5_vs_20": velocity5 - velocity20,
        "volume_ratio_20": _volume_ratio(rows[-1:], rows[-21:-1]),
        "recent_volume_ratio_5_to_prior20": _volume_ratio(rows[-5:], rows[-25:-5]),
    }


def _quantiles(values: Sequence[Decimal], probabilities: Sequence[float]) -> dict[str, str] | None:
    if not values:
        return None
    ordered = sorted(values)
    output: dict[str, str] = {}
    for probability in probabilities:
        rank = max(1, math.ceil(float(probability) * len(ordered)))
        output[f"p{int(round(float(probability) * 100)):02d}"] = format(ordered[rank - 1], "f")
    return output


def _sign(value: Decimal | None, negative: str, zero: str, positive: str) -> str:
    if value is None:
        return "unavailable"
    if value < 0:
        return negative
    if value > 0:
        return positive
    return zero


def _position_band(value: Decimal | None, low: Decimal, high: Decimal) -> str:
    if value is None:
        return "undefined"
    if value <= low:
        return "lower_third"
    if value >= high:
        return "upper_third"
    return "middle_third"


def _efficiency_band(value: Decimal | None, low: Decimal, high: Decimal) -> str:
    if value is None:
        return "undefined"
    magnitude = abs(value)
    if magnitude <= low:
        return "low_abs_efficiency"
    if magnitude >= high:
        return "high_abs_efficiency"
    return "middle_abs_efficiency"


def summarize_metrics(
    observations: Sequence[Mapping[str, Decimal | None]],
    *,
    metric_names: Sequence[str],
    quantiles: Sequence[float],
    bands: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int], dict[str, int]]:
    metric_profiles: dict[str, Any] = {}
    for name in metric_names:
        values = [item[name] for item in observations if item.get(name) is not None]
        exact = [item for item in values if item is not None]
        metric_profiles[name] = {"count": len(exact), "quantiles": _quantiles(exact, quantiles)}
    low_position = Decimal(str(bands["position_lower_third_max"]))
    high_position = Decimal(str(bands["position_upper_third_min"]))
    low_eff = Decimal(str(bands["efficiency_low_abs_max"]))
    high_eff = Decimal(str(bands["efficiency_high_abs_min"]))
    joint: dict[str, int] = defaultdict(int)
    slices: dict[str, int] = defaultdict(int)
    for item in observations:
        direction = _sign(item["return_20_sessions"], "down20", "flat20", "up20")
        acceleration = _sign(
            item["velocity_delta_5_vs_20"], "slower_recent", "same_recent", "faster_recent"
        )
        width = _sign(
            item["range_width_change_10_vs_prior10"], "contracting10", "unchanged10", "expanding10"
        )
        position = _position_band(item["position_in_range_60"], low_position, high_position)
        efficiency = _efficiency_band(item["signed_directional_efficiency_20"], low_eff, high_eff)
        key = "|".join((direction, acceleration, width, position, efficiency))
        joint[key] += 1
        for name in (direction, acceleration, width, position, efficiency):
            slices[name] += 1
    return metric_profiles, dict(sorted(joint.items())), dict(sorted(slices.items()))


def build_third_group_profile(
    admissions: Sequence[Admission],
    *,
    report_start: date,
    report_end: date,
) -> tuple[tuple[dict[str, Decimal | None], ...], dict[str, int]]:
    segments: dict[str, deque[DailyInput]] = defaultdict(lambda: deque(maxlen=61))
    resets = {
        "nonadmitted": 0,
        "listing_or_coordinate_change": 0,
        "hidden_session_gap": 0,
        "profiled_rows": 0,
    }
    observations: list[dict[str, Decimal | None]] = []
    for attempt in sorted(
        admissions, key=lambda item: (item.row.market_date, item.row.security_code)
    ):
        row = attempt.row
        segment = segments[row.security_code]
        previous = segment[-1] if segment else None
        if not attempt.admitted or row.close is None or row.high is None or row.low is None:
            if segment:
                resets["nonadmitted"] += 1
            segment.clear()
            continue
        if previous is not None and (
            row.listing_id != previous.listing_id or row.coordinate_ref != previous.coordinate_ref
        ):
            resets["listing_or_coordinate_change"] += 1
            segment.clear()
            previous = None
        if previous is not None and row.session_index != previous.session_index + 1:
            resets["hidden_session_gap"] += 1
            segment.clear()
        segment.append(row)
        if not report_start <= row.market_date <= report_end or len(segment) < 61:
            continue
        observations.append(build_observation_metrics(tuple(segment)))
        resets["profiled_rows"] += 1
    return tuple(observations), resets


def run_third_group_profile(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageThirdGroupProfileError("third-group profile requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_third_group_profile_config(config_path)
    node_config = _object(root / str(config["training_node_config"]), "training node config")
    window = config["development_window"]
    ranked = discover_development_security_population(
        training_root=training_root,
        node_config=node_config,
        scan_start=_date(window, "cohort_scan_start"),
        cohort_date=_date(window, "cohort_date"),
        minimum_observations=int(window["minimum_pre_cohort_observations"]),
        seed=str(window["cohort_seed"]),
    )
    cohort_size = int(config["population"]["cohort_size"])
    if len(ranked) < cohort_size:
        raise StageThirdGroupProfileError("development population smaller than frozen cohort")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)
    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-third-group-profile-definition.v1",
            "profile_id": config["profile_id"],
            "semantic_subgroups": config["semantic_subgroups"],
            "observation_contract": config["observation_contract"],
            "measurements": config["measurements"],
            "official_stage_labels": False,
        }
    )
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    admissions, source_manifest, _policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=DevelopmentReadScope(_date(window, "warmup_start"), report_start, report_end),
        security_codes=cohort,
        spec_digest=spec_digest,
    )
    _progress(started, "source-ready", admissions=len(admissions))
    observations, reset_counts = build_third_group_profile(
        admissions, report_start=report_start, report_end=report_end
    )
    _progress(started, "profile-ready", observation_count=len(observations), **reset_counts)
    metric_names = tuple(str(item) for item in config["measurements"]["metrics"])
    quantiles = tuple(float(item) for item in config["measurements"]["quantiles"])
    metric_profiles, joint, slices = summarize_metrics(
        observations,
        metric_names=metric_names,
        quantiles=quantiles,
        bands=config["measurements"]["fixed_diagnostic_bands"],
    )
    run_id = "stage-third-group-profile-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": "quanthus.stage-third-group-profile-summary.v1",
        "work_item": "P3-STAGE-003-B",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_id": config["profile_id"],
        "profile_config_digest": digest(config),
        "development_only": True,
        "eligible_population_count": len(ranked),
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "report_start": report_start,
        "report_end": report_end,
        "admission_count": len(admissions),
        "profile_observation_count": len(observations),
        "reset_counts": reset_counts,
        "metric_profiles": metric_profiles,
        "joint_diagnostic_counts": joint,
        "diagnostic_slice_counts": slices,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "state_or_modifier_selected": False,
        "formal_structure_fact_registered": False,
        "future_settlement_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "strategy_result_read": False,
        "elapsed_seconds": time.perf_counter() - started,
        "source_manifest_digest": digest(source_manifest),
        "claim_limits": [
            "Stage 0/1/2/8/9/12 are not emitted by this profile",
            (
                "8/9 modifier status is a hypothesis to be reviewed after the profile, "
                "not a Job result"
            ),
            "daily profile rows are correlated observations, not independent semantic samples",
            "job-local morphology metrics are not automatically registered Structure Facts",
            "no parameter threshold or state boundary is selected inside this Job",
            "2022-2024 arbitration is not reread and 2025 permanent blind remains untouched",
        ],
    }
    payload = canonical(summary)
    (run_root / "summary.json").write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-third-group-profile-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_config_digest": digest(config),
        "files": {"summary.json": _sha256(payload)},
        "development_only": True,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "formal_structure_fact_registered": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_root": str(run_root), "manifest_sha256": ready}


def verify_third_group_profile_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "third-group profile manifest")
    if manifest.get("schema_version") != "quanthus.stage-third-group-profile-artifact.v1":
        raise StageThirdGroupProfileError("third-group profile artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageThirdGroupProfileError("third-group profile READY unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageThirdGroupProfileError("third-group profile READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageThirdGroupProfileError("third-group profile code revision differs")
    summary_path = root / "summary.json"
    summary = _object(summary_path, "third-group profile summary")
    if _sha256(summary_path.read_bytes()) != manifest.get("files", {}).get("summary.json"):
        raise StageThirdGroupProfileError("third-group profile summary digest differs")
    if (
        summary.get("development_only") is not True
        or summary.get("stage_labels_emitted") is not False
        or summary.get("parameter_search_performed") is not False
        or summary.get("state_or_modifier_selected") is not False
        or summary.get("formal_structure_fact_registered") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("holdout_population_selected") is not False
    ):
        raise StageThirdGroupProfileError("third-group profile authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": 1,
        "verified": True,
        "development_only": True,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "profile_observation_count": summary["profile_observation_count"],
        "reset_counts": summary["reset_counts"],
        "metric_profiles": summary["metric_profiles"],
        "joint_diagnostic_counts": summary["joint_diagnostic_counts"],
        "diagnostic_slice_counts": summary["diagnostic_slice_counts"],
        "arbitration_read": False,
        "permanent_blind_read": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_third_group_profile(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_third_group_profile_artifact(
            args.artifact,
            code_revision=args.code_revision,
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
