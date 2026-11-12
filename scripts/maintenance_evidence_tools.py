#!/usr/bin/env python3
"""Restricted stdio MCP access to private Loop3 evidence; no shell or arbitrary URL tools."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from maintenance_evidence import (
    EvidenceError,
    HFStore,
    operator_environment,
    read_pointer,
)
from maintenance_store import canonical, digest

BINDINGS = {
    "wff": ("rv198-star/software-lifecycle-skills", "oci-dev"),
    "quanthus": ("rv198-star/Quanthus", "cn02-dev"),
    "aiwork": ("rv198-star/aiwork", "oci-dev"),
    "controller": (None, "cloud"),
}
REPORT_SCHEMAS = {
    "maintenance.daily.v1",
    "maintenance.acceptance.v1",
    "maintenance.readback-confirmation.v1",
}


def tool(name, description, properties, required, readonly):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": readonly,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


PROJECT = {"type": "string", "enum": list(BINDINGS)}
TOOLS = [
    tool(
        "read_evidence",
        "Read and validate a Loop3 GitHub evidence pointer from its authenticated fixed HF revision.",
        {"pointer": {"type": "object"}},
        ["pointer"],
        True,
    ),
    tool(
        "store_report",
        "Store a signed Loop3 daily/acceptance report in the private HF dataset; return a stable small pointer.",
        {"project": PROJECT, "report": {"type": "object"}},
        ["project", "report"],
        False,
    ),
    tool(
        "read_report",
        "Read and validate a stored Loop3 report through its fixed HF pointer.",
        {"pointer": {"type": "object"}},
        ["pointer"],
        True,
    ),
]


class EvidenceTools:
    def __init__(self, settings, state):
        os.environ.update(operator_environment(settings))
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = state / "report-pointers.sqlite3"
        self.cache = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.cache.execute(
            "CREATE TABLE IF NOT EXISTS pointers (digest TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.cache.commit()

    def backend(self, project):
        if project not in BINDINGS:
            raise EvidenceError("Loop3 project is not registered")
        return HFStore(
            os.environ["LOOP3_HF_REPO_ID"],
            project,
            BINDINGS[project][1],
            os.environ["LOOP3_HF_TOKEN"],
        )

    def call(self, name, arguments):
        if name == "read_evidence":
            pointer = arguments["pointer"]
            project = next(
                (
                    k
                    for k, (repo, env) in BINDINGS.items()
                    if repo
                    and repo == pointer.get("repository")
                    and env == pointer.get("environment_id")
                ),
                None,
            )
            if project is None:
                raise EvidenceError("Loop3 project is not registered")
            repo, env = BINDINGS[project]
            config = {
                "repository": repo,
                "environments": [{"id": env}],
                "evidence_store": {"kind": "hf-private-dataset", "project": project},
            }
            return read_pointer(config, pointer)
        if name == "store_report":
            project, report = arguments["project"], arguments["report"]
            backend = self.backend(project)
            if (
                report.get("schema") not in REPORT_SCHEMAS
                or not report.get("batch_id")
                or not report.get("challenge")
            ):
                raise EvidenceError("Loop3 report schema or batch identity is invalid")
            if digest(
                {k: v for k, v in report.items() if k != "report_digest"}
            ) != report.get("report_digest"):
                raise EvidenceError("Loop3 report digest differs")
            if (
                BINDINGS[project][0]
                and report.get("repository") != BINDINGS[project][0]
            ):
                raise EvidenceError("Loop3 report project does not match")
            key = digest({"project": project, "report": report})
            row = self.cache.execute(
                "SELECT value FROM pointers WHERE digest=?", (key,)
            ).fetchone()
            if row:
                pointer = json.loads(row[0])
                if backend.read(pointer["evidence"]) != report:
                    raise EvidenceError("Retained HF report readback differs")
                return pointer
            reference = backend.put("report", report)
            pointer = {
                "schema": "maintenance.report-pointer.v1",
                "project": project,
                "batch_id": report["batch_id"],
                "challenge": report["challenge"],
                "report_digest": report["report_digest"],
                "evidence": reference,
            }
            pointer["pointer_digest"] = digest(pointer)
            self.cache.execute(
                "INSERT OR IGNORE INTO pointers VALUES (?,?)", (key, canonical(pointer))
            )
            self.cache.commit()
            return json.loads(
                self.cache.execute(
                    "SELECT value FROM pointers WHERE digest=?", (key,)
                ).fetchone()[0]
            )
        if name == "read_report":
            pointer = arguments["pointer"]
            if pointer.get("schema") != "maintenance.report-pointer.v1" or digest(
                {k: v for k, v in pointer.items() if k != "pointer_digest"}
            ) != pointer.get("pointer_digest"):
                raise EvidenceError("Loop3 report pointer digest differs")
            if pointer["evidence"].get("role") != "report":
                raise EvidenceError("Loop3 report artifact role differs")
            report = self.backend(pointer.get("project")).read(pointer["evidence"])
            if any(
                report.get(k) != pointer.get(k)
                for k in ("batch_id", "challenge", "report_digest")
            ):
                raise EvidenceError("Loop3 report identity differs")
            if (
                digest({k: v for k, v in report.items() if k != "report_digest"})
                != report["report_digest"]
            ):
                raise EvidenceError("Loop3 report digest differs")
            return report
        raise EvidenceError("Unknown Loop3 evidence tool")


def serve(service, source=sys.stdin, destination=sys.stdout):
    for line in source:
        request = None
        try:
            if len(line.encode()) > 2_100_000:
                raise EvidenceError("MCP request exceeds its size bound")
            request = json.loads(line)
            if "id" not in request:
                continue
            method, params = request.get("method"), request.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "loop3-hf-evidence", "version": "1.2.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                value = service.call(params["name"], params.get("arguments", {}))
                result = {
                    "content": [{"type": "text", "text": canonical(value)}],
                    "structuredContent": value,
                }
            else:
                raise EvidenceError("Unsupported MCP method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
            request_id = (
                request.get("id") if isinstance(locals().get("request"), dict) else None
            )
            message = (
                str(error)
                if isinstance(error, EvidenceError)
                else "Loop3 evidence request is invalid"
            )
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": message},
            }
        destination.write(canonical(response) + "\n")
        destination.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-config", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.operator_config.read_text())
    service = EvidenceTools(settings, settings["state_dir"])
    try:
        serve(service)
    finally:
        service.cache.close()


if __name__ == "__main__":
    main()
