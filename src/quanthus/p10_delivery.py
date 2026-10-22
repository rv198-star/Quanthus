"""Drain durable data events: operational mail and exact Base2.4 increment backups.

No collector, data publication or formal-product deletion is reachable here.
Failures are retained and retried independently of the successful data stage.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from quanthus import p10_gdrive_backup as drive
from quanthus.p10_operational_mail import atomic, notify, validate
from quanthus.pipelines.securities_warehouse.container_code import digest, verify_bundle


def package(
    root: Path,
    *,
    identifier: str,
    layer: str,
    day: str,
    proof: str,
    expected: str,
    partial: bool,
    remaining_bytes: int,
    until: float,
) -> dict[str, Any]:
    drive.physical(root, directory=True)
    if "sha256:" + digest(root / proof) != expected:
        raise ValueError("P10_DELIVERY_UPSTREAM_MANIFEST_CHANGED")
    names = sorted(drive.tree(root))
    if len(names) > 50000 or sum((root / name).stat().st_size for name in names) > remaining_bytes:
        raise ValueError("P10_DELIVERY_FREEZE_SOURCE_BUDGET")
    if time.monotonic() >= until:
        raise TimeoutError("P10_DELIVERY_FREEZE_TIMEOUT")
    files = {
        name: {"bytes": (root / name).stat().st_size, "sha256": digest(root / name)}
        for name in names
    }
    return {
        "id": identifier,
        "layer": layer,
        "cadence": "daily",
        "business_date": day,
        "root": str(root),
        "files": files,
        "qualification_receipt": proof,
        "qualification": "partial_scope" if partial else "qualified_scope",
        "limitations": ["Native 5m held; independently qualified daily core only"]
        if partial
        else [],
    }


def freeze_daily(
    event: dict[str, Any], routing: dict[str, Any], runtime: Path, revision: str
) -> dict[str, Any]:
    """Select only the exact verified date entry named by the immutable job event."""
    validate(event)
    day = event["market_date"]
    entry = json.loads((runtime / "DATES.json").read_bytes())["dates"][day]
    if entry.get("execution_authorized") is not False:
        raise ValueError("P10_DELIVERY_DATA_GATE_DIFFERS")
    packages: list[dict[str, Any]] = []
    until = time.monotonic() + min(600, routing["limits"]["timeout_seconds"])

    def add(
        root: Path, identifier: str, layer: str, proof: str, expected: str, partial: bool = False
    ) -> None:
        if not root.is_relative_to(runtime):
            raise ValueError("P10_DELIVERY_SOURCE_OUTSIDE_RUNTIME")
        packages.append(
            package(
                root,
                identifier=identifier,
                layer=layer,
                day=day,
                proof=proof,
                expected=expected,
                partial=partial,
                remaining_bytes=routing["limits"]["max_source_bytes"]
                - sum(f["bytes"] for p in packages for f in p["files"].values()),
                until=until,
            )
        )

    if event["status"] == "closed":
        if entry.get("event_manifest_sha256") not in event["manifests"]:
            raise ValueError("P10_DELIVERY_DATE_EVENT_IDENTITY_DIFFERS")
        add(
            Path(entry["event_source"]),
            "ods-events",
            "ODS",
            "release.json",
            entry["event_manifest_sha256"],
        )
    elif event["status"] in {"complete", "partial"}:
        partial = event["status"] == "partial"
        key = "core" if partial else "product"
        if (
            entry.get(key + "_manifest_sha256") not in event["manifests"]
            or entry.get("base_manifest_sha256") != routing["base_manifest_sha256"]
        ):
            raise ValueError("P10_DELIVERY_DATE_EVENT_IDENTITY_DIFFERS")
        add(
            Path(entry[key]),
            "dwd-" + key,
            "DWD",
            "manifest.json",
            entry[key + "_manifest_sha256"],
            partial,
        )
        run = Path(entry["core"]).parent
        for label, lane in (("market", "market_daily_core"), ("events", "calendar_daily_events")):
            source = json.loads((run / (label + "-source.json")).read_bytes())
            add(
                Path(source["release_dir"]),
                "ods-" + label,
                "ODS",
                "release.json",
                entry["source_manifest_sha256"][lane],
            )
        # The sealed native acquisition package retains original raw observations,
        # acquisition evidence and its adapter/grade material together. It is an
        # acquisition backup, not a newly qualified or separate consumer product.
        if entry.get("native_source"):
            add(
                Path(entry["native_source"]),
                "ods-native5m-acquisition",
                "ODS",
                "manifest.json",
                entry["native_manifest_sha256"],
                partial,
            )
    else:
        raise ValueError("P10_DELIVERY_NO_QUALIFIED_DATA_SCOPE")
    return {
        "kind": "p10-base24-backup-selection-v1",
        "policy_sha256": digest(drive.POLICY),
        "base_version": "2.4",
        "base_manifest_sha256": routing["base_manifest_sha256"],
        "code_revision": revision,
        "intervals": ["1d", "5m"],
        "execution_authorized": False,
        "formal_product_deletion": "forbidden",
        "remote_name": routing["remote_name"],
        "folder_ids": routing["folder_ids"],
        "limits": routing["limits"],
        "packages": packages,
    }


def drain(
    routing_path: Path,
    runtime: Path,
    events: Path,
    state: Path,
    mail_state: Path,
    mail_secret: Path,
    cloud_secret: Path,
    revision: str,
) -> dict[str, Any]:
    routing = json.loads(routing_path.read_bytes())
    policy = json.loads(drive.POLICY.read_bytes())
    if (
        routing.get("kind") != "p10-base24-delivery-routing-v1"
        or routing.get("base_manifest_sha256") != policy["base_manifest_sha256"]
        or routing.get("code_revision") != revision
        or routing.get("notify_statuses") != ["complete", "partial", "closed", "busy", "failed"]
    ):
        raise ValueError("P10_DELIVERY_ROUTING_INVALID")
    for p in (runtime, events, state, mail_state):
        drive.physical(p, directory=True)
    for source in (runtime, events):
        if state.is_relative_to(source) or source.is_relative_to(state):
            raise ValueError("P10_DELIVERY_STATE_SOURCE_OVERLAP")
    with (state / "DELIVERY.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        paths = sorted(events.glob("*.json"))
        if len(paths) > 4096:
            raise ValueError("P10_DELIVERY_EVENT_CENSUS_BUDGET")
        results = []
        for path in paths:
            drive.physical(path)
            if path.stat().st_size > 8192:
                raise ValueError("P10_DELIVERY_SMALL_EVENT_REQUIRED")
            raw = path.read_bytes()
            event = json.loads(raw)
            validate(event)
            key = hashlib.sha256(raw).hexdigest()
            mail = notify(path, mail_state, mail_secret)
            done = state / (key + ".delivered.json")
            if done.exists():
                results.append({"event": path.name, "mail": mail, "backup": "already_verified"})
                continue
            backup_status = "no_qualified_data_scope"
            if event["role"] in {"daily", "recovery"} and event["status"] in {
                "complete",
                "partial",
                "closed",
            }:
                selected = state / (key + ".selection.json")
                try:
                    if not selected.exists():
                        atomic(selected, freeze_daily(event, routing, runtime, revision))
                    # Keep the durable outbox outside data sources; pass an isolated
                    # small plan file so backup never makes its state a source ancestor.
                    with tempfile.TemporaryDirectory(prefix="p10-delivery-", dir="/tmp") as tmp:
                        plan = Path(tmp) / "selection.json"
                        plan.write_bytes(selected.read_bytes())
                        result = drive.backup(plan, state, cloud_secret)
                    atomic(
                        done,
                        {
                            "event_sha256": key,
                            "selection_sha256": result["selection_sha256"],
                            "status": "cloud_bytes_verified",
                        },
                    )
                    backup_status = "cloud_bytes_verified"
                except Exception as error:
                    code = str(error)
                    code = (
                        code
                        if re.fullmatch(r"P10_[A-Z0-9_]{1,140}", code)
                        else type(error).__name__
                    )
                    atomic(state / (key + ".failure.json"), {"event_sha256": key, "reason": code})
                    # No secret-bearing provider exception text or raw data in state/logs.
                    backup_status = "failed_retained_for_retry"
            results.append({"event": path.name, "mail": mail, "backup": backup_status})
        result = {"status": "delivery_checked", "events": results}
        atomic(state / "DELIVERY.json", result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    revision = json.loads((root / "SOURCE.json").read_bytes())["revision"]
    verify_bundle(root, revision)
    secret = Path("/run/secrets/gdrive/oauth-bridge.json")
    if not secret.exists():
        secret = secret.with_name("rclone.conf")
    result = drain(
        args.routing,
        args.runtime,
        Path("/run/events"),
        Path("/run/backup"),
        Path("/run/mail"),
        Path("/run/secrets/mail.json"),
        secret,
        revision,
    )
    print(json.dumps(result, sort_keys=True))
    if any(
        v["mail"] == "failed_event_retained" or v["backup"] == "failed_retained_for_retry"
        for v in result.get("events", [])
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
