"""Authenticate one frozen, previously consumed derived selection and census it."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import resource
import time
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.expert_strategy.held_decision_census import census_held_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    payload = args.selection.read_bytes()
    if hashlib.sha256(payload).hexdigest() != args.selection_sha256:
        raise ValueError("CENSUS_SELECTION_CHANGED")
    frozen = json.loads(payload)
    pins = frozen["pins"]
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("CENSUS_OUTPUT_EXISTS")
    parent = args.output.parent.resolve(strict=True)
    if parent != args.output.parent.absolute():
        raise ValueError("CENSUS_OUTPUT_PARENT_ALIAS")
    source_job = Path(frozen["source_job"])
    if parent == source_job or parent.is_relative_to(source_job):
        raise ValueError("CENSUS_OUTPUT_SOURCE_OVERLAP")
    for name in pins:
        path = Path(name)
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise ValueError("CENSUS_INPUT_ALIAS")
        if args.output == path or path.is_relative_to(args.output):
            raise ValueError("CENSUS_OUTPUT_SOURCE_OVERLAP")

    consumed: set[str] = set()

    def load(path: Path, *, lines: bool = False) -> Any:
        identity = pins[str(path)]
        raw = path.read_bytes()
        if len(raw) != identity["bytes"] or hashlib.sha256(raw).hexdigest() != (identity["sha256"]):
            raise ValueError("CENSUS_INPUT_CHANGED:" + path.name)
        consumed.add(str(path))
        text = gzip.decompress(raw) if path.suffix == ".gz" else raw
        return [json.loads(line) for line in text.splitlines()] if lines else json.loads(text)

    held_paths = [Path(p) for p in pins if Path(p).name == "held-identities.TMP.private.json"]
    if len(held_paths) != 1:
        raise ValueError("CENSUS_HELD_IDENTITY_SELECTION")
    held = load(held_paths[0])["rows"]["B"]
    source_inputs = load(source_job / "inputs.final.frozen.json")
    trace = load(source_job / "trace/held-contexts.private.jsonl.gz", lines=True)
    if any(row["context"]["knowledge_cutoff"] != source_inputs["source_cutoff"] for row in trace):
        raise ValueError("CENSUS_ORIGINAL_SNAPSHOT_CUTOFF")
    shadow = load(source_job / "decision-shadow.private.json.gz")
    fact_paths = sorted(Path(p) for p in pins if Path(p).parent == source_job / "facts")
    support = []
    for path in fact_paths:
        support.extend(load(path)["held_decision_inputs"])
    result = census_held_decisions(
        held,
        trace,
        shadow,
        support,
        expected=frozen["expected_population"],
        first_date=date(2012, 1, 1),
        last_date=date(2021, 12, 31),
    )
    # Every pin, including the original receipts, must still match before writing
    # a PASS census. No source-root recovery or inference is attempted.
    for name, identity in pins.items():
        raw = Path(name).read_bytes()
        if len(raw) != identity["bytes"] or hashlib.sha256(raw).hexdigest() != (identity["sha256"]):
            raise ValueError("CENSUS_SOURCE_CHANGED_DURING_RUN")
    result.update(
        selection_sha256=args.selection_sha256,
        source_members_preserved=len(pins),
        authenticated_input_bytes=sum(p["bytes"] for p in pins.values()),
        parsed_members=len(consumed),
        facts_materializations=0,
        recorded_source_cutoff=source_inputs["source_cutoff"],
        facts_disposition="REUSE_EXACT_EXISTING_IMMUTABLE_DERIVED_SELECTION",
        original_baseline_evaluations=0,
        model_fits_updates_scores=0,
        market_source_reads=0,
        elapsed_seconds=time.monotonic() - started,
        peak_RSS_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    )
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({"status": "PASS_CENSUS_ONLY", "output": str(args.output)}))


if __name__ == "__main__":
    main()
