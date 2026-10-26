"""One prospective temporal representation; no source repair or consumer activation.

The immutable forty-session prerequisite is shared with the original seven facts.
Only the two declared Decimal temporal ratios are added. This module cannot admit
sources/references, choose samples, change old scores, or enable an expert/P6.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.stage_learning.head2_facts import Head2WindowFacts, build_head2_window_facts
from quanthus.stage_learning.head2_revision import (
    FEATURE_NAMES as ORIGINAL_FEATURE_NAMES,
)
from quanthus.stage_learning.head2_revision import (
    FeatureEncoding,
    _features,
    encode_reference_facts,
)
from quanthus.stage_learning.head2_revision import (
    feature_contract_digest as original_contract_digest,
)
from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadCheckpoint,
    HeadPolicy,
    HeadRow,
    _row_digest,
    _rows,
    fit_head,
    predict_head,
)
from quanthus.stage_learning.p5_assessment import P5AssessmentError
from quanthus.structure_v2_inputs import Admission, digest

VERSION = "head2-temporal-facts-v1"
TEMPORAL_NAMES = ("late_outside_earlier_range_fraction", "midrange_side_transition_fraction")
FEATURE_NAMES = (*ORIGINAL_FEATURE_NAMES, *TEMPORAL_NAMES)


def feature_contract_digest() -> str:
    return digest({
        "version": VERSION, "base": original_contract_digest(), "columns": FEATURE_NAMES,
        "late": "strict outside min(low1..15),max(high1..15), last5 closes /5",
        "transitions": "current20 midpoint; remove exact-zero signs; changes/max(1,n-1)",
        "arithmetic": "Decimal local precision28; no epsilon; <2 signs=0:no-observed-transition",
        "admission": "same exact40 one coordinate; no missing/reset/future interpolation",
    })


@dataclass(frozen=True)
class TemporalWindowFacts:
    base: Head2WindowFacts
    values: tuple[Decimal, Decimal] | None
    reasons: tuple[str, str]

    @property
    def available(self) -> bool:
        return self.base.available and self.values is not None

    @property
    def digest(self) -> str:
        return digest({"contract": feature_contract_digest(), "base": self.base.digest,
                       "values": self.values, "reasons": self.reasons})


def build_temporal_facts(
    admissions: Sequence[Admission], *, anchor: date,
) -> TemporalWindowFacts:
    """Validate the complete prerequisite before calculating current20 facts."""
    base = build_head2_window_facts(admissions, anchor=anchor)
    if not base.available:
        return TemporalWindowFacts(base, None, (base.reason, base.reason))
    rows = tuple(a.row for a in admissions[-20:])
    # The shared admission above rejects None and mixed coordinates; retain values.
    highs = tuple(r.high for r in rows)
    lows = tuple(r.low for r in rows)
    closes = tuple(r.close for r in rows)
    assert all(isinstance(v, Decimal) and v.is_finite() for v in (*highs, *lows, *closes))
    valid_highs = tuple(v for v in highs if v is not None)
    valid_lows = tuple(v for v in lows if v is not None)
    valid_closes = tuple(v for v in closes if v is not None)
    # Match deterministic Decimal context of the admitted kernel; no float epsilon.
    with localcontext(Context(prec=28)):
        earlier_low, earlier_high = min(valid_lows[:15]), max(valid_highs[:15])
        outside = sum(c < earlier_low or c > earlier_high for c in valid_closes[15:])
        # Comparisons need an exact midpoint even for source values >28 digits.
        extrema = (max(valid_highs), min(valid_lows))
        exact_precision = max(28, max(v.adjusted() for v in extrema)
                              - min(int(v.as_tuple().exponent) for v in extrema) + 4)
        with localcontext(Context(prec=exact_precision)):
            midpoint = sum(extrema, Decimal(0)) / Decimal(2)
        signs = tuple(1 if c > midpoint else -1 for c in valid_closes if c != midpoint)
        changes = sum(a != b for a, b in zip(signs, signs[1:], strict=False))
        late = Decimal(outside) / Decimal(5)
        transitions = Decimal(changes) / Decimal(max(1, len(signs) - 1))
    return TemporalWindowFacts(
        base, (late, transitions),
        ("available", "no-observed-transition" if len(signs) < 2 else "available"),
    )


def encode_temporal_features(facts: TemporalWindowFacts, *, source_digest: str) -> FeatureEncoding:
    primary, path = facts.base.reference_facts()
    identity = digest({"facts": facts.digest, "source": source_digest})
    if not facts.available:
        # Original unavailable bundles contain no fact records; no fabricated fields.
        from quanthus.stage_learning.p5_contracts import require_digest
        require_digest(source_digest)
        return FeatureEncoding(None, identity, "REQUIRED_FACT_UNAVAILABLE")
    original = encode_reference_facts(primary, path, anchor=facts.base.anchor.isoformat(),
                                      source_digest=source_digest)
    if original.values is None:
        return FeatureEncoding(None, identity, original.reason)
    assert facts.values is not None
    values = (*original.values, *(float(v) for v in facts.values))
    _validate_features(values)
    return FeatureEncoding(values, digest({"identity": identity, "original": original.digest}),
                           original.reason)


def _validate_features(values: Sequence[float]) -> None:
    if len(values) not in (7, 9):
        raise P5AssessmentError("head2_temporal_feature_width")
    _features(values[:7])
    if any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1
           for v in values[7:]):
        raise P5AssessmentError("head2_temporal_feature_range")


@dataclass(frozen=True)
class MaskSupport:
    rows_digest: str
    observed: tuple[int, ...]

    @property
    def digest(self) -> str:
        return digest({"version": "head2-training-mask-support-v1", **asdict(self)})


def mask_support(rows: Sequence[HeadRow]) -> MaskSupport:
    if not rows:
        raise P5AssessmentError("head2_temporal_empty_support")
    ordered = _rows(rows, len(rows[0].features))
    for r in ordered:
        _validate_features(r.features)
    return MaskSupport(_row_digest(ordered), tuple(sorted({int(r.features[6]) for r in ordered})))


def fit_matched_heads(
    train: Sequence[HeadRow], *, seven_bindings: HeadBindings, nine_bindings: HeadBindings,
) -> tuple[HeadCheckpoint, HeadCheckpoint]:
    """Exactly two primary fits on one prospectively admitted40-row cohort.

    This does not qualify labels/sources or replace the caller's frozen request.
    Reproduction must be a separately admitted execution, never an extra search.
    """
    ordered = _rows(train, 9)
    seven_bindings.validate()
    nine_bindings.validate()
    if (len(ordered) != 40 or sum(r.label for r in ordered) != 12
            or len({r.security_id for r in ordered}) != 40
            or len({r.episode_id for r in ordered}) != 40
            or max(r.anchor for r in ordered) > "2021-08-31"):
        raise P5AssessmentError("head2_temporal_exact_train40")
    for r in ordered:
        _validate_features(r.features)
    # Every binding except the representation is identical across arms.
    if (seven_bindings.features != original_contract_digest()
            or nine_bindings.features != feature_contract_digest()
            or {k: v for k, v in asdict(seven_bindings).items() if k != "features"}
            != {k: v for k, v in asdict(nine_bindings).items() if k != "features"}):
        raise P5AssessmentError("head2_temporal_matched_bindings")
    seven = tuple(HeadRow(r.sample_id, r.security_id, r.episode_id, r.anchor,
                          r.features[:7], r.label) for r in ordered)
    policy7 = HeadPolicy(ORIGINAL_FEATURE_NAMES, 200, 100, .05, .01, .6931471805599453)
    policy9 = HeadPolicy(FEATURE_NAMES, 200, 100, .05, .01, .6931471805599453)
    control = fit_head(seven, policy=policy7, bindings=seven_bindings)
    candidate = fit_head(ordered, policy=policy9, bindings=nine_bindings)
    return control, candidate


@dataclass(frozen=True)
class SupportedPrediction:
    raw_score: float | None
    reason: str
    support_digest: str
    calibrated_probability: None = None
    consumer_admission: str = "NOT_GRANTED"


def predict_supported(
    checkpoint: HeadCheckpoint, features: Sequence[float], *, support: MaskSupport,
) -> SupportedPrediction:
    checkpoint.validate()
    _validate_features(features)
    expected_names = ORIGINAL_FEATURE_NAMES if len(features) == 7 else FEATURE_NAMES
    expected_contract = (original_contract_digest() if len(features) == 7
                         else feature_contract_digest())
    if (checkpoint.policy.feature_names != expected_names
            or checkpoint.bindings.features != expected_contract):
        raise P5AssessmentError("head2_temporal_model_feature_binding")
    mean = checkpoint.means[6]
    expected_support = (0,) if mean == 0 else (1,) if mean == 1 else (0, 1)
    if (checkpoint.rows_digest != support.rows_digest or not support.observed
            or support.observed != expected_support
            or tuple(sorted(set(support.observed))) != support.observed
            or any(type(v) is not int or v not in (0, 1) for v in support.observed)):
        raise P5AssessmentError("head2_temporal_support_binding")
    if len(features) != len(checkpoint.weights) or not checkpoint.complete:
        raise P5AssessmentError("head2_temporal_model_shape_or_incomplete")
    if int(features[6]) not in support.observed:
        return SupportedPrediction(None, "ABSTAIN_UNSEEN_ZERO_NET_GEOMETRY", support.digest)
    prediction = predict_head(checkpoint, features)
    return SupportedPrediction(prediction.raw_score, "RESEARCH_SCORE_ONLY", support.digest)


def _physical(path: Path) -> None:
    # Resolve each ancestor as well as a possibly nonexistent leaf.
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("HEAD2_TEMPORAL_NONPHYSICAL_PATH")
    if path.is_symlink():
        raise ValueError("HEAD2_TEMPORAL_SYMLINK")


def _save(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def dispatch_temporal_stage(
    *, output: Path, stage: str, command: Sequence[str], inputs: Mapping[Path, str],
    deadline: datetime, free_floor_bytes: int, reserved_bytes: int,
) -> None:
    """Preflight a single Head2 launch; record its identity before invoking it.

    The caller supplies a frozen versioned request/code/input closure. Source and
    reference qualification remain separate. There is no retry, budget extension,
    network permission or source fallback. Failure leaves a launch receipt/logs.
    """
    if stage not in {"fit-seven", "fit-nine", "blind-reference-a", "blind-reference-b",
                     "independent-reproduction"}:
        raise ValueError("HEAD2_TEMPORAL_STAGE")
    if (not command or any(not isinstance(c, str) or not c for c in command) or not inputs
            or type(free_floor_bytes) is not int or free_floor_bytes < 0
            or type(reserved_bytes) is not int or reserved_bytes < 0):
        raise ValueError("HEAD2_TEMPORAL_LAUNCH_CONTRACT")
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None or now >= deadline:
        raise ValueError("HEAD2_TEMPORAL_DEADLINE")
    _physical(output)
    if output.exists():
        raise FileExistsError(output)
    if not output.parent.is_dir():
        raise ValueError("HEAD2_TEMPORAL_PARENT")
    stats = os.statvfs(output.parent)
    if stats.f_bavail * stats.f_frsize - reserved_bytes < free_floor_bytes:
        raise ValueError("HEAD2_TEMPORAL_SPACE")
    records = []
    for path, expected in sorted(inputs.items()):
        _physical(path)
        if output in path.parents or path in output.parents or not path.is_file():
            raise ValueError("HEAD2_TEMPORAL_INPUT_OUTPUT_OVERLAP")
        before = path.stat()
        if before.st_nlink != 1:
            raise ValueError("HEAD2_TEMPORAL_LINKED_INPUT")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
        if (actual != expected or before != after):
            raise ValueError("HEAD2_TEMPORAL_INPUT_CHANGED")
        records.append({"path": str(path), "sha256": actual, "bytes": before.st_size})
    # All admission checks above occur before any output side effect.
    output.mkdir()
    receipt = {"kind": "head2-temporal-launch-v1", "stage": stage,
               "status": "PREPARED_NOT_EXECUTED", "UTC": now.isoformat(),
               "deadline_utc": deadline.isoformat(), "command": list(command),
               "inputs": records, "device": output.parent.stat().st_dev,
               "free_bytes": stats.f_bavail * stats.f_frsize,
               "reserved_bytes": reserved_bytes, "free_floor_bytes": free_floor_bytes}
    _save(output / "launch.json", receipt)
    directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    with (output / "stdout.txt").open("xb") as stdout, (output / "stderr.txt").open("xb") as stderr:
        remaining = max(.001, (deadline - datetime.now(timezone.utc)).total_seconds())
        result = subprocess.run(list(command), cwd=output, stdout=stdout, stderr=stderr,
                                timeout=remaining,
                                check=True)
    _save(output / "complete.json", {"status": "COMPLETE", "returncode": result.returncode,
                                    "UTC": datetime.now(timezone.utc).isoformat()})
