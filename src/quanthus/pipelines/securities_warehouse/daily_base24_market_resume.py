"""Resume one frozen daily acquisition, retaining original SDK bytes and clocks.

This is an explicit source adapter, not acceptance of the interrupted package.
The published collector still validates and seals the complete merged population.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

FIELDS = (
    "date,code,open,high,low,close,volume,amount,turn,pctChg,tradestatus,isST,"
    "peTTM,pbMRQ,psTTM,pcfNcfTTM"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def physical(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError("DAILY_SEED_SYMLINK")
    return path


def stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("DAILY_SEED_NAIVE_CLOCK")
    return parsed


def load_seed(inventory: Path, digest: str, day: date) -> dict[str, Any]:
    """Verify frozen members before any output is created; never trust old READY."""
    from quanthus.providers.acquisition_evidence import (
        verify_capture,  # type: ignore[import-untyped]
    )

    inventory = physical(inventory)
    data = inventory.read_bytes()
    if sha(data) != digest:
        raise ValueError("DAILY_SEED_INVENTORY_CHANGED")
    value = json.loads(data)
    if value["kind"] != "cn02_daily_market_seed_v1" or value["market_date"] != str(day):
        raise ValueError("DAILY_SEED_SCOPE_DIFFERS")
    root = physical(Path(value["root"]))
    members = value["files"]
    if (
        not {"acquisition_log.json", "source_universe.json"} <= members.keys()
        or len(members) > 15000
        or sum(v["bytes"] for v in members.values()) > 128 * 1024**2
    ):
        raise ValueError("DAILY_SEED_MEMBER_BUDGET")
    for relative, expected in members.items():
        rel = Path(relative)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or str(rel) != relative
            or (
                relative not in {"acquisition_log.json", "source_universe.json"}
                and (rel.parent != Path("original-responses") or not rel.name.endswith(".json.gz"))
            )
        ):
            raise ValueError("DAILY_SEED_MEMBER_PATH")
        p = physical(root / rel)
        raw = p.read_bytes()
        if len(raw) != expected["bytes"] or sha(raw) != expected["sha256"]:
            raise ValueError("DAILY_SEED_MEMBER_CHANGED")
    actual = {"original-responses/" + p.name for p in (root / "original-responses").iterdir()}
    declared = {s for s in members if s.startswith("original-responses/")}
    if actual != declared:
        raise ValueError("DAILY_SEED_PHYSICAL_SET_DIFFERS")
    captures = {}
    for name in sorted(declared):
        p = root / name
        envelope = verify_capture(p)
        if p.name != sha(gzip.decompress(p.read_bytes())) + ".json.gz":
            raise ValueError("DAILY_SEED_CAPTURE_NAME_DIFFERS")
        captures[p.name] = envelope
    log = json.loads((root / "acquisition_log.json").read_bytes())
    universe = json.loads((root / "source_universe.json").read_bytes())
    if log["market_date"] != str(day) or universe["market_date"] != str(day):
        raise ValueError("DAILY_SEED_DATE_DIFFERS")
    return {
        **value,
        "inventory_sha256": digest,
        "log": log,
        "universe": universe,
        "captures": captures,
    }


def rows_for(log: dict[str, Any], envelope: dict[str, Any], day: date) -> tuple[Any, Any]:
    import base64

    from quanthus.providers.acquisition_evidence import decimal_token, suspended_status

    request = log["request"]
    wanted = {
        "provider": "baostock",
        "api": "query_history_k_data_plus",
        "start": str(day),
        "end": str(day),
        "frequency": "d",
        "adjustflag": "3",
        "fields": FIELDS,
    }
    if any(request.get(k) != v for k, v in wanted.items()):
        raise ValueError("DAILY_SEED_REQUEST_SCOPE")
    if envelope["request"] != {**request, "representation": "lossless_sdk_strings"}:
        raise ValueError("DAILY_SEED_REQUEST_CLOSURE")
    observed = log["acquired_at"]
    if not (
        # The pinned collector records acquired_at with timespec="seconds".
        # Preserve that value; compare its declared precision, never fabricate
        # subsecond knowledge time from the more precise response envelope.
        stamp(envelope["request_started_at"]).replace(microsecond=0)
        <= stamp(observed)
        <= stamp(envelope["response_completed_at"])
        <= stamp(envelope["warehouse_observed_at"])
    ):
        raise ValueError("DAILY_SEED_CLOCK_ORDER")
    value = json.loads(base64.b64decode(envelope["raw_base64"], validate=True))
    if (
        value["fields"] != FIELDS.split(",")
        or value["error_code"] != "0"
        or envelope["status"] != "0"
    ):
        raise ValueError("DAILY_SEED_RESPONSE_SCOPE")
    if len(value["rows"]) != 1 or log["response_rows"] != 1:
        raise ValueError("DAILY_SEED_NONUNIQUE_RESPONSE")
    row = value["rows"][0]
    if len(row) != 16 or any(not isinstance(s, str) for s in row):
        raise ValueError("DAILY_SEED_TOKEN_SCHEMA")
    code = log["security_id"]
    # Match the separately pinned collector's exact provider identifier rule.
    exchange = "sh" if code.startswith(("5", "6", "9")) else "sz"
    if request["code"] != exchange + "." + code:
        raise ValueError("DAILY_SEED_SECURITY_IDENTITY")
    suspension = suspended_status(
        row, code=code, provider_code=request["code"], day=str(day), observed_at=observed
    )
    if suspension is not None:
        return None, suspension
    for token in row[2:8]:
        decimal_token(token, required=True)
    common = dict(
        security_id=code,
        stock_code=code,
        market_date=str(day),
        date=str(day),
        acquired_at=observed,
        built_at=observed,
    )
    bar = {
        **common,
        **{
            k: decimal_token(row[i])
            for i, k in enumerate(
                ["open", "high", "low", "close", "volume", "amount", "turnover_rate", "pct_change"],
                2,
            )
        },
        "adj_mode": "none",
        "adj_version": "baostock.adjustflag=3.v1",
        "source": "baostock.query_history_k_data_plus.adjustflag=3",
        **{
            k: decimal_token(row[i])
            for i, k in enumerate(
                ["_valuation_pe_ttm", "_valuation_pb", "_valuation_ps_ttm", "_valuation_pcf_ttm"],
                12,
            )
        },
    }
    status = {
        **common,
        "tradestatus": row[10],
        "isST": row[11],
        "source": "baostock.query_history_k_data_plus",
    }
    return bar, status


def adapter(
    original: Callable[..., Any], seed: dict[str, Any], producer: str
) -> Callable[..., Any]:
    def fetch(codes: list[str], market_date: date, **kwargs: Any) -> Any:
        if list(codes) != seed["universe"]["codes"] or str(market_date) != seed["market_date"]:
            raise ValueError("DAILY_SEED_UNIVERSE_DIFFERS")
        target = physical(Path(os.environ["HERMES_ODS_RAW_CAPTURE_ROOT"]))
        root = physical(Path(seed["root"]))
        if target == root or target in root.parents or root in target.parents:
            raise ValueError("DAILY_SEED_OUTPUT_OVERLAP")
        bars, states, logs = {}, {}, []
        for log in seed["log"]["requests"]:
            if log.get("response_error_code") != "0" or "security_id" not in log:
                continue
            receipt = log["raw_receipt"]
            envelope = seed["captures"][receipt["capture_file"]]
            expected = {k: v for k, v in envelope.items() if k != "raw_base64"} | {
                "capture_file": receipt["capture_file"],
                "capture_sha256": seed["files"]["original-responses/" + receipt["capture_file"]][
                    "sha256"
                ],
            }
            if receipt != expected:
                raise ValueError("DAILY_SEED_RECEIPT_DIFFERS")
            bar, status = rows_for(log, envelope, market_date)
            code = log["security_id"]
            if code not in codes or code in states:
                raise ValueError("DAILY_SEED_DUPLICATE_OR_UNKNOWN_SECURITY")
            if bar is not None:
                bars[code] = bar
            states[code] = status
            logs.append(
                {
                    **log,
                    "response_reused": True,
                    "seed_inventory_sha256": seed["inventory_sha256"],
                    "resume_adapter_revision": producer,
                }
            )
        # All immutable evidence is copied as independent files into the new source.
        target.mkdir(parents=True, exist_ok=True)
        for relative, expected in seed["files"].items():
            p = root / relative
            raw = p.read_bytes()
            if sha(raw) != expected["sha256"] or len(raw) != expected["bytes"]:
                raise ValueError("DAILY_SEED_CHANGED_BEFORE_COPY")
            if relative.startswith("original-responses/"):
                with (target / p.name).open("xb") as out:
                    out.write(raw)
                if (target / p.name).stat().st_ino == p.stat().st_ino:
                    raise ValueError("DAILY_SEED_PHYSICAL_COPY_REQUIRED")
        # Keep the frozen selection, clocks and original log with the new package.
        # The collector archives all gzip envelopes, so capture the adapter contract
        # itself under the same original-response policy, with its actual new clock.
        from quanthus.providers.acquisition_evidence import capture, clock

        capture(
            request={
                "provider": "quanthus",
                "api": "daily_market_resume",
                "market_date": str(market_date),
                "collector_revision": seed["collector_revision"],
                "adapter_revision": producer,
            },
            raw=json.dumps(
                {k: v for k, v in seed.items() if k != "captures"}, sort_keys=True
            ).encode(),
            started_at=clock(),
            completed_at=clock(),
            status="retained_source_evidence",
            root=target,
        )
        missing = [code for code in codes if code not in states]
        if missing:
            new_bars, new_states, new_logs = original(missing, market_date, **kwargs)
            bars.update({r["security_id"]: r for r in new_bars})
            states.update({r["security_id"]: r for r in new_states})
            logs.extend(new_logs)
        logs.append(
            {
                "event": "frozen_market_resume",
                "seeded_securities": len(codes) - len(missing),
                "requested_missing_securities": len(missing),
                "adapter_revision": producer,
                "collector_revision": seed["collector_revision"],
                "seed_inventory_sha256": seed["inventory_sha256"],
            }
        )
        return (
            [bars[c] for c in codes if c in bars],
            [states[c] for c in codes if c in states],
            logs,
        )

    return fetch
