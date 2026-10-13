"""Target-day supplier universe, with original SDK strings and lifecycle evidence."""

from __future__ import annotations

import contextlib
import fcntl
import io
import os
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, file_digest, object_at
from quanthus.providers.acquisition_evidence import POLICY, capture, clock, verify_capture
from quanthus.providers.baostock_transport import set_socket_timeout


def in_scope(code: str) -> bool:
    return (
        len(code) == 9
        and code[:3] in {"sh.", "sz."}
        and code[3:].isdigit()
        and (
            (code.startswith("sh.") and code[3:].startswith(("60", "68")))
            or (code.startswith("sz.") and code[3:].startswith(("00", "30")))
        )
    )


def validate(
    day: date, daily: list[dict[str, str]], basic: list[dict[str, str]]
) -> list[dict[str, Any]]:
    members = [row for row in daily if in_scope(row["code"])]
    identities = {row["code"]: row for row in basic if in_scope(row["code"])}
    codes = [row["code"] for row in members]
    if (
        not codes
        or len(codes) != len(set(codes))
        or len(identities) != sum(in_scope(row["code"]) for row in basic)
    ):
        raise ValueError("UNIVERSE_EMPTY_OR_DUPLICATE")
    rows = []
    for item in sorted(members, key=lambda row: row["code"]):
        stock = identities.get(item["code"])
        if stock is None or stock.get("type") != "1" or item.get("tradeStatus") not in {"0", "1"}:
            raise ValueError("UNIVERSE_AUTHORITY_MISSING")
        ipo = date.fromisoformat(stock["ipoDate"])
        out = date.fromisoformat(stock["outDate"]) if stock.get("outDate") else None
        if ipo > day or (out and out <= day):
            raise ValueError("UNIVERSE_LISTING_DATE_CONFLICT")
        rows.append(
            {
                "code": item["code"][3:],
                "stock_code": item["code"][3:],
                "security_id": item["code"][3:],
                "exchange": "SSE" if item["code"].startswith("sh.") else "SZSE",
                "name": item["code_name"],
                "list_date": ipo.isoformat(),
                "delist_date": out.isoformat() if out else None,
                "tradestatus": item["tradeStatus"],
                "target_date": day.isoformat(),
                "source": "baostock.query_all_stock+query_stock_basic",
                "basic_snapshot_semantics": "observed_current_basic_with_target_day_listing_check",
            }
        )
    return rows


def verify(root: Path, day: date) -> dict[str, Any]:
    import pyarrow.parquet as pq

    value = object_at(root / "universe.json")
    if value.get("policy_id") != POLICY or value.get("market_date") != day.isoformat():
        raise ValueError("UNIVERSE_IDENTITY_DIFFERS")
    for name, sha in value["files"].items():
        if Path(name).name != name or file_digest(root / name) != sha:
            raise ValueError("UNIVERSE_PHYSICAL_CLOSURE_DIFFERS")
        if name.endswith(".json.gz"):
            verify_capture(root / name)
    rows = pq.read_table(root / "stock_list.parquet").to_pylist()
    codes = sorted(row["stock_code"] for row in rows)
    if (
        len(set(codes)) != len(codes)
        or codes != value["codes"]
        or digest(encoded(codes)) != value["universe_digest"]
    ):
        raise ValueError("UNIVERSE_KEYS_DIFFERS")
    return value


def refresh(*, home: Path, day: date) -> tuple[Path, dict[str, Any]]:
    """Two bounded metadata calls, once per target day; no price acquisition."""
    import baostock as bs
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = home / "source-universe-v1" / day.isoformat()
    root.parent.mkdir(parents=True, exist_ok=True)
    with (root.parent / ".refresh.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if (root / "universe.json").exists():
            return root, verify(root, day)
        # An interrupted, unsealed attempt is retained and retried in a distinct directory.
        if root.exists():
            os.rename(root, root.with_name(root.name + ".unsealed-" + clock().replace(":", "")))
        attempt = root.parent / ("attempt-" + day.isoformat() + "-" + clock().replace(":", ""))
        attempt.mkdir()
        set_socket_timeout(30)
        with contextlib.redirect_stdout(io.StringIO()):
            login = bs.login()
        if str(login.error_code) != "0":
            raise ValueError("UNIVERSE_PROVIDER_LOGIN_FAILED")
        responses = []
        try:
            for api, args in [
                ("query_all_stock", {"day": day.isoformat()}),
                ("query_stock_basic", {}),
            ]:
                started = clock()
                result = getattr(bs, api)(**args)
                rows = []
                while str(result.error_code) == "0" and result.next():
                    rows.append(result.get_row_data())
                completed = clock()
                raw = encoded(
                    {
                        "representation": "lossless_sdk_strings",
                        "fields": result.fields,
                        "rows": rows,
                        "error_code": result.error_code,
                        "error_msg": result.error_msg,
                    }
                )
                capture(
                    root=attempt,
                    request={"provider": "baostock", "api": api, **args},
                    raw=raw,
                    started_at=started,
                    completed_at=completed,
                    status=str(result.error_code),
                )
                if str(result.error_code) != "0":
                    raise ValueError("UNIVERSE_PROVIDER_QUERY_FAILED")
                responses.append([dict(zip(result.fields, row, strict=True)) for row in rows])
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                bs.logout()
        rows = validate(day, *responses)
        import requests

        started = clock()
        url = "http://www.cninfo.com.cn/new/data/szse_stock.json"
        response = requests.get(url, timeout=30)
        capture(
            root=attempt,
            request={"provider": "cninfo", "url": url},
            raw=response.content,
            started_at=started,
            completed_at=clock(),
            status=str(response.status_code),
        )
        response.raise_for_status()
        body = response.json()
        org_map = {}
        for values in body.values():
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and item.get("code") and item.get("orgId"):
                        org_map[str(item["code"])] = str(item["orgId"])
        if not org_map:
            raise ValueError("CNINFO_COMPANY_IDENTITY_MAP_MISSING")
        atomic(attempt / "cninfo_orgid_map.json", org_map)
        observed = clock()
        for row in rows:
            row["warehouse_observed_at"] = observed
        for name in ("stock_list.parquet", "security_master.parquet"):
            pq.write_table(pa.Table.from_pylist(rows), attempt / name)
        # Publish all physical members before the immutable descriptor.
        members = {}
        for path in sorted(attempt.iterdir()):
            members[path.name] = file_digest(path)
        codes = sorted(row["stock_code"] for row in rows)
        atomic(
            attempt / "universe.json",
            {
                "policy_id": POLICY,
                "market_date": day.isoformat(),
                "provider": "baostock",
                "scope": "SSE_SZSE_A_SHARE_00_30_60_68",
                "codes": codes,
                "company_org_ids": {code: org_map[code] for code in codes if code in org_map},
                "population": len(codes),
                "universe_digest": digest(encoded(codes)),
                "warehouse_observed_at": observed,
                "historical_pit_authorized": False,
                "files": members,
            },
        )
        verify(attempt, day)
        os.rename(attempt, root)
        return root, verify(root, day)
