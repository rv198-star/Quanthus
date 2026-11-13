"""R9 reference QA and the fixed, bounded single-head learning chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quanthus.r9_reference import (
    READERS,
    reference_protocol,
    reference_rubric,
    strict_json,
    validate_opinion,
)
from quanthus.structure_v2_inputs import canonical


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def support(role: str, resolved: list[dict[str, Any]]) -> dict[str, Any]:
    positive = sum(r["label"] == "TRUE" for r in resolved)
    negative = sum(r["label"] == "FALSE" for r in resolved)
    blocks = len({r["anchor"][:7] for r in resolved if r["label"] in {"TRUE", "FALSE"}})
    passes = {
        "guard": min(positive, negative) >= 2,
        "evaluation": positive + negative >= 12 and min(positive, negative) >= 5 and blocks >= 2,
        "train": positive + negative >= 12 and min(positive, negative) >= 4 and blocks >= 2,
        "update": positive + negative >= 1,
    }[role]
    return {
        "role": role,
        "subjects": len(resolved),
        "positive": positive,
        "negative": negative,
        "unknown": len(resolved) - positive - negative,
        "blocks": blocks,
        "support_pass": passes,
    }


def review_role(
    qualified: Path, raw_reference: Path, review: Path, request: Path, output: Path, *, role: str
) -> dict[str, Any]:
    """QA can only remove an opinion from consensus; it cannot supply a new label."""
    if output.exists():
        raise ValueError("R9_QA_FRESH_OUTPUT")
    contract = json.loads(request.read_bytes())
    protocol = reference_protocol(contract, file_digest(request))
    if file_digest(qualified) != contract["qualified_input"]["qualified_cases_sha256"]:
        raise ValueError("R9_QA_INPUT_BINDING")
    qa = strict_json(review.read_text())
    required = {"role", "raw_reference_sha256", "reviewer", "invalid_opinions"}
    if not isinstance(qa, dict) or not required <= set(qa) or not set(qa) <= required | {"summary"}:
        raise ValueError("R9_QA_SCHEMA")
    if qa["role"] != role or qa["raw_reference_sha256"] != file_digest(raw_reference):
        raise ValueError("R9_QA_REFERENCE_BINDING")
    if not isinstance(qa["reviewer"], str) or not qa["reviewer"]:
        raise ValueError("R9_QA_REVIEWER")
    if not isinstance(qa["invalid_opinions"], list):
        raise ValueError("R9_QA_INVALID_LIST")
    rows = [r for r in json.loads(qualified.read_bytes())["rows"] if r["identity"]["role"] == role]
    expected = {"guard": 8, "evaluation": 24, "train": 24, "update": 8}[role]
    if (
        len(rows) != expected
        or len({r["identity"]["company_id"] for r in rows}) != expected
        or len({r["identity"]["sample_id"] for r in rows}) != expected
    ):
        raise ValueError("R9_QA_ROLE_DENOMINATOR")
    envelope = strict_json(raw_reference.read_text())
    if protocol and envelope.get("protocol") != protocol:
        raise ValueError("R9_QA_PROTOCOL_BINDING")
    opinions = envelope["opinions"]
    pairs = [(o["case_id"], o["reader"]) for o in opinions]
    wanted = {(r["identity"]["sample_id"], reader) for r in rows for reader in READERS}
    if len(pairs) != expected * 2 or len(wanted) != expected * 2 or set(pairs) != wanted:
        raise ValueError("R9_QA_OPINION_DENOMINATOR")
    invalid = set()
    for item in qa["invalid_opinions"]:
        if not isinstance(item, dict) or set(item) != {"case_id", "reader", "reason"}:
            raise ValueError("R9_QA_INVALID_SCHEMA")
        if not all(isinstance(item[k], str) and bool(item[k]) for k in item):
            raise ValueError("R9_QA_INVALID_TYPES")
        pair = (item["case_id"], item["reader"])
        if (
            pair not in wanted
            or pair in invalid
            or not isinstance(item["reason"], str)
            or not item["reason"]
        ):
            raise ValueError("R9_QA_INVALID_OPINION")
        invalid.add(pair)
    resolved = []
    for row in rows:
        identity = row["identity"]
        case_id = identity["sample_id"]
        both = [o for o in opinions if o["case_id"] == case_id]
        labels = []
        for opinion in both:
            if protocol:
                expected_prompt = reference_rubric(contract).format(
                    reader=opinion["reader"],
                    packet=json.dumps(row["packet"], separators=(",", ":")),
                )
                started = opinion.get("call_started") is True
                needs_prompt = started or opinion.get("status") == "VALID"
                if (
                    opinion.get("protocol") != protocol
                    or (opinion.get("status") == "VALID" and not started)
                    or (
                        needs_prompt
                        and opinion.get("prompt_sha256")
                        != hashlib.sha256(expected_prompt.encode()).hexdigest()
                    )
                ):
                    raise ValueError("R9_QA_OPINION_PROTOCOL_BINDING")
            if (
                opinion["status"] == "VALID"
                and row["available"]
                and (case_id, opinion["reader"]) not in invalid
            ):
                validate_opinion(opinion["opinion"], row["packet"], opinion["reader"])
                labels.append(opinion["opinion"]["label"])
        label = labels[0] if len(labels) == 2 and labels[0] == labels[1] else "UNKNOWN"
        resolved.append(
            {
                "case_id": case_id,
                "company_id": identity["company_id"],
                "anchor": identity["anchor"],
                "available": row["available"],
                "label": label,
            }
        )
    summary = support(role, resolved)
    result = {
        "raw_reference_sha256": file_digest(raw_reference),
        "qa_sha256": file_digest(review),
        "resolved": resolved,
        "support": summary,
        "invalidated_opinions": len(invalid),
    }
    if protocol:
        result["protocol"] = protocol
    payload = canonical(result) + b"\n"
    output.write_bytes(payload)
    summary = {
        **summary,
        "invalidated_opinions": len(invalid),
        "qa_sealed_sha256": hashlib.sha256(payload).hexdigest(),
        "fit_runs": 0,
        "update_runs": 0,
    }
    if protocol:
        summary["protocol"] = protocol
    output.with_name("support.qa.safe.json").write_bytes(canonical(summary) + b"\n")
    return summary
