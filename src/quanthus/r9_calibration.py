"""Finite, model-diverse, clause-level calibration on an existing qualified pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO

from quanthus.r9_reference import strict_json, validate_trace
from quanthus.structure_v2_inputs import canonical

CLAUSES = (
    "low_directional_efficiency",
    "materially_narrow",
    "material_contraction",
    "no_coherent_relocation",
)
MODELS = ("auto/gpt-6.1-sol", "auto/gpt-6-astra")
RUBRIC = """Judge observed orderly consolidation, using ONLY the supplied normalized HLC40.
No tools, files, retrieval, outside knowledge, future returns or investment judgment.
Each row is [H,L,C]. First close=100. Rows1..20 are prior; rows21..40 are CURRENT.
Judge FOUR necessary conditions separately:
low_directional_efficiency: limited directional displacement relative to cumulative price
travel. Consider the full path; small endpoint change alone is not sufficient evidence.
materially_narrow: positive evidence that the full current price envelope is materially
bounded relative to its price scale. Explain actual occupied amplitude; contraction alone
does not establish narrowness. Do not invent a universal numerical cutoff.
material_contraction: current full-window range materially contracts relative to prior20.
no_coherent_relocation: absence of sustained directional repricing through successive portions
of current20. A single leg, center shift, or repeated travel inside a tight contracting band
does not alone constitute coherent relocation. Explain supporting and contrary path evidence.
Center stability is NOT required. High in-band churn is NOT an independent veto.
Narrow but noncontracting, contracting but still wide, and coherent relocation defeat the
respective necessary condition. Use UNKNOWN when a condition cannot be defensibly resolved.
For each condition return TRUE,FALSE,UNKNOWN, exact HLC citations supporting the condition,
exact counterevidence against it, and rationale. FALSE needs clear counterevidence; TRUE needs
positive support. For contraction cite BOTH windows. Other conditions primarily use current20.
Overall: any FALSE => FALSE; all TRUE => TRUE; otherwise UNKNOWN. Never force class balance.
Return exactly one JSON object: case_id,reader,clauses,label.
clauses is an object keyed by the four names above. Each value has exactly label,support,
counterevidence,rationale. A citation has exactly offset(integer1..40),field(H/L/C),
value(exact supplied string),reason(nonempty). No markdown.
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value) + b"\n")


def conjunction(labels: list[str]) -> str:
    if len(labels) != 4 or any(x not in {"TRUE", "FALSE", "UNKNOWN"} for x in labels):
        raise ValueError("R9_CALIBRATION_CLAUSE_LABELS")
    return "FALSE" if "FALSE" in labels else "TRUE" if set(labels) == {"TRUE"} else "UNKNOWN"


def validate(value: Any, packet: dict[str, Any], reader: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"case_id", "reader", "clauses", "label"}:
        raise ValueError("R9_CALIBRATION_OPINION_SCHEMA")
    if value["case_id"] != packet["case_id"] or value["reader"] != reader:
        raise ValueError("R9_CALIBRATION_OPINION_IDENTITY")
    clauses = value["clauses"]
    if not isinstance(clauses, dict) or set(clauses) != set(CLAUSES):
        raise ValueError("R9_CALIBRATION_CLAUSES")
    for name, item in clauses.items():
        if not isinstance(item, dict) or set(item) != {
            "label",
            "support",
            "counterevidence",
            "rationale",
        }:
            raise ValueError("R9_CALIBRATION_CLAUSE_SCHEMA")
        if not isinstance(item["label"], str) or item["label"] not in {"TRUE", "FALSE", "UNKNOWN"}:
            raise ValueError("R9_CALIBRATION_CLAUSE_LABELS")
        if not isinstance(item["rationale"], str) or not item["rationale"].strip():
            raise ValueError("R9_CALIBRATION_RATIONALE")
        for kind in ("support", "counterevidence"):
            if not isinstance(item[kind], list):
                raise ValueError("R9_CALIBRATION_CITATIONS")
            for citation in item[kind]:
                if not isinstance(citation, dict) or set(citation) != {
                    "offset",
                    "field",
                    "value",
                    "reason",
                }:
                    raise ValueError("R9_CALIBRATION_CITATION_SCHEMA")
                offset, field = citation["offset"], citation["field"]
                if (
                    type(offset) is not int
                    or not 1 <= offset <= 40
                    or not isinstance(field, str)
                    or field not in {"H", "L", "C"}
                    or not isinstance(citation["reason"], str)
                    or not citation["reason"].strip()
                    or citation["value"] != packet["HLC40"][offset - 1]["HLC".index(field)]
                ):
                    raise ValueError("R9_CALIBRATION_CITATION_VALUE")
        required = "support" if item["label"] == "TRUE" else "counterevidence"
        if item["label"] in {"TRUE", "FALSE"} and not item[required]:
            raise ValueError("R9_CALIBRATION_EVIDENCE_MISSING")
        if name == "material_contraction" and item["label"] in {"TRUE", "FALSE"}:
            if {c["offset"] <= 20 for c in item[required]} != {True, False}:
                raise ValueError("R9_CALIBRATION_CONTRACTION_WINDOWS")
        elif item["label"] in {"TRUE", "FALSE"} and not any(
            c["offset"] > 20 for c in item[required]
        ):
            raise ValueError("R9_CALIBRATION_CURRENT_WINDOW")
    if value["label"] != conjunction([clauses[k]["label"] for k in CLAUSES]):
        raise ValueError("R9_CALIBRATION_CONJUNCTION")
    return value


def select(
    pool: list[dict[str, Any]], excluded: set[str], anchors: list[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in anchors:
        rows = [
            r
            for r in pool
            if r["available"]
            and r["identity"]["company_id"] not in excluded
            and r["identity"]["anchor"] == anchor
        ]

        def number(row: dict[str, Any], key: str) -> Decimal:
            number = Decimal(row["primary_facts"][key]["value"])
            if not number.is_finite():
                raise ValueError("R9_CALIBRATION_NONFINITE")
            return number

        contracting = sorted(
            [
                r
                for r in rows
                if abs(number(r, "directional_efficiency")) <= Decimal("0.33")
                and number(r, "width_change") <= Decimal("-0.20")
            ],
            key=lambda r: (number(r, "range_width"), r["identity"]["sample_id"]),
        )
        expanding = sorted(
            [r for r in rows if number(r, "width_change") > 0],
            key=lambda r: (-number(r, "width_change"), r["identity"]["sample_id"]),
        )
        if len(contracting) < 2 or not expanding:
            raise ValueError("R9_CALIBRATION_QUOTA_SHORTAGE")
        choices = (contracting[0], contracting[1:][len(contracting[1:]) // 2], expanding[0])
        for intention, row in zip(
            ("positive_intent", "boundary_intent", "negative_intent"), choices, strict=True
        ):
            result.append({"sampling_intent": intention, "source_row": row})
    ids = [r["source_row"]["identity"]["company_id"] for r in result]
    if len(result) != 12 or len(set(ids)) != 12:
        raise ValueError("R9_CALIBRATION_IDENTITY_CAPACITY")
    return result


def selection_payload(request: Path) -> dict[str, Any]:
    c = strict_json(request.read_text())
    pool, exclude = (Path(c["source"][k]) for k in ("pool_path", "exclude_path"))
    if sha(pool) != c["source"]["pool_sha256"] or sha(exclude) != c["source"]["exclude_sha256"]:
        raise ValueError("R9_CALIBRATION_SOURCE_BINDING")
    rows = select(
        strict_json(pool.read_text())["rows"],
        {r["identity"]["company_id"] for r in strict_json(exclude.read_text())["rows"]},
        c["selection"]["anchors"],
    )
    for row in rows:
        packet = row["source_row"]["packet"]
        if set(packet) != {"case_id", "available", "HLC40"} or packet["available"] is not True:
            raise ValueError("R9_CALIBRATION_BLIND_PACKET")
        if not isinstance(packet["case_id"], str) or len(packet["HLC40"]) != 40:
            raise ValueError("R9_CALIBRATION_PACKET_SHAPE")
        for hlc in packet["HLC40"]:
            if len(hlc) != 3 or any(
                not isinstance(x, str) or not Decimal(x).is_finite() for x in hlc
            ):
                raise ValueError("R9_CALIBRATION_PACKET_SHAPE")
    return {
        "request_sha256": sha(request),
        "rubric_sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
        "rows": rows,
    }


def prepare(request: Path, output: Path) -> dict[str, Any]:
    payload = selection_payload(request)
    if output.exists():
        raise ValueError("R9_CALIBRATION_FRESH_OUTPUT")
    output.mkdir(parents=True)
    write(output / "selected.private.json", payload)
    summary = {
        "cases": len(payload["rows"]),
        "selected_sha256": sha(output / "selected.private.json"),
        "request_sha256": sha(request),
        "rubric_sha256": payload["rubric_sha256"],
        "fit_runs": 0,
        "update_runs": 0,
    }
    write(output / "selection.safe.json", summary)
    return summary


def process_tree_rss(pid: int) -> int:
    """Linux RSS of collector and descendants, including worker-thread children."""
    pending, seen, pages = [pid], set(), 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        proc = Path(f"/proc/{current}")
        try:
            pages += int((proc / "statm").read_text().split()[1])
            for task in (proc / "task").iterdir():
                with suppress(FileNotFoundError, ProcessLookupError):
                    pending.extend(int(x) for x in (task / "children").read_text().split())
        except (FileNotFoundError, ProcessLookupError):
            continue
    return pages * os.sysconf("SC_PAGE_SIZE")


def directory_bytes(path: Path) -> int:
    return sum(p.lstat().st_size for p in path.rglob("*") if not p.is_dir())


class ResourceGuard:
    """Bound the whole Linux collector; stop process groups on the first breach."""

    def __init__(self, runtime: dict[str, Any], output: Path, sandbox: Path):
        if sys.platform != "linux" or not Path("/proc/self/statm").exists():
            raise ValueError("R9_CALIBRATION_LINUX_LIMITS_REQUIRED")
        self.runtime, self.output, self.sandbox = runtime, output, sandbox
        self.lock, self.done, self.stopped = threading.Lock(), threading.Event(), threading.Event()
        self.processes: list[subprocess.Popen[Any]] = []
        self.reason = ""
        self.peak_rss = 0
        self.affinity = sorted(os.sched_getaffinity(0))[: runtime["cpu_cores"]]
        if len(self.affinity) != runtime["cpu_cores"]:
            raise ValueError("R9_CALIBRATION_CPU_CAPACITY")
        os.sched_setaffinity(0, self.affinity)
        self.thread = threading.Thread(target=self.monitor, daemon=True)

    def stop(self, reason: str) -> None:
        with self.lock:
            if not self.reason:
                self.reason = reason
            self.stopped.set()
            for proc in self.processes:
                if proc.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)

    def monitor(self) -> None:
        while not self.done.wait(0.1):
            try:
                rss = process_tree_rss(os.getpid())
                self.peak_rss = max(self.peak_rss, rss)
                if rss > self.runtime["rss_bytes"]:
                    self.stop("R9_CALIBRATION_RSS_LIMIT")
                if directory_bytes(self.output) > self.runtime["output_bytes"]:
                    self.stop("R9_CALIBRATION_OUTPUT_LIMIT")
                if any(p.stat().st_size > 1048576 for p in self.output.glob("*.opinion.json")):
                    self.stop("R9_CALIBRATION_OPINION_LIMIT")
                if directory_bytes(self.sandbox) > self.runtime["tmp_bytes"]:
                    self.stop("R9_CALIBRATION_TMP_LIMIT")
            except (OSError, ValueError) as exc:
                self.stop(f"R9_CALIBRATION_LIMIT_MONITOR:{type(exc).__name__}")


def capture_stream(stream: BinaryIO, sink: BinaryIO, guard: ResourceGuard) -> None:
    """Bound only job output streams; do not limit the client's shared SQLite files."""
    total = 0
    try:
        while block := stream.read(16384):
            remaining = 262144 - total
            sink.write(block[:remaining])
            total += min(len(block), remaining)
            if len(block) > remaining:
                guard.stop("R9_CALIBRATION_STREAM_LIMIT")
                return
    except (OSError, ValueError):
        guard.stop("R9_CALIBRATION_STREAM_FAILURE")
    finally:
        stream.close()


def filtered_trace(trace: str, model: str) -> str:
    warning = (
        f"Model metadata for `{model}` not found. Defaulting to fallback metadata; "
        "this can degrade performance and cause issues."
    )
    filtered, began, warnings = [], False, 0
    for line in trace.splitlines():
        event = strict_json(line)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError("R9_CALIBRATION_TRACE_SCHEMA")
        if event["type"] == "turn.started":
            began = True
        item = event.get("item", {})
        if not isinstance(item, dict):
            raise ValueError("R9_CALIBRATION_TRACE_ITEM")
        if (
            not began
            and event["type"] == "item.completed"
            and item.get("type") == "error"
            and item.get("message") == warning
        ):
            warnings += 1
            if warnings > 1:
                raise ValueError("R9_CALIBRATION_REPEATED_WARNING")
            continue
        filtered.append(line)
    result = "\n".join(filtered)
    validate_trace(result)
    return result


def collect(request: Path, selected: Path, output: Path, sandbox: Path) -> dict[str, Any]:
    c, frozen = strict_json(request.read_text()), strict_json(selected.read_text())
    if canonical(frozen) != canonical(selection_payload(request)):
        raise ValueError("R9_CALIBRATION_PROTOCOL_BINDING")
    if (
        c["reference"]["models"] != list(MODELS)
        or len(frozen["rows"]) != 12
        or c["reference"]["maximum_calls"] != 24
        or c["reference"]["retries"] != 0
        or c["reference"]["concurrency"] != 2
    ):
        raise ValueError("R9_CALIBRATION_CALL_CAP")
    if output.exists() or (sandbox.exists() and any(sandbox.iterdir())):
        raise ValueError("R9_CALIBRATION_FRESH_RUN")
    deadline = datetime.fromisoformat(c["runtime"]["calibration_deadline"].replace("Z", "+00:00"))
    if (deadline - datetime.now(timezone.utc)).total_seconds() < 5:
        raise ValueError("R9_CALIBRATION_EXPIRED")
    guard = ResourceGuard(c["runtime"], output, sandbox)
    marker = Path(c["runtime"]["attempt_marker"])
    with marker.open("x") as stream:
        json.dump(
            {
                "request_sha256": sha(request),
                "selected_sha256": sha(selected),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "maximum_calls": 24,
                "retries": 0,
            },
            stream,
        )
    output.mkdir(parents=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    guard.thread.start()

    def read_one(args: tuple[dict[str, Any], int]) -> dict[str, Any]:
        row, index = args
        source_packet = row["source_row"]["packet"]
        packet = {k: source_packet[k] for k in ("case_id", "HLC40")}
        reader, model = f"reader_{'ab'[index]}", MODELS[index]
        prompt = (
            RUBRIC
            + f"\ncase_id={packet['case_id']}; reader={reader}\nPacket: "
            + json.dumps(packet)
        )
        receipt: dict[str, Any] = {
            "case_id": packet["case_id"],
            "reader": reader,
            "model_id": model,
            "call_started": False,
            "status": "NOT_RUN",
            "request_sha256": sha(request),
            "selected_sha256": sha(selected),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "rubric_sha256": frozen["rubric_sha256"],
        }
        stem = output / f"{packet['case_id']}-{reader}"
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining < 5 or guard.stopped.is_set():
            receipt["error"] = guard.reason or "R9_CALIBRATION_DEADLINE"
            write(stem.with_suffix(".receipt.json"), receipt)
            return receipt
        stem.with_suffix(".prompt.txt").write_text(prompt)
        started = time.monotonic()
        receipt.update(attempted=True, started_at=datetime.now(timezone.utc).isoformat())
        write(stem.with_suffix(".receipt.json"), receipt)
        process = None
        try:
            with (
                stem.with_suffix(".events.jsonl").open("wb") as events,
                stem.with_suffix(".stderr.txt").open("wb") as errors,
            ):
                command = [
                    "codex",
                    "exec",
                    "--model",
                    model,
                    "-c",
                    'model_reasoning_effort="medium"',
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
                ]
                with guard.lock:
                    if guard.stopped.is_set():
                        raise ValueError(guard.reason)
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    receipt["call_started"] = True
                    guard.processes.append(process)
                if process.stdout is None or process.stderr is None:
                    raise ValueError("R9_CALIBRATION_PIPE_MISSING")
                readers = [
                    threading.Thread(target=capture_stream, args=(stream, sink, guard), daemon=True)
                    for stream, sink in ((process.stdout, events), (process.stderr, errors))
                ]
                for thread in readers:
                    thread.start()
                try:
                    process.wait(timeout=min(117, remaining - 3))
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                    raise ValueError("R9_CALIBRATION_TIMEOUT") from None
                finally:
                    for thread in readers:
                        thread.join(timeout=2)
                if any(thread.is_alive() for thread in readers):
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    raise ValueError("R9_CALIBRATION_PIPE_DRAIN_TIMEOUT")
                receipt["process_exit_code"] = process.returncode
                if guard.stopped.is_set():
                    raise ValueError(guard.reason)
            if process.returncode != 0:
                raise ValueError(f"R9_CALIBRATION_PROCESS_EXIT:{process.returncode}")
            trace = stem.with_suffix(".events.jsonl").read_text()
            filtered_trace(trace, model)
            opinion_path = stem.with_suffix(".opinion.json")
            if opinion_path.stat().st_size > 1048576:
                raise ValueError("R9_CALIBRATION_OUTPUT_SIZE")
            receipt.update(
                status="VALID",
                opinion=validate(strict_json(opinion_path.read_text()), packet, reader),
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            subprocess.TimeoutExpired,
        ) as exc:
            receipt.update(status="INVALID_OR_FAILED", error=str(exc)[:1000])
            if str(exc).startswith(("R9_REFERENCE_TRACE_", "R9_CALIBRATION_TRACE_")):
                guard.stop("R9_CALIBRATION_TRACE_FAILURE")
            if process is not None and process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            if process is not None and process.poll() is not None and process.returncode != 0:
                guard.stop("R9_CALIBRATION_PROCESS_FAILURE")
            if process is not None:
                receipt["process_exit_code"] = process.returncode
                receipt["termination_signal"] = (
                    -process.returncode
                    if process.returncode is not None and process.returncode < 0
                    else None
                )
            receipt["resource_stop_reason"] = guard.reason
        receipt.update(
            seconds=round(time.monotonic() - started, 3),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        write(stem.with_suffix(".receipt.json"), receipt)
        return receipt

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(
                executor.map(read_one, [(row, i) for row in frozen["rows"] for i in range(2)])
            )
    finally:
        guard.done.set()
        guard.thread.join(timeout=2)
    write(
        output / "references.sealed.json",
        {
            "request_sha256": sha(request),
            "selected_sha256": sha(selected),
            "rubric_sha256": frozen["rubric_sha256"],
            "opinions": receipts,
        },
    )
    result = {
        "calls": sum(r["call_started"] for r in receipts),
        "valid": sum(r["status"] == "VALID" for r in receipts),
        "opinions_sha256": sha(output / "references.sealed.json"),
        "fit_runs": 0,
        "update_runs": 0,
        "resource_monitor": {
            "interval_seconds": 0.1,
            "peak_rss_bytes": guard.peak_rss,
            "cpu_affinity": guard.affinity,
            "stop_reason": guard.reason,
            "stream_bytes_cap": 262144,
            "opinion_bytes_monitor_cap": 1048576,
        },
    }
    write(output / "receipt.safe.json", result)
    return result


def summarize(
    request: Path, selected: Path, references: Path, qa: Path, output: Path
) -> dict[str, Any]:
    """Apply independent exclusion-only QA; never create or repair a reference label."""
    frozen, sealed, review = (strict_json(p.read_text()) for p in (selected, references, qa))
    bindings = {
        "request_sha256": sha(request),
        "selected_sha256": sha(selected),
        "opinions_sha256": sha(references),
    }
    if any(review.get(k) != v for k, v in bindings.items()):
        raise ValueError("R9_CALIBRATION_QA_BINDING")
    if sealed["request_sha256"] != sha(request) or sealed["selected_sha256"] != sha(selected):
        raise ValueError("R9_CALIBRATION_REFERENCE_BINDING")
    if sealed["rubric_sha256"] != frozen["rubric_sha256"]:
        raise ValueError("R9_CALIBRATION_REFERENCE_RUBRIC")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("R9_CALIBRATION_QA_REVIEWER")
    expected = {
        (r["source_row"]["packet"]["case_id"], f"reader_{x}") for r in frozen["rows"] for x in "ab"
    }
    inspected = {(r["case_id"], r["reader"]) for r in review["inspected_opinions"]}
    if inspected != expected or len(review["inspected_opinions"]) != len(expected):
        raise ValueError("R9_CALIBRATION_QA_COVERAGE")
    excluded = set()
    for row in review["excluded"]:
        key = (row["case_id"], row["reader"])
        if (
            key not in expected
            or key in excluded
            or not isinstance(row["reason"], str)
            or not row["reason"].strip()
        ):
            raise ValueError("R9_CALIBRATION_QA_EXCLUSION")
        excluded.add(key)
    opinions = {(r["case_id"], r["reader"]): r for r in sealed["opinions"]}
    if set(opinions) != expected or len(sealed["opinions"]) != len(expected):
        raise ValueError("R9_CALIBRATION_REFERENCE_COVERAGE")
    counts = dict.fromkeys(("TRUE", "FALSE", "UNKNOWN"), 0)
    rows = []
    for entry in frozen["rows"]:
        packet = entry["source_row"]["packet"]
        keys = [(packet["case_id"], f"reader_{x}") for x in "ab"]
        pair = [opinions[k] for k in keys]
        for r, key in zip(pair, keys, strict=True):
            blind = {k: packet[k] for k in ("case_id", "HLC40")}
            prompt = (
                RUBRIC
                + f"\ncase_id={packet['case_id']}; reader={key[1]}\nPacket: "
                + json.dumps(blind)
            )
            if (
                r["request_sha256"] != sha(request)
                or r["selected_sha256"] != sha(selected)
                or r["rubric_sha256"] != frozen["rubric_sha256"]
                or r["model_id"] != MODELS[0 if key[1] == "reader_a" else 1]
                or r["prompt_sha256"] != hashlib.sha256(prompt.encode()).hexdigest()
            ):
                raise ValueError("R9_CALIBRATION_OPINION_BINDING")
        valid = all(
            r["status"] == "VALID" and k not in excluded for r, k in zip(pair, keys, strict=True)
        )
        if valid:
            for r, k in zip(pair, keys, strict=True):
                validate(r["opinion"], packet, k[1])
        label = (
            pair[0]["opinion"]["label"]
            if valid and pair[0]["opinion"]["label"] == pair[1]["opinion"]["label"]
            else "UNKNOWN"
        )
        disagreements = [
            c
            for c in CLAUSES
            if valid
            and pair[0]["opinion"]["clauses"][c]["label"]
            != pair[1]["opinion"]["clauses"][c]["label"]
        ]
        counts[label] += 1
        rows.append(
            {
                "case_id": packet["case_id"],
                "label": label,
                "clause_disagreements": disagreements,
                "qa_admitted": valid,
            }
        )
    result = {
        **bindings,
        "qa_sha256": sha(qa),
        "counts": counts,
        "cases": rows,
        "panel_qualified": counts["TRUE"] >= 2 and counts["FALSE"] >= 2,
        "fit_runs": 0,
        "update_runs": 0,
        "grade": "fixed development semantic anchors; successor qualification required",
    }
    if output.exists():
        raise ValueError("R9_CALIBRATION_FRESH_SUMMARY")
    output.mkdir(parents=True)
    write(output / "calibration.safe.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "collect", "summarize"))
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selected", type=Path)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--qa", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.request, args.output)
    elif args.action == "collect" and args.selected is not None and args.sandbox is not None:
        result = collect(args.request, args.selected, args.output, args.sandbox)
    elif (
        args.action == "summarize"
        and args.selected is not None
        and args.references is not None
        and args.qa is not None
    ):
        result = summarize(args.request, args.selected, args.references, args.qa, args.output)
    else:
        parser.error("collect requires --selected and --sandbox")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
