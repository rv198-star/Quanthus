"""P10 #538: exact dated status evidence from retained ODS responses.

This additive product qualifies dated provider observations only. It never
resolves canonical conflicts, grants IPO/price/execution eligibility, or changes
old source bytes and policies. Its verifier consumes only embedded originals.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import file_sha
from .base22_sources import _fingerprint, _tree, disjoint_output, physical_path

VERSION = "quanthus.p10-dated-status-evidence.v1"
FLAGS = {"0", "1"}
PROVIDER = "baostock.query_history_k_data_plus"


class StatusEvidenceError(ValueError):
    """Unqualified evidence or immutable publication failure."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise StatusEvidenceError(reason)


def _instant(value: Any) -> datetime:
    _require(isinstance(value, str), "CAPTURE_MISSING")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StatusEvidenceError("CAPTURE_INVALID") from exc
    _require(parsed.utcoffset() is not None, "CAPTURE_ZONE_MISSING")
    return parsed.astimezone(timezone.utc)


def _day(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    _require(isinstance(value, str), "DATE_INVALID")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise StatusEvidenceError("DATE_INVALID") from exc


def _observation(
    code: Any, day: Any, st: Any, trading: Any, acquired: Any, sha: str, index: int
) -> dict[str, Any]:
    _require(isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) is not None, "CODE_INVALID")
    _require(
        isinstance(st, str) and st in FLAGS and isinstance(trading, str) and trading in FLAGS,
        "STATUS_UNKNOWN",
    )
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is not None, "SOURCE_HASH_INVALID")
    known = _instant(acquired)
    business_day = _day(day)
    _require(
        date.fromisoformat(business_day) <= known.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        "CAPTURE_BEFORE_OBSERVATION",
    )
    return dict(
        stock_code=code,
        market_date=business_day,
        status_value="ST" if st == "1" else "NORMAL",
        reason="PROVIDER_TRADING" if trading == "1" else "PROVIDER_NOT_TRADING",
        acquired_at=acquired,
        source_sha256=sha,
        source_row=index,
    )


def raw_observations(
    raw: dict[str, Any], keys: set[tuple[str, str]], sha: str
) -> list[dict[str, Any]]:
    req = raw.get("request") or {}
    code, exchange = raw.get("stock_code"), raw.get("exchange")
    _require(exchange in {"SSE", "SZSE"}, "EXCHANGE_UNKNOWN")
    expected = ("sh." if exchange == "SSE" else "sz.") + str(code)
    _require(raw.get("bs_code") == req.get("code") == expected, "SOURCE_IDENTITY")
    _require(
        req.get("fields") == "date,code,isST,tradestatus"
        and req.get("frequency") == "d"
        and req.get("adjustflag") == "3",
        "REQUEST_SCHEMA",
    )
    _require(raw.get("error_code") == "0", "SOURCE_REQUEST_FAILED")
    acquired = req.get("fetched_at")
    _instant(acquired)  # Never substitute built_at, mtime or market-date + 1.
    rows = raw.get("rows")
    if not isinstance(rows, list):
        raise StatusEvidenceError("SOURCE_ROW_COUNT")
    _require(
        isinstance(rows, list) and type(raw.get("n_rows")) is int and raw["n_rows"] == len(rows),
        "SOURCE_ROW_COUNT",
    )
    start, end = _day(req.get("start_date")), _day(req.get("end_date"))
    window = raw.get("window") or {}
    _require(
        window.get("start") == start and window.get("end") == end and start <= end, "REQUEST_WINDOW"
    )
    seen: set[str] = set()
    result = []
    for index, row in enumerate(rows):
        _require(
            isinstance(row, dict) and set(row) == {"date", "code", "isST", "tradestatus"},
            "RAW_ROW_SCHEMA",
        )
        day = _day(row["date"])
        _require(row["code"] == expected and start <= day <= end, "ROW_IDENTITY_OR_WINDOW")
        _require(day not in seen, "DUPLICATE_SOURCE_DATE")
        seen.add(day)
        observation = _observation(code, day, row["isST"], row["tradestatus"], acquired, sha, index)
        if (code, day) in keys:
            result.append(observation)
    return result


def hotfix_observation(row: dict[str, Any], sha: str, index: int) -> dict[str, Any]:
    _require(row.get("source") == PROVIDER, "PROVIDER_UNKNOWN")
    _require(row.get("stock_code") == row.get("security_id"), "HOTFIX_IDENTITY")
    _require(_day(row.get("market_date")) == _day(row.get("date")), "HOTFIX_DATE_ALIAS")
    return _observation(
        row["stock_code"],
        row["market_date"],
        row.get("isST"),
        row.get("tradestatus"),
        row.get("acquired_at"),
        sha,
        index,
    )


def select_status(
    requests: list[dict[str, str]], observations: list[dict[str, Any]], cutoff: str
) -> list[dict[str, Any]]:
    instant = _instant(cutoff)
    keys = [(r["stock_code"], _day(r["market_date"])) for r in requests]
    _require(len(keys) == len(set(keys)), "DUPLICATE_REQUEST")
    result = []
    for code, day in sorted(keys):
        rows = [r for r in observations if (r["stock_code"], r["market_date"]) == (code, day)]
        visible = [r for r in rows if _instant(r["acquired_at"]) <= instant]
        latest = max((_instant(r["acquired_at"]) for r in visible), default=None)
        chosen = [r for r in visible if _instant(r["acquired_at"]) == latest]
        states = {(r["status_value"], r["reason"]) for r in chosen}
        qualified = len(states) == 1
        state, reason = next(iter(states)) if qualified else (None, None)
        result.append(
            dict(
                stock_code=code,
                market_date=day,
                qualified=qualified,
                status_value=state,
                reason=reason,
                knowledge_from=latest.isoformat() if latest else None,
                observations=len(rows),
                selected_evidence=sorted(chosen, key=_json),
                disposition="DATED_PROVIDER_OBSERVATION"
                if qualified
                else "EQUAL_TIME_CONFLICT"
                if chosen
                else "DATED_STATUS_UNAVAILABLE",
                execution_authorized=False,
                historical_pit_granted=False,
            )
        )
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write(path: Path, value: Any) -> None:
    path.write_text(_json(value) + "\n", encoding="utf-8")


def _evaluate(root: Path, plan: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    pq = importlib.import_module("pyarrow.parquet")

    _require(
        plan["schema_version"] == VERSION
        and plan["execution_authorized"] is False
        and plan["historical_pit_granted"] is False,
        "PURPOSE_CONTRACT",
    )
    inventory_path = root / "original-manifest.json"
    _require(file_sha(inventory_path) == plan["inventory_sha256"], "INVENTORY_HASH")
    inventory = json.loads(inventory_path.read_text())["content"]["files"]
    keys = {(r["stock_code"], _day(r["market_date"])) for r in plan["requests"]}
    observations = []
    identities: set[str] = set()
    for entry in plan["sources"]:
        member = entry["original_member"]
        _require(member in inventory, "SOURCE_MEMBERSHIP")
        sha = member["sha256"]
        _require(re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is not None, "SOURCE_HASH_INVALID")
        _require(sha not in identities, "DUPLICATE_SOURCE")
        identities.add(sha)
        kind = entry["format"]
        _require(kind in {"baostock-status-response", "dated-status-hotfix"}, "SOURCE_FORMAT")
        suffix = ".json" if kind == "baostock-status-response" else ".parquet"
        path = root / "sources" / (sha[7:] + suffix)
        _require(
            file_sha(path) == sha and path.stat().st_size == member["size_bytes"], "SOURCE_HASH"
        )
        if kind == "baostock-status-response":
            observations.extend(raw_observations(json.loads(path.read_text()), keys, sha))
        else:
            columns = [
                "stock_code",
                "security_id",
                "market_date",
                "date",
                "isST",
                "tradestatus",
                "acquired_at",
                "source",
            ]
            offset = 0
            for batch in pq.ParquetFile(path).iter_batches(batch_size=4096, columns=columns):
                for index, row in enumerate(batch.to_pylist(), offset):
                    if (row["stock_code"], _day(row["market_date"])) in keys:
                        observations.append(hotfix_observation(row, sha, index))
                offset += len(batch)
    observations.sort(key=_json)
    return observations, select_status(plan["requests"], observations, plan["knowledge_cutoff"])


def build(
    ods_root: Path,
    inventory_sha256: str,
    sources: list[dict[str, Any]],
    requests: list[dict[str, str]],
    cutoff: str,
    output: Path,
    code_revision: str,
) -> dict[str, Any]:
    ods_root = physical_path(ods_root)
    output = disjoint_output(output, [ods_root])
    _require(not output.exists(), "OUTPUT_EXISTS")
    inventory = ods_root / "manifest.json"
    _require(file_sha(inventory) == inventory_sha256, "INVENTORY_HASH")
    _require(re.fullmatch(r"[0-9a-f]{40}", code_revision) is not None, "CODE_REVISION")
    plan = dict(
        schema_version=VERSION,
        inventory_sha256=inventory_sha256,
        sources=sources,
        requests=requests,
        knowledge_cutoff=cutoff,
        code_revision=code_revision,
        execution_authorized=False,
        historical_pit_granted=False,
    )
    original_members = json.loads(inventory.read_text())["content"]["files"]
    _instant(cutoff)
    select_status(requests, [], cutoff)
    paths = [inventory]
    identities: set[str] = set()
    for entry in sources:
        member = entry["original_member"]
        _require(member in original_members, "SOURCE_MEMBERSHIP")
        _require(
            entry["format"] in {"baostock-status-response", "dated-status-hotfix"}, "SOURCE_FORMAT"
        )
        sha = member["sha256"]
        _require(re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is not None, "SOURCE_HASH_INVALID")
        _require(sha not in identities, "DUPLICATE_SOURCE")
        identities.add(sha)
        relative = Path(entry["original_member"]["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts, "SOURCE_PATH")
        paths.append(physical_path(ods_root / relative))
    before = {p: _fingerprint(p) for p in paths}
    output.mkdir()
    (output / "sources").mkdir()
    shutil.copyfile(inventory, output / "original-manifest.json")
    _write(output / "plan.json", plan)
    for entry, path in zip(sources, paths[1:], strict=True):
        suffix = ".json" if entry["format"] == "baostock-status-response" else ".parquet"
        target = output / "sources" / (entry["original_member"]["sha256"][7:] + suffix)
        _require(not target.exists(), "DUPLICATE_SOURCE")
        shutil.copyfile(path, target)
    observations, selected = _evaluate(output, plan)
    _write(output / "observations.json", observations)
    _write(output / "dated-status.json", selected)
    _require(all(_fingerprint(p) == before[p] for p in paths), "SOURCE_CHANGED")
    manifest = dict(
        schema_version=VERSION,
        built_at=datetime.now(timezone.utc).isoformat(),
        files={name: file_sha(output / name) for name in _tree(output)},
    )
    _write(output / "manifest.json", manifest)
    digest = file_sha(output / "manifest.json")
    verify(output, digest, require_ready=False)
    (output / "READY").write_text(digest + "\n", encoding="utf-8")
    return dict(
        manifest_sha256=digest,
        requests=len(requests),
        observations=len(observations),
        dated_status_qualified=sum(r["qualified"] for r in selected),
        execution_authorized=False,
    )


def verify(root: Path, manifest_sha256: str, *, require_ready: bool = True) -> dict[str, Any]:
    root = physical_path(root)
    _require(file_sha(root / "manifest.json") == manifest_sha256, "MANIFEST_HASH")
    manifest = json.loads((root / "manifest.json").read_text())
    _require(manifest["schema_version"] == VERSION, "MANIFEST_VERSION")
    files = manifest["files"]
    expected = set(files) | {"manifest.json"} | ({"READY"} if require_ready else set())
    _require(set(_tree(root)) == expected, "FILE_SET")
    for name, sha in files.items():
        _require(file_sha(root / name) == sha, "MEMBER_HASH")
    if require_ready:
        _require((root / "READY").read_text().strip() == manifest_sha256, "READY_HASH")
    plan = json.loads((root / "plan.json").read_text())
    semantic_files = {
        "original-manifest.json",
        "plan.json",
        "observations.json",
        "dated-status.json",
    }
    for entry in plan["sources"]:
        sha = entry["original_member"]["sha256"]
        _require(re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is not None, "SOURCE_HASH_INVALID")
        suffix = ".json" if entry["format"] == "baostock-status-response" else ".parquet"
        semantic_files.add("sources/" + sha[7:] + suffix)
    _require(set(files) == semantic_files, "SEMANTIC_FILE_SET")
    observations, selected = _evaluate(root, plan)
    _require(
        json.loads((root / "observations.json").read_text()) == observations
        and json.loads((root / "dated-status.json").read_text()) == selected,
        "RECOMPUTED_SEMANTICS",
    )
    return dict(
        manifest_sha256=manifest_sha256,
        requests=len(selected),
        dated_status_qualified=sum(r["qualified"] for r in selected),
        execution_authorized=False,
    )
