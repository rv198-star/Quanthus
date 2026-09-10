"""Governed immutable-source qualification runner for Trend Evolution Facts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_trend_evolution_artifact import (
    FILES,
    MANIFEST_SCHEMA,
    build_trend_evolution_qualification,
    load_trend_evolution_qualification,
    verify_trend_evolution_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

REQUEST_PATH = "config/structure-trend-evolution-qualification.v1.json"


def write_trend_evolution_qualification(
    output: Path,
    request: dict,
    *,
    code_revision: str,
) -> None:
    payloads, summary = build_trend_evolution_qualification(request)
    if set(payloads) != FILES:
        raise ValueError("Trend Evolution qualification payload inventory differs")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": code_revision,
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "fact_contract_digest": summary["fact_contract_digest"],
        "trend_facts_digest": summary["trend_facts_digest"],
        "source_inputs_digest": summary["source_inputs_digest"],
        "result_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(value).hexdigest()
            for name, value in payloads.items()
        },
    }
    payloads = {**payloads, "manifest.json": canonical(manifest)}
    payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.trend-evolution-qualification-ready.v1",
            "manifest_sha256": "sha256:"
            + hashlib.sha256(payloads["manifest.json"]).hexdigest(),
        }
    )
    for name, data in payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    if Path(args.request).as_posix() != REQUEST_PATH:
        raise ValueError("Trend Evolution qualification requires the fixed governed request")
    if len(args.code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.code_revision
    ):
        raise ValueError("Trend Evolution qualification requires exact lowercase code revision")
    root = find_project_root()
    request = load_trend_evolution_qualification(root / REQUEST_PATH)
    output = Path(args.output)
    write_trend_evolution_qualification(output, request, code_revision=args.code_revision)
    verified = verify_trend_evolution_artifact(
        output,
        code_revision=args.code_revision,
        request_digest=digest(request),
    )
    print(json.dumps(verified, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
