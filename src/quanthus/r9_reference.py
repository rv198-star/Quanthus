"""Bounded, candidate-blind operational AI references, with full failed-call receipts."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.r9_qualification import check_roles
from quanthus.structure_v2_inputs import canonical

ABSOLUTE_STOP = datetime(2026, 11, 13, 15, 10, 14, tzinfo=timezone.utc)
READERS = ("short_term", "fund")
RUBRIC = """You are a candidate-blind research reader, role {reader}.
Use only the supplied normalized HLC40. Do not use tools, files, retrieval, or outside knowledge.
Rows 1..20 are the preceding window; rows 21..40 are the CURRENT 20-session proposition.
Judge current orderly consolidation: positive evidence of low directional progress, bounded
range, contraction relative to the preceding window, and stable center. Assess contrary
evidence such as coherent directional relocation or instability. Stable without contraction
is not sufficient. Do not infer future breakout, investment merit, or any other market outcome.
Use qualitative reasoning; no invented numerical thresholds, classifiers or feature formulas.
Return UNKNOWN if evidence is unclear. Never force class balance.
Return exactly one JSON object with keys case_id,reviewer,label,support,counterevidence,rationale.
case_id must equal supplied case_id; reviewer must be {reader}; label is TRUE,FALSE,UNKNOWN.
support and counterevidence are lists of exact price citations, each with exactly
offset (integer1..40), field (H,L,C), value (exact supplied string), reason (nonempty).
TRUE requires support; FALSE requires counterevidence. Cite both windows when asserting
contraction or its absence. Include reasons and uncertainty in rationale; do not use Markdown.
Packet: {packet}
"""


def strict_json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("R9_REFERENCE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def validate_opinion(value: Any, packet: dict[str, Any], reader: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "reviewer",
        "label",
        "support",
        "counterevidence",
        "rationale",
    }:
        raise ValueError("R9_REFERENCE_SCHEMA")
    if value["case_id"] != packet["case_id"] or value["reviewer"] != reader:
        raise ValueError("R9_REFERENCE_IDENTITY")
    if (
        not isinstance(value["label"], str)
        or value["label"] not in {"TRUE", "FALSE", "UNKNOWN"}
        or not isinstance(value["rationale"], str)
        or not value["rationale"]
    ):
        raise ValueError("R9_REFERENCE_LABEL")
    for name in ("support", "counterevidence"):
        if not isinstance(value[name], list):
            raise ValueError("R9_REFERENCE_CITATIONS")
        for item in value[name]:
            if (
                not isinstance(item, dict)
                or set(item) != {"offset", "field", "value", "reason"}
                or type(item["offset"]) is not int
                or not 1 <= item["offset"] <= 40
                or not isinstance(item["field"], str)
                or item["field"] not in {"H", "L", "C"}
                or not isinstance(item["reason"], str)
                or not item["reason"]
            ):
                raise ValueError("R9_REFERENCE_CITATION_SCHEMA")
            if item["value"] != packet["HLC40"][item["offset"] - 1]["HLC".index(item["field"])]:
                raise ValueError("R9_REFERENCE_CITATION_VALUE")
    if (value["label"] == "TRUE" and not value["support"]) or (
        value["label"] == "FALSE" and not value["counterevidence"]
    ):
        raise ValueError("R9_REFERENCE_MISSING_EVIDENCE")
    if value["label"] == "TRUE" and {item["offset"] <= 20 for item in value["support"]} != {
        False,
        True,
    }:
        raise ValueError("R9_REFERENCE_TWO_WINDOW_SUPPORT")
    return value


def validate_trace(text: str) -> None:
    events = [strict_json(line) for line in text.splitlines() if line.strip()]
    if not events or not all(isinstance(event, dict) for event in events):
        raise ValueError("R9_REFERENCE_TRACE_SHAPE")
    if events[-1].get("type") != "turn.completed":
        raise ValueError("R9_REFERENCE_TRACE_INCOMPLETE")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("R9_REFERENCE_TRACE_SHAPE")
        if not isinstance(event.get("type"), str) or event.get("type") not in {
            "thread.started",
            "turn.started",
            "item.started",
            "item.updated",
            "item.completed",
            "turn.completed",
        }:
            raise ValueError("R9_REFERENCE_TRACE_EVENT")
        if "item" in event:
            if not isinstance(event["item"], dict):
                raise ValueError("R9_REFERENCE_TRACE_ITEM_SHAPE")
            if not isinstance(event["item"].get("type"), str) or event["item"].get("type") not in {
                "agent_message",
                "reasoning",
            }:
                raise ValueError("R9_REFERENCE_TOOL_OR_CONTEXT_TRACE")


def collect_role(
    qualified: Path, role: str, output: Path, sandbox: Path, *, request: Path
) -> dict[str, Any]:
    if output.exists() or role not in {"guard", "evaluation", "train", "update"}:
        raise ValueError("R9_REFERENCE_FRESH_ROLE_OUTPUT")
    contract = json.loads(request.read_bytes())
    raw = qualified.read_bytes()
    if hashlib.sha256(raw).hexdigest() != contract["qualified_input"]["qualified_cases_sha256"]:
        raise ValueError("R9_REFERENCE_QUALIFIED_HASH")
    all_rows = json.loads(raw)["rows"]
    identities = {
        "schema": "quanthus.r9-role-identities.v1",
        "rows": [r["identity"] for r in all_rows],
    }
    check_roles(identities)
    if (
        hashlib.sha256(canonical(identities) + b"\n").hexdigest()
        != contract["qualified_input"]["identity_sha256"]
    ):
        raise ValueError("R9_REFERENCE_ROLE_IDENTITY_HASH")
    rows = [r for r in all_rows if r["identity"]["role"] == role]
    if len(rows) != {"guard": 8, "evaluation": 24, "train": 24, "update": 8}[role]:
        raise ValueError("R9_REFERENCE_ROLE_DENOMINATOR")
    output.mkdir(parents=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    if any(sandbox.iterdir()):
        raise ValueError("R9_REFERENCE_EMPTY_SANDBOX")
    started = time.monotonic()

    def read_one(args: tuple[dict[str, Any], str]) -> dict[str, Any]:
        row, reader = args
        packet = row["packet"]
        stem = output / (packet["case_id"] + "-" + reader)
        receipt: dict[str, Any] = {
            "case_id": packet["case_id"],
            "reader": reader,
            "packet_sha256": hashlib.sha256(canonical(packet)).hexdigest(),
            "status": "UNAVAILABLE",
            "call_started": False,
        }
        if not row["available"]:
            return receipt
        remaining = (ABSOLUTE_STOP - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 3:
            receipt["status"] = "DEADLINE_NOT_RUN"
            return receipt
        prompt = RUBRIC.format(reader=reader, packet=json.dumps(packet, separators=(",", ":")))
        (stem.with_suffix(".prompt.txt")).write_text(prompt)
        then = time.monotonic()
        receipt["call_started"] = True
        receipt["started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with (
                stem.with_suffix(".events.jsonl").open("w") as events_file,
                stem.with_suffix(".stderr.txt").open("w") as stderr_file,
            ):
                process = subprocess.Popen(
                    [
                        "codex",
                        "exec",
                        "--ephemeral",
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "-C",
                        str(sandbox),
                        "--json",
                        "-o",
                        str(stem.with_suffix(".opinion.json")),
                        prompt,
                    ],
                    stdout=events_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=min(87, remaining - 3))
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                    raise ValueError("R9_REFERENCE_PROCESS_TIMEOUT") from None
            if any(
                stem.with_suffix(s).stat().st_size > 4194304
                for s in (".events.jsonl", ".stderr.txt", ".opinion.json")
            ):
                raise ValueError("R9_REFERENCE_OUTPUT_BYTES")
            if process.returncode != 0:
                raise ValueError("R9_REFERENCE_PROCESS_EXIT:" + str(process.returncode))
            validate_trace(stem.with_suffix(".events.jsonl").read_text())
            opinion = validate_opinion(
                strict_json(stem.with_suffix(".opinion.json").read_text()), packet, reader
            )
            receipt.update(status="VALID", opinion=opinion)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            receipt.update(status="INVALID_OR_FAILED", error=str(exc)[:1000])
        receipt["seconds"] = round(time.monotonic() - then, 3)
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        stem.with_suffix(".receipt.json").write_bytes(canonical(receipt) + b"\n")
        return receipt

    with ThreadPoolExecutor(max_workers=2) as pool:
        opinions = list(pool.map(read_one, [(row, reader) for row in rows for reader in READERS]))
    resolved = []
    for row in rows:
        identity = row["identity"]
        both = [r for r in opinions if r["case_id"] == identity["sample_id"]]
        labels = [r["opinion"]["label"] for r in both if r["status"] == "VALID"]
        label = labels[0] if len(labels) == 2 and labels[0] == labels[1] else "UNKNOWN"
        resolved.append(
            {
                "case_id": identity["sample_id"],
                "label": label,
                "available": row["available"],
                "company_id": identity["company_id"],
                "anchor": identity["anchor"],
            }
        )
    positive = sum(r["label"] == "TRUE" for r in resolved)
    negative = sum(r["label"] == "FALSE" for r in resolved)
    blocks = len({r["anchor"][:7] for r in resolved if r["label"] in {"TRUE", "FALSE"}})
    passes = {
        "guard": min(positive, negative) >= 2,
        "evaluation": positive + negative >= 12 and min(positive, negative) >= 5 and blocks >= 2,
        "train": positive + negative >= 12 and min(positive, negative) >= 4 and blocks >= 2,
        "update": positive + negative >= 1,
    }[role]
    raw = canonical({"opinions": opinions, "resolved": resolved}) + b"\n"
    (output / "references.sealed.json").write_bytes(raw)
    summary = {
        "role": role,
        "subjects": len(rows),
        "positive": positive,
        "negative": negative,
        "unknown": len(rows) - positive - negative,
        "blocks": blocks,
        "support_pass": passes,
        "calls_started": sum(r["call_started"] for r in opinions),
        "invalid_or_failed": sum(r["status"] == "INVALID_OR_FAILED" for r in opinions),
        "seconds": round(time.monotonic() - started, 3),
        "sealed_sha256": hashlib.sha256(raw).hexdigest(),
        "fit_runs": 0,
        "update_runs": 0,
    }
    (output / "support.safe.json").write_bytes(canonical(summary) + b"\n")
    return summary
