"""Bounded synthetic adapter round trip through the existing Stage worker protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

from quanthus.stage_learning.p5_adapters import (
    BaselineAdapter,
    TrainingExample,
    base_rate_receipt,
    t1_admission_status,
    temporal_predict,
    temporal_state_digest,
)
from quanthus.stage_learning.p5_assessment import (
    FeatureCell,
    FeatureWindow,
    P5AssessmentError,
    TemporalEncoderConfig,
    build_t0_model,
)
from quanthus.stage_learning.p5_contracts import (
    PROPOSITIONS,
    REQUEST_DIGEST,
    TARGET_DIGEST,
    ModelIdentity,
    SampleIdentity,
    aggregation_receipt,
    head_set_digest,
    project_output,
)

CONFIG = {
    "schema_version": "quanthus.p5-synthetic-worker.v1",
    "synthetic_only": True,
    "market_row_read": False,
    "formal_seed_budget_consumed": 0,
    "heads": [2, 3],
    "input_channels": 2,
    "samples": 24,
    "T1_admitted": False,
}


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def code_digest() -> str:
    root = Path(__file__).parent
    return digest(
        b"".join(
            (root / name).read_bytes()
            for name in (
                "p5_synthetic_runner.py",
                "p5_assessment.py",
                "p5_contracts.py",
                "p5_adapters.py",
            )
        )
    )


def synthetic_summary() -> dict:
    import torch

    torch.set_num_threads(1)
    heads = (2, 3)
    cfg = TemporalEncoderConfig(input_channels=2)
    days = tuple(
        date(2014, 1, 1) + timedelta(days=i)
        for i in range(168)
        if (date(2014, 1, 1) + timedelta(days=i)).weekday() < 5
    )
    assert len(days) == 120
    rows = []
    for index in range(CONFIG["samples"]):
        sample = SampleIdentity(
            f"synthetic-{index}", "SYNTHETIC_ONLY", days[-1], f"episode-{index}"
        )
        window = FeatureWindow(
            days,
            tuple(tuple(FeatureCell((i + j + index) / 200) for j in range(2)) for i in range(120)),
            days[-1],
        )
        labels = tuple(index % 2 if code in heads else None for code in range(1, 13))
        rows.append(TrainingExample(sample, window, labels, (True,) * 12, {}))
    closure = digest(canonical({"config": CONFIG, "fixture_version": 1}))
    identity = ModelIdentity(
        "L",
        REQUEST_DIGEST,
        TARGET_DIGEST,
        closure,
        closure,
        head_set_digest(heads),
        closure,
        closure,
        closure,
        code_digest(),
        closure,
        closure,
    )
    output = {}
    baseline_receipts = {}
    for kind in ("L", "G"):
        model = BaselineAdapter(replace(identity, kind=kind), heads, cfg)
        model.fit(rows, partition="train")
        predicted = model.predict(rows[0].sample, rows[0].window)
        output[kind] = predicted.raw_scores
        prediction_receipt = asdict(predicted)
        prediction_receipt["sample"]["anchor_date"] = predicted.sample.anchor_date.isoformat()
        baseline_receipts[kind] = {
            "model_identity": asdict(model.identity),
            "prediction": prediction_receipt,
        }
    seed_predictions = {}
    for seed in (17, 29, 43):
        torch.manual_seed(seed)
        model = build_t0_model(cfg)
        seed_identity = replace(
            identity,
            kind="T0",
            checkpoint_digest=temporal_state_digest(model),
        )
        seed_predictions[seed] = temporal_predict(
            model,
            sample=rows[0].sample,
            window=rows[0].window,
            identity=seed_identity,
            heads=heads,
            config=cfg,
            seed=seed,
        )
    aggregate = aggregation_receipt(seed_predictions, trainable_heads=heads)
    output["T0"] = aggregate["raw_scores"]
    t0_identity = replace(
        identity,
        kind="T0",
        checkpoint_digest=digest(
            canonical({seed: value.checkpoint_digest for seed, value in seed_predictions.items()})
        ),
    )
    projected = project_output(
        sample=rows[0].sample,
        historical_stage_code=2,
        proposition_id=PROPOSITIONS[1],
        identity=t0_identity,
        trainable_heads=heads,
        applicability=True,
        raw_score=output["T0"][1],
        calibrated_probability=None,
    )
    return {
        "schema_version": "quanthus.p5-synthetic-result.v1",
        "synthetic_only": True,
        "market_rows_read": 0,
        "formal_seed_budget_consumed": 0,
        "formal_training_authorized": False,
        "gate_acceptance": False,
        "raw_scores": output,
        "baseline_receipts": baseline_receipts,
        "projected_output": projected,
        "base_rate": base_rate_receipt(rows=rows, heads=heads, train_manifest_digest=closure),
        "T1": t1_admission_status(),
        "T0_weights": "synthetic_initialization_only",
        "T0_aggregation": aggregate,
    }


def verify(artifact: Path, revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise P5AssessmentError("exact Git revision required")
    if artifact.is_symlink() or {p.name for p in artifact.iterdir()} != {
        "manifest.json",
        "READY",
        "summary.json",
    }:
        raise P5AssessmentError("synthetic artifact layout differs")
    if any(p.is_symlink() or not p.is_file() for p in artifact.iterdir()):
        raise P5AssessmentError("synthetic artifact must contain regular files")
    raw = (artifact / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    expected = {
        "schema_version": "quanthus.p5-synthetic-manifest.v1",
        "code_revision": revision,
        "code_digest": code_digest(),
        "config_digest": digest(canonical(CONFIG)),
        "summary_digest": digest((artifact / "summary.json").read_bytes()),
    }
    if manifest != expected or (artifact / "READY").read_text() != digest(raw) + "\n":
        raise P5AssessmentError("synthetic artifact identity/checksum differs")
    summary = json.loads((artifact / "summary.json").read_bytes())
    if (
        summary.get("synthetic_only") is not True
        or summary.get("market_rows_read") != 0
        or summary.get("gate_acceptance") is not False
    ):
        raise P5AssessmentError("synthetic claim ceiling differs")
    return {
        "verified": True,
        "run_id": artifact.name,
        "manifest_sha256": digest(raw),
        "file_count": 3,
    }


def run(config: Path, output_root: Path, revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or json.loads(config.read_bytes()) != CONFIG:
        raise P5AssessmentError("synthetic code/config identity differs")
    artifact = output_root / "p5-synthetic-v1"
    artifact.mkdir(parents=True, exist_ok=False)
    summary = canonical(synthetic_summary())
    (artifact / "summary.json").write_bytes(summary)
    manifest = canonical(
        {
            "schema_version": "quanthus.p5-synthetic-manifest.v1",
            "code_revision": revision,
            "code_digest": code_digest(),
            "config_digest": digest(canonical(CONFIG)),
            "summary_digest": digest(summary),
        }
    )
    (artifact / "manifest.json").write_bytes(manifest)
    (artifact / "READY").write_text(digest(manifest) + "\n")
    return verify(artifact, revision)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--training-root", required=True)  # protocol-only; never inspected
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--code-revision", required=True)
    verifier = sub.add_parser("verify")
    verifier.add_argument("--artifact", type=Path, required=True)
    verifier.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    result = (
        run(args.config, args.output_root, args.code_revision)
        if args.command == "run"
        else verify(args.artifact, args.code_revision)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
