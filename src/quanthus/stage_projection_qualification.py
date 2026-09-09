"""Immutable F9 artifact qualification for the formal Stage-10/11 Projection Contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.stage_projection_artifact import (
    FILES,
    MANIFEST_SCHEMA,
    build_projection_qualification,
    load_projection_qualification_request,
    verify_stage_projection_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

REQUEST_PATH = "config/stage-10-11-projection-qualification.v1.json"


def write_projection_qualification(
    output: Path,
    request: dict,
    *,
    code_revision: str,
) -> None:
    payloads, summary = build_projection_qualification(request)
    if set(payloads) != FILES:
        raise ValueError("projection qualification payload inventory differs")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": code_revision,
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "formal_projection_digest": summary["formal_projection_digest"],
        "projection_contract_digest": summary["projection_contract_digest"],
        "source_support_facts_digest": summary["source_support_facts_digest"],
        "result_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(value).hexdigest()
            for name, value in payloads.items()
        },
    }
    payloads = {**payloads, "manifest.json": canonical(manifest)}
    payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.stock-stage-projection-qualification-ready.v1",
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
        raise ValueError("projection qualification requires the fixed governed request")
    if len(args.code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.code_revision
    ):
        raise ValueError("projection qualification requires exact lowercase code revision")
    root = find_project_root()
    request = load_projection_qualification_request(root / REQUEST_PATH)
    output = Path(args.output)
    write_projection_qualification(output, request, code_revision=args.code_revision)
    verified = verify_stage_projection_artifact(
        output,
        code_revision=args.code_revision,
        request_digest=digest(request),
    )
    print(json.dumps(verified, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
