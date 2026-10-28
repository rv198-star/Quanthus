#!/usr/bin/env python3
"""Blind support receipt, then one explicitly gated fit/prediction commitment.

Orchestration only: production qualification, seven-feature fit and existing R0
remain in quanthus. Evaluation opinions are not opened by the fit command.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import socket
import zlib
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from entry_march_confirmation import CONTRACT, ROOT, guard, read, verify, write

from quanthus.expert_strategy.held_structure_integrity import file_sha
from quanthus.stage_learning.entry_confirmation import (
    reference_support,
    require_reference_admission,
    training_support,
)
from quanthus.stage_learning.entry_consolidation import EntryDiagnostic, EntryOpportunity, summarize
from quanthus.stage_learning.head2_revision import FEATURE_NAMES
from quanthus.stage_learning.head_comparison import stage2_predicate
from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadPolicy,
    HeadRow,
    dump_checkpoint,
    fit_head,
    predict_head,
)
from quanthus.stage_learning.order_rule_screen import load_rule
from quanthus.structure_v2_inputs import canonical, digest


def committed_panel(job: Path, reader: str) -> tuple[list[dict[str, Any]], str]:
    commit = read(job / f"reference/commit-{reader}.json")
    spec = read(job / "inputs.frozen.private.json")["spec"]
    if (
        commit["reader"] != reader
        or commit["codec"] != "zlib+base64"
        or commit["count"] != 87
        or commit["packet_sha256"] != file_sha(job / "reference/packet.json")
        or commit["rubric_sha256"] != file_sha(Path(spec["rubric"]))
    ):
        raise ValueError("ENTRY_REFERENCE_COMMITMENT_BINDING")
    raw = zlib.decompress(base64.b64decode(commit["payload"], validate=True))
    if (
        len(raw) > 2**20
        or hashlib.sha256(raw).hexdigest() != commit["canonical_uncompressed_sha256"]
    ):
        raise ValueError("ENTRY_REFERENCE_COMMITMENT_BYTES")
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError("ENTRY_REFERENCE_COMMITMENT_CANONICAL")
    return value, commit["canonical_uncompressed_sha256"]


def gate(job: Path) -> dict[str, Any]:
    guard(job)
    a, sha_a = committed_panel(job, "A")
    b, sha_b = committed_panel(job, "B")
    contract = read(ROOT / CONTRACT)["reference"]
    receipt, consensus = reference_support(
        read(job / "private/reference-identities.json"),
        a,
        b,
        minimum_definite=contract["minimum_definite"],
        minimum_companies=contract["minimum_distinct_companies_per_class"],
        minimum_blocks=contract["minimum_time_blocks"],
    )
    receipt.update(
        {
            "panel_A_sha256": sha_a,
            "panel_B_sha256": sha_b,
            "packet_sha256": file_sha(job / "reference/packet.json"),
            "new_fits": 0,
            "UTC": datetime.now(timezone.utc).isoformat(),
        }
    )
    write(job / "private/consensus.sealed.json", consensus)
    write(job / "evidence/reference-support.safe.json", receipt)
    return receipt


def fit(job: Path) -> dict[str, Any]:
    guard(job)
    require_reference_admission(read(job / "evidence/reference-support.safe.json"))
    frozen = read(job / "inputs.frozen.private.json")
    verify(frozen["pins"])
    verify(read(job / "private/training-members.frozen.json"))
    contract = read(ROOT / CONTRACT)
    training = read(job / "private/training20.json")["rows"]
    train_receipt = training_support(
        training,
        read(frozen["spec"]["training_identity"])["rows"],
        cutoff=contract["training"]["cutoff"],
    )
    bindings = HeadBindings(
        request=frozen["purpose_digest"],
        source=digest(frozen["source"]["source_pins"]),
        reference=digest(
            {
                "qualified_training_artifact": frozen["pins"][frozen["spec"]["training_facts"]],
                "prefix": read(frozen["spec"]["training_identity"]),
            }
        ),
        eligibility=digest(read(job / "private/reference-identities.json")),
        split=digest(contract["training"]),
        features=digest(FEATURE_NAMES),
        target=digest({"observed_order_rubric": file_sha(Path(frozen["spec"]["rubric"]))}),
        code=digest({"frozen_producer": frozen["code"], "fit_driver": file_sha(Path(__file__))}),
    )
    model = fit_head(
        [
            HeadRow(
                r["sample_id"],
                r["security_id"],
                r["episode_id"],
                r["anchor"],
                tuple(r["features"][:7]),
                r["label"],
            )
            for r in training
        ],
        policy=HeadPolicy(FEATURE_NAMES, 200, 100, 0.05, 0.01, 0.6931471805599453),
        bindings=bindings,
    )
    if max(u.anchor for u in model.uses) > "2021-03-31" or model.generation != 0:
        raise ValueError("ENTRY_EARLY_MODEL_USE_LEDGER")
    # Opinion files are deliberately never opened by this command.
    with (job / "private/checkpoint.json").open("xb") as stream:
        stream.write(dump_checkpoint(model))
    results, predictions = [], []
    rule = load_rule(ROOT)
    for row in read(job / "private/facts87.json")["rows"]:
        features = row["features"]
        score, positive = None, None
        if features is not None:
            if features[6] not in train_receipt["mask_support"]:
                raise ValueError("ENTRY_UNSUPPORTED_MASK")
            score = predict_head(model, features).raw_score
            primary = row["facts"]["primary"]
            values = dict(
                zip(
                    ("directional_efficiency", "range_width", "width_change", "center_shift"),
                    (Decimal(x["value"]) for x in primary),
                    strict=True,
                )
            )
            positive = stage2_predicate(values, rule)
        event = row["event"]
        opportunity = EntryOpportunity(
            **{
                **event,
                "issuer_id": row["proposal"]["company_id"],
                "signal_date": date.fromisoformat(event["signal_date"]),
            }
        )
        result = EntryDiagnostic(
            opportunity=opportunity,
            anchor=date.fromisoformat(row["proposal"]["anchor"]),
            earliest_calendar_execution=date.fromisoformat(row["proposal"]["execution_session"]),
            facts_digest=row["facts_digest"],
            model_digest=model.digest,
            rule_positive=positive,
            raw_score=score,
            c0_allowed=opportunity.baseline_allowed,
            c1_allowed=opportunity.baseline_allowed and positive is True,
            c2_allowed=opportunity.baseline_allowed and score is not None and score >= 0.5,
            reason=row["feature_reason"],
            revision=digest({"blind_id": row["blind_id"], "model": model.digest}),
        )
        results.append(result)
        predictions.append({"blind_id": row["blind_id"], **asdict(result)})
    write(job / "private/predictions87.json", predictions)
    commitment = {
        "checkpoint_sha256": file_sha(job / "private/checkpoint.json"),
        "predictions87_sha256": file_sha(job / "private/predictions87.json"),
        "use_ledger_digest": digest([asdict(u) for u in model.uses]),
        "scaler_digest": digest({"means": model.means, "scales": model.scales}),
        "training_rows": len(training),
        "generation": model.generation,
        "UTC": datetime.now(timezone.utc).isoformat(),
        "evaluation_opinions_opened": False,
    }
    write(job / "evidence/prediction-commitment.safe.json", commitment)
    summary = summarize(results)
    summary.update(commitment)
    write(job / "evidence/fit-diagnostic.safe.json", summary)
    return summary


def quality(job: Path) -> dict[str, Any]:
    guard(job)
    commitment = read(job / "evidence/prediction-commitment.safe.json")
    for field, name in (
        ("checkpoint_sha256", "checkpoint.json"),
        ("predictions87_sha256", "predictions87.json"),
    ):
        if file_sha(job / "private" / name) != commitment[field]:
            raise ValueError("ENTRY_PREDICTION_COMMITMENT_CHANGED")
    # Recompute consensus from the two committed panels, not an editable label file.
    _, consensus = reference_support(
        read(job / "private/reference-identities.json"),
        committed_panel(job, "A")[0],
        committed_panel(job, "B")[0],
    )
    predictions = read(job / "private/predictions87.json")
    counts: dict[str, Counter[str]] = {arm: Counter() for arm in ("R0", "model")}
    for row in predictions:
        label = consensus[row["blind_id"]]
        if label not in {"POSITIVE", "NEGATIVE"} or row["raw_score"] is None:
            continue
        for arm, prediction in (("R0", row["rule_positive"]), ("model", row["raw_score"] >= 0.5)):
            cell = (
                ("TP" if prediction else "FN")
                if label == "POSITIVE"
                else ("FP" if prediction else "TN")
            )
            counts[arm][cell] += 1
    metrics = {}
    for arm, count in counts.items():
        recall = count["TP"] / (count["TP"] + count["FN"])
        specificity = count["TN"] / (count["TN"] + count["FP"])
        metrics[arm] = {
            "confusion": dict(count),
            "positive_recall": recall,
            "negative_specificity": specificity,
            "balanced_accuracy": (recall + specificity) / 2,
        }
    receipt = {
        "opportunities": 87,
        "known_consensus_compared": sum(counts["model"].values()),
        "metrics": metrics,
        "reference_unknown_or_unavailable": 87 - sum(counts["model"].values()),
        "prediction_commitment_precedes_opinion_opening": True,
        "consumer_admission": "NOT_GRANTED",
        "UTC": datetime.now(timezone.utc).isoformat(),
        "recognition_ceiling": "Descriptive fixed-batch comparison; not admission or independent market truth",
    }
    write(job / "evidence/recognition.safe.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("gate", "fit", "quality"))
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()

    def deny(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("ENTRY_NETWORK_NOT_AUTHORIZED")

    socket.create_connection = deny
    socket.socket.connect = deny  # type: ignore[method-assign]
    print(
        json.dumps(
            {"gate": gate, "fit": fit, "quality": quality}[args.command](args.job), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
