"""Read-only Quanthus P10 four-node inventory, no production pointer mutation.

Each node reports its own real filesystem. CN01 and Hermes are independent
producers (equal dates do not imply equal manifest bytes); OCI must mirror
CN01 daily-core exactly. CN02 holds an isolated restored Base candidate, not
an assumed production mirror. Any missing check is UNKNOWN, never PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DAYS = (
    "2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08",
)
NODES = ("CN01", "CN02", "Hermes", "OCI")
CN02_RESTORED_ROOTS = (
    "/srv/agentdock/AgentDock/data/quanthus-ods-onedrive-20261003/restored/base/"
    "ods-base-202607-v2",
    "/srv/agentdock/AgentDock/data/quanthus-gdrive-20261002/restored",
    "/srv/agentdock/AgentDock/data/quanthus-daily-sync-20261003",
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _daily_root(node: str) -> Path | None:
    if node == "OCI":
        return Path("/home/ubuntu/data/quanthus/dwd-daily-core")
    if node in {"CN01", "Hermes"}:
        return Path("/srv/quanthus/dwd-dws-v2/daily/products/daily-core")
    return None


def daily_snapshot(
    *,
    root: Path,
    days: tuple[str, ...] = DAYS,
    deep_verify: bool = False,
) -> dict[str, Any]:
    pointer = root / "CURRENT_DAILY_CORE.json"
    result: dict[str, Any] = {
        "pointer_exists": pointer.is_file(),
        "pointer_verified": False,
        "pointer_market_date": None,
        "current_manifest_sha256": None,
        "market_dates_with_physical_package": [],
        "dates_with_missing_package": [],
        "deep_verified_market_dates": {},
        "deep_payload_verification_failures": {},
    }
    for day in days:
        candidates = list((root / "packages" / day).glob("*/manifest.json"))
        if candidates:
            result["market_dates_with_physical_package"].append(day)
        else:
            result["dates_with_missing_package"].append(day)
        if deep_verify and candidates:
            from quanthus.pipelines.securities_warehouse.daily_core_domain import (
                verify_daily_domain_increment,
            )
            valid = []
            failures = []
            for member in sorted(candidates):
                try:
                    verified = verify_daily_domain_increment(member.parent)
                    if verified.market_date.isoformat() != day:
                        raise ValueError("wrong-date DWD package")
                    valid.append({
                        "manifest_sha256": verified.manifest_sha256,
                        "content_digest": verified.content_digest,
                        "row_counts": dict(verified.row_counts),
                    })
                except (OSError, ValueError, RuntimeError, KeyError) as exc:
                    failures.append(f"{member.parent.name}:{type(exc).__name__}")
            if len(valid) == 1 and not failures:
                result["deep_verified_market_dates"][day] = valid[0]
            else:
                result["deep_payload_verification_failures"][day] = {
                    "verified_candidates": len(valid),
                    "rejected_candidates": failures,
                }
    if not pointer.is_file():
        return result
    payload = json.loads(pointer.read_text())
    if payload.get("kind") != "cn01_daily_core_dwd_current":
        raise ValueError("daily-core pointer kind is not the versioned product")
    rel = payload.get("manifest_path")
    if not isinstance(rel, str):
        raise ValueError("daily-core manifest path missing")
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("current manifest escapes daily-core root")
    if not path.is_file() or _sha(path) != payload.get("manifest_sha256"):
        raise ValueError("daily-core current does not physically match manifest SHA")
    result.update(
        pointer_verified=True,
        pointer_market_date=payload.get("market_date"),
        current_manifest_sha256=payload["manifest_sha256"],
    )
    return result


def minute_research_snapshot(
    root: Path,
    days: tuple[str, ...] = DAYS,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "product_exists": root.is_dir(),
        "per_date": {},
        "research_current_date": None,
    }
    current = root / "CURRENT_RESEARCH_1M.json"
    if current.exists():
        result["research_current_date"] = json.loads(current.read_text()).get("market_date")
    for day in days:
        file = root / "date-status" / f"{day}.json"
        if not file.is_file():
            result["per_date"][day] = {"status": "not_published"}
            continue
        data = json.loads(file.read_text())
        manifest = root / str(data.get("research_dwd_path", "")) / "manifest.json"
        report = {
            "status": "not_verified",
            "research_ready": data.get("research_ready"),
        }
        if (
            manifest.is_file()
            and manifest.resolve().is_relative_to(root.resolve())
            and _sha(manifest) == data.get("research_dwd_manifest_sha256")
        ):
            report["status"] = "manifest_sha_verified"
            report["manifest_sha256"] = data["research_dwd_manifest_sha256"]
            report["quality_grades"] = data.get("quality_grades")
        result["per_date"][day] = report
    return result


def collect_local(node: str) -> dict[str, Any]:
    if node not in NODES:
        raise ValueError("unsupported node identity")
    result: dict[str, Any] = {
        "node": node,
        "node_role": (
            "isolated_recovered_base22_not_current_market_producer"
            if node == "CN02" else
            "exact_cn01_daily_core_mirror"
            if node == "OCI" else "independent_market_daily_core_producer"
        ),
        "days_checked": list(DAYS),
        "daily_core": None,
        "minute_v1": None,
        "CN02_isolated_candidate": None,
        "environment_inspected_at": datetime.now(timezone.utc).isoformat(),
    }
    if node == "CN02":
        result["CN02_isolated_candidate"] = {
            path: {"exists": Path(path).exists(), "is_dir": Path(path).is_dir()}
            for path in CN02_RESTORED_ROOTS
        }
        return result
    root = _daily_root(node)
    assert root is not None
    result["daily_core"] = daily_snapshot(root=root, deep_verify=True)
    if node == "CN01":
        result["minute_v1"] = minute_research_snapshot(
            Path("/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v1")
        )
    return result


def compare_four_nodes(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    known = {s["node"]: s for s in snapshots}
    if len(known) != 4 or set(known) != set(NODES):
        raise ValueError("must include four genuinely distinct node snapshots")
    core = {}
    for node in ("CN01", "Hermes", "OCI"):
        ds = known[node].get("daily_core")
        if not ds or not ds.get("pointer_verified"):
            core[node] = "NOT_VERIFIED"
        elif ds.get("pointer_market_date") != "2026-10-08":
            core[node] = "STALE_OR_DIFFERENT_DATE"
        elif ds.get("dates_with_missing_package"):
            core[node] = "HISTORICAL_PACKAGES_MISSING"
        else:
            core[node] = "DATED_SOURCE_PRESENT"
    cn01 = known["CN01"].get("daily_core") or {}
    oci = known["OCI"].get("daily_core") or {}
    matching = (
        core.get("CN01") == "DATED_SOURCE_PRESENT"
        and core.get("OCI") == "DATED_SOURCE_PRESENT"
        and cn01.get("current_manifest_sha256")
        == oci.get("current_manifest_sha256")
    )
    core["OCI_exact_CN01_mirror"] = "PASS" if matching else "NOT_VERIFIED"
    isolated = known["CN02"].get("CN02_isolated_candidate")
    core["CN02"] = (
        "ISOLATED_BASE_CANDIDATE_NOT_PRODUCTION_MIRROR"
        if isinstance(isolated, dict) and any(
            record.get("is_dir") for record in isolated.values()
        ) else "ISOLATED_SCOPE_NOT_VERIFIED"
    )
    return {
        "schema_version": 1,
        "kind": "quanthus_p10_four_node_readonly_date_census",
        "core_reconciliation": core,
        "full_four_node_production_mirror_asserted": False,
        "latest_core_pointers_and_package_presence_qualified": (
            core["CN01"] == "DATED_SOURCE_PRESENT"
            and core["Hermes"] == "DATED_SOURCE_PRESENT"
            and matching
        ),
        "CN02_remains_isolated": True,
        "all_market_dates_deep_verified": all(
            set((known[n].get("daily_core") or {}).get(
                "deep_verified_market_dates", {}
            )) == set(DAYS)
            for n in ("CN01", "Hermes", "OCI")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", choices=NODES)
    parser.add_argument("--snapshots", type=Path)
    opts = parser.parse_args()
    if bool(opts.node) == bool(opts.snapshots):
        parser.error("select exactly one of --node or --snapshots")
    if opts.node:
        output = collect_local(opts.node)
    else:
        obj = json.loads(opts.snapshots.read_text())
        if not isinstance(obj, list):
            raise ValueError("snapshots should be an array of actual node reports")
        output = compare_four_nodes(obj)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
