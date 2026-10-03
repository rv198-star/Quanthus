"""Extract exact accepted Facts input identities without deriving labels or ACTIVE."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.stage_learning.p5_contracts import REQUEST_DIGEST
from quanthus.stage_learning.p5_input_census import CensusError, checked_child, digest, file_digest

IDENTITY_FIELDS = (
    "security_code",
    "market_date",
    "session_index",
    "cutoff",
    "security_id",
    "listing_id",
    "exchange",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "dwd_known_from",
    "coordinate_epoch",
    "finality",
    "quality_reason",
    "reset_reason",
    "calendar_member_digest",
    "action_member_digest",
)


def project_members(raw: bytes, request: dict[str, Any]) -> tuple[list[dict], dict]:
    lines = raw.splitlines()
    if len(lines) > request["maximum_rows"]:
        raise CensusError("Facts sidecar row budget exceeded")
    members = []
    seen = set()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for line in lines:
        row = json.loads(line)
        if not all(key in row for key in IDENTITY_FIELDS):
            raise CensusError("Facts source identity schema differs")
        day = date.fromisoformat(row["market_date"])
        if (
            not date.fromisoformat(request["source_market_start"])
            <= day
            <= date.fromisoformat(request["source_market_end"])
            or row["security_code"] not in request["security_codes"]
            or row["exchange"] not in (request["exchanges"])
        ):
            raise CensusError("Facts source member outside frozen scope")
        key = (row["security_id"], row["listing_id"], row["market_date"])
        if key in seen or type(row["session_index"]) is not int:
            raise CensusError("Facts source identity is repeated or invalid")
        seen.add(key)
        member = {key: row[key] for key in IDENTITY_FIELDS}
        member["source_member_digest"] = digest(
            {key: value for key, value in row.items() if key not in {"session_index", "cutoff"}}
        )
        member["ACTIVE"] = None
        members.append(member)
        groups[(row["security_id"], row["listing_id"], row["coordinate_epoch"])].append(member)
    members.sort(key=lambda x: (x["security_id"], x["market_date"], x["listing_id"]))
    segments = []
    possible_windows = 0
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda x: x["market_date"])
        length = maximum = 0
        previous = None
        for row in rows:
            eligible = (
                all(
                    row.get(field)
                    for field in (
                        "security_id",
                        "listing_id",
                        "coordinate_epoch",
                        "calendar_member_digest",
                        "action_member_digest",
                        "source_revision",
                        "source_artifact_sha256",
                    )
                )
                and row["finality"] == "final"
                and row["quality_reason"] is None
            )
            # A reset cannot carry preceding history, even with the same textual epoch.
            extends = previous is not None and row["session_index"] == previous + 1
            length = ((length + 1) if extends and not row["reset_reason"] else 1) if eligible else 0
            maximum = max(maximum, length)
            possible_windows += int(length >= request["window_sessions"])
            previous = row["session_index"]
        segments.append(
            {
                "security_id": key[0],
                "listing_id": key[1],
                "coordinate_epoch": key[2],
                "rows": len(rows),
                "first_date": rows[0]["market_date"],
                "last_date": rows[-1]["market_date"],
                "longest_source_session_run": maximum,
            }
        )
    summary = {
        "source_rows": len(members),
        "securities": len({x["security_id"] for x in members}),
        "identity_coordinate_segments": segments,
        "contiguous_120_member_identity_windows": possible_windows,
        "qualified_P5_feature_windows": None,
        "active_population_count": None,
        "independent_reference_capacity": None,
        "quality_reason_counts": dict(
            sorted(Counter(str(x["quality_reason"]) for x in members).items())
        ),
        "knowledge_view": "retrospective",
        "b_acceptance": False,
        "scope_extrapolation": False,
        "source_member_identity_digest": digest(members),
    }
    return members, summary


def run_sidecar(root: Path, request_path: Path, output: Path, code_revision: str) -> dict[str, Any]:
    request = json.loads(request_path.read_text())
    if (
        request.get("schema_version") != "quanthus.p5-stage-001-facts-sidecar-request.v1"
        or request.get("p5_request_sha256") != REQUEST_DIGEST
        or request.get("activation_issue") != 402
        or request.get("source_market_start") != "2019-10-08"
        or request.get("source_market_end") != "2020-03-31"
        or request.get("security_codes") != ["000001", "300750", "600519"]
        or request.get("window_sessions") != 120
        or request.get("maximum_rows") != 5000
        or request.get("maximum_member_bytes") != 16777216
        or request.get("member") != "source-inputs.jsonl"
        or len(request.get("sources", [])) != 2
        or re.fullmatch(r"[0-9a-f]{40}", code_revision) is None
    ):
        raise CensusError("Facts sidecar frozen boundary differs")
    if output.exists():
        raise CensusError("Facts sidecar output already exists")
    raw = None
    for source in request["sources"]:
        if re.fullmatch(r"run_[0-9a-f]{32}", source["run_id"]) is None:
            raise CensusError("invalid Facts run identity")
        run = root / source["run_id"]
        if run.is_symlink():
            raise CensusError("symlink Facts source")
        manifest_path = checked_child(run, "manifest.json")
        if file_digest(manifest_path) != source["manifest_sha256"]:
            raise CensusError("Facts source manifest differs")
        ready = json.loads(checked_child(run, "READY").read_text())
        if ready != {
            "schema_version": "quanthus.structure-f9-ready.v1",
            "manifest_sha256": source["manifest_sha256"],
        }:
            raise CensusError("Facts READY differs")
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest.get("schema_version") != "quanthus.structure-f9-artifact.v1"
            or manifest.get("research_only") is not True
            or manifest.get("execution_authorized") is not False
            or manifest["code_revision"] != request["source_code_revision"]
            or manifest["request_digest"] != request["source_request_digest"]
            or manifest["files"].get(request["member"]) != request["member_sha256"]
        ):
            raise CensusError("Facts source provenance differs")
        path = checked_child(run, request["member"])
        if (
            path.stat().st_size > request["maximum_member_bytes"]
            or file_digest(path) != request["member_sha256"]
        ):
            raise CensusError("Facts source member differs")
        # Both accepted copies must match; parse only one, never double the denominator.
        if raw is None:
            raw = path.read_bytes()
    if raw is None:
        raise CensusError("Facts sidecar source closure empty")
    members, summary = project_members(raw, request)
    summary.update(
        {
            "schema_version": "quanthus.p5-stage-001-facts-sidecar-result.v1",
            "request_sha256": file_digest(request_path),
            "code_revision": code_revision,
            "source_member_sha256": request["member_sha256"],
            "sources": request["sources"],
        }
    )
    output.mkdir(parents=True)
    (output / "members.jsonl").write_text(
        "".join(json.dumps(m, sort_keys=True) + "\n" for m in members)
    )
    (output / "summary.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    manifest = {
        "schema_version": "quanthus.p5-stage-001-facts-sidecar-manifest.v1",
        "code_revision": code_revision,
        "request_sha256": file_digest(request_path),
        "files": {name: file_digest(output / name) for name in ("members.jsonl", "summary.json")},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (output / "READY").write_text(file_digest(output / "manifest.json") + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_sidecar(args.root, args.request, args.output, args.code_revision), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
