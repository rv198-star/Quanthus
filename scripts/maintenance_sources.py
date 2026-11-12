"""Read-only projections of registered task, executor and verification authorities."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from maintenance_store import digest, now


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_tasks(source):
    path = Path(source["path"])
    paths = [path] if path.is_file() else sorted(path.glob(source.get("glob", "*.json")))
    if not path.exists():
        raise ValueError("registered native task source is absent")
    rows = []
    for p in paths[:100]:
        d = json.loads(p.read_text())
        if source["kind"] == "tplan":
            mission = d["mission"]
            for t in d["tasks"]:
                rows.append(
                    {
                        "native_ref": "tplan://" + mission["id"] + "/" + t["id"],
                        "mission_id": mission["id"],
                        "task_id": t["id"],
                        "status": t["status"],
                        "mission_status": mission["status"],
                        "source_digest": sha(p),
                    }
                )
        elif source["kind"] == "agentdock":
            rows.append(
                {
                    "native_ref": "agentdock-task:" + d["id"],
                    "task_id": d["id"],
                    "status": d["status"],
                    "phase": d.get("phase"),
                    "updated_at": d.get("updated_at"),
                    "source_digest": sha(p),
                }
            )
        else:
            raise ValueError("unsupported native task authority")
    active = [
        t
        for t in rows
        if t["status"] not in {"completed", "closed", "cancelled", "stopped", "archived"}
    ]
    return {
        "status": "ok",
        "authority": source["kind"],
        "registered_source_id": source["id"],
        "queried_count": len(paths[:100]),
        "truncated": len(paths) > 100,
        "active_count": len(active),
        "active_tasks": active[:30],
        "recent_terminal_tasks": [x for x in rows if x not in active][-5:],
        "source_set_digest": digest(rows),
    }


def verification(source, root, operational_fingerprint):
    path = Path(source["path"])
    d = json.loads(path.read_text())
    value = dict(d)
    claimed = value.pop("evidence_digest")
    if d.get("schema") != "maintenance.verification.v1" or digest(value) != claimed:
        raise ValueError("verification evidence digest mismatch")
    if d["operational_fingerprint"] != operational_fingerprint:
        raise ValueError("verification tested a different operational fingerprint")
    if any(sha(root / p) != expected for p, expected in d["tested_files"].items()):
        raise ValueError("verification tested source/test closure differs")
    log = path.parent / d["log_file"]
    if log.parent.resolve() != path.parent.resolve() or sha(log) != d["log_digest"]:
        raise ValueError("verification log cannot be read back")
    # Export the real result and the entry; private log bytes stay at their authority.
    return {
        "status": "ok" if d["exit_code"] == 0 else "failed",
        "registered_source_id": source["id"],
        "native_ref": "maintenance-verification:" + d["verification_run_id"],
        "source_digest": sha(path),
        "evidence": {k: v for k, v in d.items() if k != "log_file"},
    }


def executor_status(config, environment, executor):
    role = next(e for e in config["environments"] if e["id"] == environment)
    base = {
        "receiver": {
            "status": "available",
            "execution_model": "in_process_probe",
            "runtime": "python",
            "runtime_version": sys.version.split()[0],
        },
        "fixture_consumer": {
            "status": "available",
            "entry": "scripts/maintenance_consumer.py:consume",
        },
    }
    if environment != config["repair_environment"]:
        return {
            "status": "ok",
            "role": role["role"],
            **base,
            "coding_executor": {
                "status": "not_applicable",
                "configuration_ref": "config/maintenance-loop.v1.json:repair_environment",
            },
        }
    coding = {"status": "not_configured"}
    if executor:
        try:
            if not Path(executor).is_absolute() or not os.access(executor, os.X_OK):
                raise ValueError("executor unavailable")
            version = subprocess.run(
                [executor, "--version"], capture_output=True, text=True, timeout=10
            )
            auth = subprocess.run(
                [executor, "login", "status"], capture_output=True, text=True, timeout=10
            )
            coding = {
                "status": "available"
                if version.returncode == 0 and auth.returncode == 0
                else "failed",
                "executor": "codex-exec",
                "executable_name": Path(executor).name,
                "version": version.stdout.strip()[:100],
                "authenticated": auth.returncode == 0,
            }
        except (ValueError, OSError, subprocess.SubprocessError):
            coding = {"status": "failed", "executor": "codex-exec"}
    return {
        "status": "ok" if coding["status"] == "available" else coding["status"],
        "role": role["role"],
        **base,
        "coding_executor": coding,
    }


def collect(root, config, environment, store, executor, sources, fingerprint):
    collected = now()
    statuses = {}
    # Issues are selected work; native execution status is exported separately below.
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{config['repository']}/issues?state=open&per_page=100",
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )
        if result.returncode:
            raise ValueError("GitHub work query failed")
        items = [x for page in json.loads(result.stdout) for x in page]
        statuses["github_work"] = {
            "status": "ok",
            "authority": "github",
            "query_ref": f"https://github.com/{config['repository']}/issues?q=is%3Aopen",
            "open_issues": sum("pull_request" not in x for x in items),
            "open_pull_requests": sum("pull_request" in x for x in items),
            "items": [
                {
                    "number": x["number"],
                    "kind": "pull_request" if "pull_request" in x else "issue",
                    "state": x["state"],
                    "updated_at": x["updated_at"],
                    "url": x["html_url"],
                }
                for x in items[:15]
            ],
            "items_truncated": len(items) > 15,
            "source_set_digest": digest(items),
        }
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        statuses["github_work"] = {"status": "failed", "reason": "work_query_failed"}
    registered = sources.get("task_sources", [])
    statuses["native_tasks"] = {"status": "not_configured", "sources": []}
    if registered:
        rows = []
        for source in registered:
            try:
                rows.append(native_tasks(source))
            except (ValueError, OSError, KeyError):
                rows.append(
                    {
                        "registered_source_id": source["id"],
                        "status": "failed",
                        "reason": "native_task_read_failed",
                    }
                )
        statuses["native_tasks"] = {
            "status": "ok" if all(x["status"] == "ok" for x in rows) else "failed",
            "sources": rows,
        }
    statuses["executor"] = executor_status(config, environment, executor)
    proofs = []
    for source in sources.get("verification_sources", []):
        try:
            proofs.append(verification(source, root, fingerprint))
        except (ValueError, OSError, KeyError):
            proofs.append(
                {
                    "status": "failed",
                    "registered_source_id": source["id"],
                    "reason": "verification_readback_failed",
                }
            )
    statuses["verification"] = {
        "status": ("ok" if all(x["status"] == "ok" for x in proofs) else "failed")
        if proofs
        else "not_configured",
        "sources": proofs,
    }
    statuses["maintenance_executions"] = {
        "status": "ok" if store else "not_configured",
        "authority": "registered-maintenance-ledger",
        "ledger_handle": store.handle if store else None,
        "receiver": store.run() if store else None,
        "active_attempts": [
            dict(request_id=k, **store.attempt(k))
            for k, _, _, _, _ in store.rows()
            if store.attempt(k) and not store.attempt(k)["completed_at"]
        ][-15:]
        if store
        else [],
    }
    missing = [
        {"source": k, "status": v["status"]}
        for k, v in statuses.items()
        if v["status"] not in {"ok", "not_applicable"}
    ]
    return {
        "collected_at": collected,
        "source_status": statuses,
        "missing_sources": missing,
        "coverage": [k for k, v in statuses.items() if v["status"] == "ok"],
        "registration_digest": digest(sources),
    }
