"""Immutable P10 supplementary observations and a finite qualification watch (#587).

One job builds raw -> parsed -> normalized once. Quality and the candidate ledger
share that intermediate. These products serve P10 qualification, not trading.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import multiprocessing
import re
import shutil
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.p10_source_contracts import (
    SCHEMA,
    digest,
    encode,
    instant,
    library_contracts,
    normalize,
    policy,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def code_identity() -> dict[str, str]:
    return {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("p10_supplement.py", "p10_source_contracts.py")
    }


def _fetch_child(sender: Any, request: Any, max_bytes: int) -> None:
    try:
        sender.send(importlib.import_module("astock.transport").fetch(request, max_bytes=max_bytes))
    finally:
        sender.close()


def bounded_fetch(request: Any, budget: dict[str, Any]) -> Any:
    """The outer process budget also covers DNS; source transport alone cannot."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_fetch_child, args=(sender, request, budget["max_response_bytes"]))
    child.start()
    sender.close()
    try:
        if receiver.poll(budget["source_wall_seconds"]):
            try:
                return receiver.recv()
            except EOFError:
                reason = "SOURCE_WORKER_FAILED"
        else:
            reason = "SOURCE_OPERATION_TIMEOUT"
    finally:
        receiver.close()
        if child.is_alive():
            child.terminate()
        child.join(timeout=2)
        if child.is_alive():
            child.kill()
            child.join()
    raw_type = importlib.import_module("astock.contracts").RawObservation
    return raw_type(request, utcnow(), b"", None, False, reason)


def _regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("SOURCE_EVIDENCE_FILE_REQUIRED")
    return path.read_bytes()


def _initialize(root: Path) -> None:
    if (
        not root.is_absolute()
        or root == Path("/")
        or any(p.is_symlink() for p in (root, *root.parents))
    ):
        raise ValueError("SOURCE_ISOLATED_ABSOLUTE_ROOT_REQUIRED")
    marker = root / "P10-SUPPLEMENT.json"
    if not marker.exists():
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise ValueError("SOURCE_EMPTY_NEW_ROOT_REQUIRED")
        marker.write_bytes(encode({"kind": "p10-supplement-store-v1", "owner": "P10"}))
    if json.loads(_regular(marker)) != {"kind": "p10-supplement-store-v1", "owner": "P10"}:
        raise ValueError("SOURCE_ROOT_IDENTITY_MISMATCH")
    for name in ("runs", "staging"):
        path = root / name
        if path.is_symlink():
            raise ValueError("SOURCE_LINK_FORBIDDEN")
        path.mkdir(exist_ok=True)


@contextlib.contextmanager
def _lock(root: Path, filename: str = ".lock") -> Iterator[None]:
    path = root / filename
    if path.is_symlink():
        raise ValueError("SOURCE_LINK_FORBIDDEN")
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".new")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("SOURCE_LINK_FORBIDDEN")
    temporary.write_bytes(encode(value))
    temporary.replace(path)


def verified_run(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("SOURCE_RUN_DIRECTORY_REQUIRED")
    value: dict[str, Any] = json.loads(_regular(path / "manifest.json"))
    if value["kind"] != "p10-supplement-run-v1" or value["schema"] != SCHEMA:
        raise ValueError("SOURCE_RUN_SCHEMA_MISMATCH")
    for filename, expected in value["files"].items():
        if not re.fullmatch(r"[a-z0-9_.-]+\.(json|bin)", filename):
            raise ValueError("SOURCE_EVIDENCE_PATH_INVALID")
        if hashlib.sha256(_regular(path / filename)).hexdigest() != expected:
            raise ValueError("SOURCE_EVIDENCE_DIGEST_MISMATCH")
    if set(p.name for p in path.iterdir()) != {*value["files"], "manifest.json"}:
        raise ValueError("SOURCE_UNEXPECTED_EVIDENCE_FILE")
    if value["semantic_identity"] != digest({"closure": value["closure"], "files": value["files"]}):
        raise ValueError("SOURCE_SEMANTIC_IDENTITY_MISMATCH")
    for key, filename in value["sources"].items():
        if filename not in value["files"] or not filename.endswith(".json"):
            raise ValueError("SOURCE_UNVERIFIED_PRODUCT_REFERENCE")
        product = json.loads(_regular(path / filename))
        raw_name = filename.removesuffix(".json") + ".bin"
        if (
            product["key"] != key
            or product["schema"] != SCHEMA
            or value["files"].get(raw_name) != product["body_sha256"]
        ):
            raise ValueError("SOURCE_PRODUCT_IDENTITY_MISMATCH")
    return value


def _history(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    return sorted(
        ((p, verified_run(p)) for p in (root / "runs").iterdir()),
        key=lambda x: (instant(x[1]["started_at"]), x[0].name),
    )


def _ledger(root: Path, config: dict[str, Any], at: datetime) -> dict[str, Any]:
    history = _history(root)
    rows = []
    current_policy = digest(config)
    watch_path = root / "watch.json"
    watch_receipt = json.loads(_regular(watch_path)) if watch_path.exists() else None
    for spec in config["sources"]:
        samples = []
        for directory, run in history:
            if spec["key"] in run["sources"]:
                sample = json.loads(_regular(directory / run["sources"][spec["key"]]))
                samples.append(
                    dict(
                        run_id=directory.name,
                        policy_sha256=run["closure"]["policy"],
                        code=run["closure"]["code"],
                        **sample,
                    )
                )
        current = [
            x
            for x in samples
            if x["policy_sha256"] == current_policy and x["code"] == code_identity()
        ]
        days = {instant(x["verified_at"]).date() for x in current if x["quality_ok"]}
        streak = 0
        # At most one result per UTC day/contract is counted; a failed attempt that
        # day wins over successes. Restarts or alternate --disabled sets cannot pad it.
        daily: dict[Any, list[dict[str, Any]]] = {}
        for sample in current:
            daily.setdefault(instant(sample["verified_at"]).date(), []).append(sample)
        previous = None
        for day in sorted(daily, reverse=True):
            if (previous is not None and previous - day != timedelta(days=1)) or not all(
                x["quality_ok"] for x in daily[day]
            ):
                break
            streak += 1
            previous = day
        first = instant(current[0]["verified_at"]) if current else None
        due = first + timedelta(days=config["watch"]["days"]) if first else None
        if (
            watch_receipt
            and watch_receipt["identity"]["policy"] == current_policy
            and watch_receipt["identity"]["code"] == code_identity()
        ):
            due = instant(watch_receipt["until"])
        latest = samples[-1] if samples else None
        status = "NOT_OBSERVED"
        if current:
            status = "WATCHING" if current[-1]["quality_ok"] else "HOLD"
            if due and at >= due:
                status = (
                    "REVIEW_DUE"
                    if (
                        status == "WATCHING"
                        and len(days) >= config["watch"]["min_distinct_success_days"]
                        and streak >= config["watch"]["min_consecutive_success_days"]
                        and (at - instant(current[-1]["verified_at"])).total_seconds()
                        <= config["watch"]["interval_seconds"] + 300
                    )
                    else "HOLD_REVIEW_DUE"
                )
        rows.append(
            {
                "key": spec["key"],
                "purpose": spec["purpose"],
                "status": status,
                "first_verified_at": first.isoformat() if first else None,
                "review_due_at": due.isoformat() if due else None,
                "last_verified_at": latest["verified_at"] if latest else None,
                "last_quality": {
                    "source_latest_notice_date": latest.get("source_latest_notice_date"),
                    "source_latest_notice_age_days": latest.get("source_latest_notice_age_days"),
                    **{
                        k: latest[k]
                        for k in (
                            "status",
                            "reason",
                            "quality_ok",
                            "quality_flags",
                            "source_row_count",
                            "source_latest_clock",
                            "source_latest_age_seconds",
                            "source_latest_date",
                            "source_latest_date_age_days",
                            "unit_contract",
                        )
                    },
                }
                if latest
                else None,
                "distinct_success_days": len(days),
                "consecutive_success_days": streak,
                "last_success_at": next(
                    (x["verified_at"] for x in reversed(samples) if x["quality_ok"]), None
                ),
                "last_failure_at": next(
                    (x["verified_at"] for x in reversed(samples) if not x["quality_ok"]), None
                ),
                "history": [
                    {
                        k: x[k]
                        for k in (
                            "run_id",
                            "verified_at",
                            "status",
                            "reason",
                            "quality_ok",
                            "quality_flags",
                        )
                    }
                    for x in samples
                ],
                "business_admission": False,
                "historical_pit_admission": False,
            }
        )
    return {
        "kind": "p10-supplement-candidates-v1",
        "as_of": at.isoformat(),
        "policy_sha256": current_policy,
        "candidates": rows,
        "business_admission": False,
        "automatic_promotion": False,
    }


def cycle(
    root: Path,
    config: dict[str, Any],
    revision: str,
    *,
    disabled: tuple[str, ...] = (),
    fetcher: Callable[[Any, dict[str, Any]], Any] = bounded_fetch,
    clock: Callable[[], datetime] = utcnow,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("SOURCE_FULL_REVISION_REQUIRED")
    started = instant(clock().isoformat())
    contracts = library_contracts(config)
    if not set(disabled) <= set(contracts):
        raise ValueError("SOURCE_UNKNOWN_DISABLED_CAPABILITY")
    closure = {
        "policy": digest(config),
        "code": code_identity(),
        "revision": revision,
        "library": config["library"],
        "schema": SCHEMA,
        "universe": {
            "securities": config["security_universe"],
            "funds": config.get("fund_universe", []),
            "etfs": config.get("etf_universe", []),
            "source_selection": config.get("universe_selection", {}),
        },
        "day": started.date().isoformat(),
        "requests": {k: r.identity for k, (r, _) in contracts.items()},
        "disabled": sorted(set(disabled)),
    }
    job_id = started.strftime("%Y%m%d") + "-" + digest(closure)
    _initialize(root)
    with _lock(root):
        target = root / "runs" / job_id
        if target.exists():
            existing = verified_run(target)
            if existing["closure"] != closure:
                raise ValueError("SOURCE_JOB_IDENTITY_MISMATCH")
            ledger = _ledger(root, config, started)
            _atomic(root / "candidates.json", ledger)
            return {
                "run_id": job_id,
                "disposition": "REUSED",
                "network_requests": 0,
                "manifest_sha256": hashlib.sha256(_regular(target / "manifest.json")).hexdigest(),
            }
        staging = root / "staging" / job_id
        if staging.exists():
            # An interrupted/unpublished stage is not a cache or an admitted snapshot.
            raise ValueError("SOURCE_INTERRUPTED_STAGE_REQUIRES_DISPOSITION")
        staging.mkdir()
        files: dict[str, str] = {}
        source_files: dict[str, str] = {}
        metrics = []
        network_requests = 0
        previous = _history(root)
        try:
            for spec in config["sources"]:
                key = spec["key"]
                request, parser = contracts[key]
                stem = key.replace(":", "-")
                if not re.fullmatch(r"[a-z0-9_.-]+", stem):
                    raise ValueError("SOURCE_KEY_INVALID")
                source_started = time.monotonic()
                raw_type = importlib.import_module("astock.contracts").RawObservation
                if key in disabled:
                    raw = raw_type(request, clock(), b"", None, False, "SOURCE_DISABLED_ROLLBACK")
                else:
                    raw = fetcher(request, config["budget"])
                    network_requests += 1
                if raw.request != request or len(raw.body) > config["budget"]["max_response_bytes"]:
                    raise ValueError("SOURCE_FETCH_ENVELOPE_MISMATCH")
                result = parser(raw)
                try:
                    normalized = normalize(result, spec, config)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    failed_type = importlib.import_module("astock.contracts")
                    result = failed_type.FetchResult(
                        raw, failed_type.Status.FAILED, "NORMALIZATION_CONTRACT_FAILED"
                    )
                    normalized = normalize(result, spec, config)
                    normalized["normalization_error_type"] = type(exc).__name__
                previous_good = next(
                    (
                        p.name
                        for p, v in reversed(previous)
                        if key in v["sources"]
                        and json.loads(_regular(p / v["sources"][key]))["quality_ok"]
                    ),
                    None,
                )
                normalized.update(
                    request_identity=request.identity,
                    body_sha256=raw.body_sha256,
                    request={
                        "method": request.method,
                        "url": request.url,
                        "body_hex": request.body.hex(),
                        "headers": list(request.headers),
                    },
                    http_status=raw.http_status,
                    complete=raw.complete,
                    transport_error=raw.error,
                    previous_good_run=previous_good,
                    current_available=normalized["quality_ok"],
                    previous_good_reused=False,
                )
                payloads = {stem + ".bin": raw.body, stem + ".json": encode(normalized)}
                for filename, data in payloads.items():
                    (staging / filename).write_bytes(data)
                    files[filename] = hashlib.sha256(data).hexdigest()
                source_files[key] = stem + ".json"
                metrics.append(
                    {
                        "source": key,
                        "stage": "raw_parse_normalize",
                        "elapsed_seconds": time.monotonic() - source_started,
                        "rows": len(normalized["records"]),
                        "bytes": len(raw.body),
                        "disposition": "BUILD",
                        "consumers": ["quality", "ledger"],
                        "raw_lifecycle": "ADM_P10_QUALIFICATION_EVIDENCE",
                    }
                )
            manifest = {
                "kind": "p10-supplement-run-v1",
                "schema": SCHEMA,
                "started_at": started.isoformat(),
                "finished_at": clock().isoformat(),
                "closure": closure,
                "files": files,
                "sources": source_files,
                "network_requests": network_requests,
                "metrics": metrics,
                "semantic_identity": digest({"closure": closure, "files": files}),
                "fallback": "ISOLATE_OPTIONAL_SOURCE_NO_VENDOR_SUBSTITUTION",
            }
            (staging / "manifest.json").write_bytes(encode(manifest))
            verified_run(staging)
            staging.rename(target)
        except BaseException:
            # Unpublished TMP is removed; already published runs are never overwritten.
            shutil.rmtree(staging)
            raise
        _atomic(root / "candidates.json", _ledger(root, config, instant(clock().isoformat())))
        return {
            "run_id": job_id,
            "disposition": "BUILD",
            "network_requests": network_requests,
            "manifest_sha256": hashlib.sha256(_regular(target / "manifest.json")).hexdigest(),
        }


def read_observation(
    root: Path, run_id: str, key: str, cutoff: str, *, policy_sha256: str
) -> dict[str, Any]:
    """Exact snapshot read: a later good value cannot silently fill an earlier failure."""
    if not re.fullmatch(r"[0-9]{8}-[a-f0-9]{64}", run_id):
        raise ValueError("SOURCE_RUN_ID_INVALID")
    path = root / "runs" / run_id
    run = verified_run(path)
    if run["closure"]["policy"] != policy_sha256:
        raise ValueError("SOURCE_READ_POLICY_MISMATCH")
    value: dict[str, Any] = json.loads(_regular(path / run["sources"][key]))
    if instant(value["verified_at"]) > instant(cutoff):
        raise ValueError("SOURCE_NOT_KNOWN_AT_CUTOFF")
    if not value["current_available"]:
        raise ValueError("SOURCE_CURRENT_OBSERVATION_UNAVAILABLE")
    return value


def watch(root: Path, config: dict[str, Any], revision: str, disabled: tuple[str, ...]) -> None:
    """Persist a non-renewable seven-day budget; container restarts do not extend it."""
    _initialize(root)
    with _lock(root, ".watch.lock"):
        _watch_loop(root, config, revision, disabled)


def _watch_loop(
    root: Path, config: dict[str, Any], revision: str, disabled: tuple[str, ...]
) -> None:
    path = root / "watch.json"
    identity = {
        "policy": digest(config),
        "revision": revision,
        "code": code_identity(),
        "disabled": sorted(set(disabled)),
    }
    with _lock(root):
        if path.exists():
            receipt = json.loads(_regular(path))
            if receipt["identity"] != identity:
                raise ValueError("SOURCE_WATCH_CHANGED_CONTRACT_REQUIRES_NEW_ROOT")
        else:
            started = utcnow()
            receipt = {
                "identity": identity,
                "started_at": started.isoformat(),
                "until": (started + timedelta(days=config["watch"]["days"])).isoformat(),
                "next_at": started.isoformat(),
                "state": "WATCHING",
            }
            _atomic(path, receipt)
    while utcnow() < instant(receipt["until"]):
        now = utcnow()
        if now >= instant(receipt["next_at"]):
            print(json.dumps(cycle(root, config, revision, disabled=disabled)), flush=True)
            receipt["next_at"] = (
                now + timedelta(seconds=config["watch"]["interval_seconds"])
            ).isoformat()
            _atomic(path, receipt)
        time.sleep(min(30, max(0.05, (instant(receipt["next_at"]) - utcnow()).total_seconds())))
    receipt["state"] = "STOPPED_REVIEW_DUE"
    _atomic(path, receipt)
    _atomic(root / "candidates.json", _ledger(root, config, utcnow()))
    print(json.dumps({"state": receipt["state"], "until": receipt["until"]}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("cycle", "watch", "status"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--disabled", action="append", default=[])
    args = parser.parse_args()
    config = policy(args.policy)
    if args.command == "watch":
        watch(args.root, config, args.revision, tuple(args.disabled))
    elif args.command == "cycle":
        print(json.dumps(cycle(args.root, config, args.revision, disabled=tuple(args.disabled))))
    else:
        _initialize(args.root)
        print(json.dumps(_ledger(args.root, config, utcnow()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
