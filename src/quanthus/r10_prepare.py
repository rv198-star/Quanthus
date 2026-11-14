"""Build shared native prerequisites, then freeze R10 roles before model scoring."""

from __future__ import annotations

import argparse
import hashlib
import os
import threading
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.r10_inputs import NativeInputs, digest, read, sha, write
from quanthus.r10_replay import build_feature_panel


def case_order(row: dict[str, Any]) -> str:
    i = row["identity"]
    return hashlib.sha256(
        ("R10-001|" + "|".join(i[k] for k in ["company_id", "listing_id", "anchor"])).encode()
    ).hexdigest()


def source_select(
    pool: list[dict[str, Any]], layout: dict[str, int], used: set[str]
) -> list[dict[str, Any]]:
    selected = []

    def number(r: dict[str, Any], key: str) -> Decimal:
        return Decimal(r["primary_facts"][key]["value"])

    def companies_once(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        representatives: dict[str, dict[str, Any]] = {}
        for row in ordered:
            representatives.setdefault(row["identity"]["company_id"], row)
        return list(representatives.values())

    for bucket, count in layout.items():
        available = [r for r in pool if r["available"] and r["identity"]["company_id"] not in used]
        if bucket in ["narrow", "boundary"]:
            available = sorted(
                [
                    r
                    for r in available
                    if abs(number(r, "directional_efficiency")) <= Decimal(".33")
                    and number(r, "width_change") <= Decimal("-.20")
                ],
                key=lambda r: (number(r, "range_width"), case_order(r)),
            )
            available = companies_once(available)
            if bucket == "boundary":
                start = max(0, (len(available) - count) // 2)
                available = available[start : start + count]
        else:
            available = sorted(
                [r for r in available if number(r, "width_change") > 0],
                key=lambda r: (-number(r, "width_change"), case_order(r)),
            )
            available = companies_once(available)
        if len(available) < count:
            raise ValueError(f"R10_SOURCE_QUOTA:{bucket}:available={len(available)}:needed={count}")
        for r in available[:count]:
            used.add(r["identity"]["company_id"])
            selected.append({"sampling_intent": bucket, "source_row": r})
    return selected


def exposure(c: dict[str, Any]) -> tuple[set[str], list[dict[str, Any]]]:
    identities, receipts = set(), []
    for name in c["exposure_sources"]:
        p = Path(name)
        if c["asset_hashes"][name] != sha(p):
            raise ValueError("R10_EXPOSURE_HASH")
        data = read(p)["rows"]
        source = [r.get("source_row", r).get("identity", r) for r in data]
        companies = {r["company_id"] for r in source}
        identities.update(companies)
        receipts.append(
            {"path": name, "sha256": sha(p), "rows": len(data), "companies": len(companies)}
        )
    if len(identities) != c["expected_known_company_exclusions"]:
        raise ValueError("R10_EXPOSURE_UNION")
    return identities, receipts


def prepare(
    project: Path,
    contract: Path,
    root: Path,
    *,
    resume: bool = False,
    physical_dwd_root: Path | None = None,
    deadline: str | None = None,
) -> dict[str, Any]:
    c = read(contract)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline or c["runtime"]["A_deadline"]):
        raise ValueError("R10_A_DEADLINE")
    root.mkdir(parents=True, exist_ok=True)
    binding = {
        "contract_sha256": sha(contract),
        "implementation": {
            name: sha(project / name)
            for name in (
                "src/quanthus/r10_inputs.py",
                "src/quanthus/r10_prepare.py",
                "src/quanthus/r10_replay.py",
                "src/quanthus/event_library/scan.py",
                "config/p4-candidate-price-volume-transition.research.v1.json",
                "config/p3-baseline-features.research.v1.json",
            )
        },
    }
    binding_path = root / "prerequisite-binding.json"
    if resume:
        if read(binding_path) != binding:
            raise ValueError("R10_PREREQUISITE_BINDING_CHANGED")
    else:
        write(binding_path, binding)
    native = NativeInputs(
        project, contract, root / "native", build=not resume, physical_dwd_root=physical_dwd_root
    )
    try:
        known, exp = exposure(c)
        if not (root / "known-exposure.private.json").exists():
            write(
                root / "known-exposure.private.json",
                {
                    "company_ids": sorted(known),
                    "sources": exp,
                    "scope": "R9 conservative superset; older history UNKNOWN",
                    "complete_global_history": False,
                },
            )
        if not resume:
            build_feature_panel(native)
        if (root / "E0.private.json").exists():
            raw = read(root / "E0.private.json")
        else:
            scan = native.scan()
            raw = {
                "rows": [
                    {
                        "event": r.record.to_dict(),
                        "listing_id": r.listing_id,
                        "listing_code": r.listing_code,
                        "company_id": r.company_id,
                        "industry_id": r.static_level1_industry_id,
                        "entry_date": str(r.entry_date),
                    }
                    for r in scan.candidates
                    if date(2021, 6, 1) <= r.record.identity.signal_date <= date(2021, 8, 31)
                ],
                "prefix_counters": scan.counters.to_dict(),
                "source_digest": native.content_digest,
                "prefix_start": "2020-01-02",
                "wave_origin": "2020-01-02",
                "caps_before_known_exclusion": True,
            }
            write(root / "E0.private.json", raw)
        if (root / "guard-pool.private.json").exists():
            guard_pool = read(root / "guard-pool.private.json")["rows"]
        else:
            ids = [
                native.identity(k, date(2021, 5, 31), "guard")
                for k, i in sorted(native.identities.items())
                if i["company_id"] not in known
                and str(i["effective_from"].date()) <= "2021-05-31"
                and native.venues[i["venue_id"]] in ["SSE", "SZSE"]
            ]
            guard_pool = native.qualify_cases(ids)
            write(root / "guard-pool.private.json", {"rows": guard_pool})
        used = set(known)
        guard = source_select(guard_pool, c["selection"]["guard_layout"], used)
        guard_companies = {r["source_row"]["identity"]["company_id"] for r in guard}
        eligible, excluded = [], []
        for r in raw["rows"]:
            signal = date.fromisoformat(r["event"]["signal_date"])
            anchor = native.dates[native.date_index[signal] - 1]
            reasons = []
            if anchor <= date(2021, 5, 31):
                reasons.append("MODEL_TIME_NOT_ELIGIBLE")
            if r["company_id"] in known:
                reasons.append("KNOWN_LEGACY_COMPANY")
            if r["company_id"] in guard_companies:
                reasons.append("CURRENT_GUARD_COMPANY")
            if reasons:
                excluded.append({"event_id": r["event"]["event_id"], "reasons": reasons})
            else:
                eligible.append(r)
        if not eligible:
            raise ValueError("R10_EMPTY_COMMON_E")
        if (root / "E-cases.private.json").exists():
            event_cases = read(root / "E-cases.private.json")["rows"]
        else:
            ids = []
            for r in eligible:
                signal = date.fromisoformat(r["event"]["signal_date"])
                ids.append(
                    native.identity(
                        r["listing_id"],
                        native.dates[native.date_index[signal] - 1],
                        "opportunity",
                        signal=signal,
                    )
                )
            event_cases = native.qualify_cases(ids)
            for case, event in zip(event_cases, eligible, strict=True):
                case["event_id"] = event["event"]["event_id"]
            write(root / "E-cases.private.json", {"rows": event_cases})
        # One fixed representative event per company is selected before source strata.
        representatives: dict[str, dict[str, Any]] = {}
        for r in sorted(event_cases, key=case_order):
            representatives.setdefault(r["identity"]["company_id"], r)
        evaluation = []
        for month, layout in c["selection"]["eval_month_layout"].items():
            pool = [
                r
                for r in representatives.values()
                if r["identity"]["signal_date"].startswith(month)
            ]
            evaluation.extend(source_select(pool, layout, used))
        evaluation = deepcopy(evaluation)
        for entry in evaluation:
            entry["source_row"]["identity"]["role"] = "evaluation"
        # Current role identities are committed together, before scoring or new reference.
        selected: dict[str, Any] = {
            "rows": guard + evaluation,
            "contract_sha256": sha(contract),
            "legacy_exposure": "UNKNOWN_DIAGNOSTIC_ONLY",
        }
        for key in ("company_id", "security_id", "listing_id", "sample_id", "episode_id"):
            if len({r["source_row"]["identity"][key] for r in selected["rows"]}) != 32:
                raise ValueError("R10_CURRENT_ROLE_IDENTITY_OVERLAP:" + key)
        if not (root / "selected.private.json").exists():
            write(root / "selected.private.json", selected)
        elif digest(read(root / "selected.private.json")) != digest(selected):
            raise ValueError("R10_SELECTED_IDENTITY_CHANGED")
        opportunity = {
            "rows": eligible,
            "source_digest": native.content_digest,
            "excluded": excluded,
            "raw_population": len(raw["rows"]),
        }
        if not (root / "E.private.json").exists():
            write(root / "E.private.json", opportunity)
        elif digest(read(root / "E.private.json")) != digest(opportunity):
            raise ValueError("R10_OPPORTUNITY_CHANGED")
        result = {
            "status": "SOURCE_AND_CURRENT_ROLE_IDENTITIES_FROZEN",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "contract_sha256": sha(contract),
            "E0": len(raw["rows"]),
            "E": len(eligible),
            "E_available": sum(r["available"] for r in event_cases),
            "excluded": dict(Counter(x for r in excluded for x in r["reasons"])),
            "guard_pool": len(guard_pool),
            "guard_available": sum(r["available"] for r in guard_pool),
            "guard_selected": len(guard),
            "eval_selected": len(evaluation),
            "known_companies": len(known),
            "E_sha256": sha(root / "E.private.json"),
            "selection_sha256": sha(root / "selected.private.json"),
            "E_cases_sha256": sha(root / "E-cases.private.json"),
            "native_members": len(native.members),
            "native_source_digest": native.content_digest,
            "current_roles_company_disjoint": len(
                {r["source_row"]["identity"]["company_id"] for r in selected["rows"]}
            )
            == 32,
            "new_reference_calls": 0,
            "new_updates": 0,
            "new_capital_trials": 0,
            "legacy_exposure": "UNKNOWN_DIAGNOSTIC_ONLY",
            "result_execution_ready": False,
        }
        write(root / "source-freeze.safe.json", result)
        return result
    finally:
        native.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    runtime = read(args.contract)["runtime"]
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[: runtime["cpu_cores"]])
    stopped = threading.Event()

    def monitor() -> None:
        from quanthus.r9_calibration import directory_bytes, process_tree_rss

        while not stopped.wait(0.5):
            reason = None
            if datetime.now(timezone.utc) >= datetime.fromisoformat(runtime["A_deadline"]):
                reason = "R10_A_DEADLINE"
            elif process_tree_rss(os.getpid()) > runtime["rss_bytes"]:
                reason = "R10_RSS_LIMIT"
            elif directory_bytes(args.root) > runtime["job_new_bytes"]:
                reason = "R10_WRITE_LIMIT"
            if reason:
                write(args.root / "runtime-stop.safe.json", {"reason": reason})
                os._exit(70)

    threading.Thread(target=monitor, daemon=True).start()
    result = prepare(
        Path(__file__).resolve().parents[2], args.contract, args.root, resume=args.resume
    )
    stopped.set()
    print(__import__("json").dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
