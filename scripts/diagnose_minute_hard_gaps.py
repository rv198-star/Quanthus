"""Owner-authorized, at-most-24-security capability test; never a publisher.

Default is offline. --probe-existing does one bounded re-observation of each
hard gap through the existing approved Tencent/HTTPS relay adapters. Only
transport/provider failure stops network work; quality rejection stays in the
denominator. All old evidence remains immutable and output must be new.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from quanthus.providers import minute_offline_ledger as ledger
from quanthus.providers.minute_quality_policy import load_policy
from quanthus.providers.minute_references import load_minute_references
from quanthus.providers.minute_tdx import _tencent_one_minute_bars
from quanthus.providers.paid_minute_source import _audit_symbol


def gap_inputs(root: Path, expected_records_sha: str):
    raw = ledger._capture(root / "records.jsonl", limit=40_000_000)
    if hashlib.sha256(raw).hexdigest() != expected_records_sha:
        raise ValueError("diagnostic ledger records SHA mismatch")
    summary = ledger._json_capture(ledger._capture(root / "summary.json", limit=100_000))
    job = ledger._json_capture(ledger._capture(root / "job.json", limit=200_000))
    identity = job["identity"]
    rows = [ledger._json_capture(line) for line in raw.splitlines()]
    if (
        summary.get("status") != "offline_evaluation_completed_not_published"
        or summary.get("records_sha256") != expected_records_sha
        or summary.get("identity_sha256")
        != hashlib.sha256(ledger._json_bytes(identity)).hexdigest()
        or summary.get("processed") != len(rows)
        or summary.get("universe_count") != len(rows)
        or len({r["stock_code"] for r in rows}) != len(rows)
        or any(r["market_date"] != identity["market_date"] for r in rows)
        or any(r.get("publication_authorized") is not False for r in rows)
        or identity.get("quality_policy", {}).get("id") != "existing-exact-1m-v1"
    ):
        raise ValueError("diagnostic ledger identity/denominator is not closed")
    gaps = [r for r in rows if r["disposition"] == "pending_no_qualified_source"]
    if not 1 <= len(gaps) <= 24 or any(r["tradestatus"] != "1" for r in gaps):
        raise ValueError("capability test requires one to 24 traded hard gaps")
    return identity, gaps


def probe_status_stops(receipt):
    return receipt.get("status") in {
        "source_pending", "rate_limited", "http_error", "transport_error",
    }


def run(root: Path, expected_records_sha: str, output: Path, *, probe=False, key=""):
    started = time.monotonic()
    identity, gaps = gap_inputs(root, expected_records_sha)
    roles = {r["role"]: r for r in identity["references"]["inputs"]}
    if set(roles) != {"sealed_daily", "sealed_status", "sealed_native_5m"}:
        raise ValueError("capability references are incomplete")
    refs = load_minute_references(
        daily_path=Path(roles["sealed_daily"]["path"]),
        status_path=Path(roles["sealed_status"]["path"]),
        five_path=Path(roles["sealed_native_5m"]["path"]),
        market_date=identity["market_date"], require_first_five=True,
        expected_sha256={role: r["sha256"] for role, r in roles.items()},
    )
    if refs.effective_view_sha256 != identity["references"]["effective_view_sha256"]:
        raise ValueError("capability reference view differs from diagnostic ledger")
    bounded = load_policy(Path(__file__).resolve().parents[1]
                          / "config/minute-quality/bounded-v1.json")
    inputs = [root, Path(identity["tencent_root"]), Path(identity["paid_root"]),
              *(Path(r["path"]) for r in roles.values())]
    target = output.resolve()
    if any(target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target)
           for p in inputs):
        raise ValueError("capability output overlaps immutable inputs")
    if probe and not key:
        raise ValueError("authenticated relay probe requires non-echoing stdin key")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "job.json").write_bytes(ledger._json_bytes({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_ledger": str(root), "source_records_sha256": expected_records_sha,
        "source_identity_sha256": hashlib.sha256(ledger._json_bytes(identity)).hexdigest(),
        "mode": "bounded_existing_source_probe" if probe else "offline_gap_diagnosis",
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "symbols": [r["stock_code"] for r in gaps], "references": refs.evidence(),
        "qualification_scope": "existing_strict_OHLCV_amount_debt_visible_not_published",
        "secondary_diagnostic_policy": bounded.evidence(),
        "publication_authorized": False, "timer_activation_authorized": False,
    }))
    checkpoint_raw = ledger._capture(
        Path(identity["tencent_root"]) / "checkpoint.json", limit=20_000_000,
    )
    if hashlib.sha256(checkpoint_raw).hexdigest() != identity["tencent_checkpoint_sha256"]:
        raise ValueError("source checkpoint changed since diagnostic ledger")
    checkpoint = ledger._json_capture(checkpoint_raw)["symbols"]
    old_rows = []
    for gap in gaps:
        code = gap["stock_code"]
        old_rows.append(ledger._evaluate(
            code, refs, Path(identity["tencent_root"]), Path(identity["paid_root"]),
            checkpoint.get(code),
        ))
    if any(r["disposition"] == "blocked_capture_integrity" for r in old_rows):
        raise ValueError("old raw integrity failure blocks capability probing")
    (output / "offline-records.jsonl").write_bytes(
        b"".join(ledger._json_bytes(r) for r in old_rows)
    )
    new_rows = []
    stop = "offline_only"
    if probe:
        stop = "completed_existing_source_probe"
        tencent_root, paid_root = output / "tencent", output / "paid"
        paid_root.mkdir()
        for gap in gaps:
            if time.monotonic() - started > 600:
                stop = "bounded_wall_budget_exhausted"
                break
            code = gap["stock_code"]
            fault = None
            try:
                _tencent_one_minute_bars(
                    code, refs.market_date, dict(refs[code]),
                    tencent_root / "provider_raw/tencent-m1",
                )
            except RuntimeError as exc:
                # Existing adapter reports both provider and quality errors.
                # Preserve the bounded failure; replay below classifies its raw.
                fault = str(exc)[:400]
            time.sleep(1)
            receipt = _audit_symbol(
                code, day=refs.market_date, key=key, daily=dict(refs[code]),
                first_5m_volume=refs[code]["first_5m_volume"], output_dir=paid_root,
                reference_view_sha256=refs.effective_view_sha256,
            )
            raw_path = tencent_root / "provider_raw/tencent-m1" / f"{code}.json.gz"
            witness = None
            if raw_path.exists():
                compressed = ledger._capture(raw_path, limit=ledger.MAX_COMPRESSED)
                with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
                    raw = source.read(ledger.MAX_RAW + 1)
                if len(raw) > ledger.MAX_RAW:
                    raise ValueError("new Tencent raw exceeds bounded size")
                witness = {"market_date": refs.market_date, "provider": ledger.PRIORITY[0],
                           "source_raw_path": str(raw_path),
                           "source_raw_sha256": hashlib.sha256(raw).hexdigest()}
            row = ledger._evaluate(code, refs, tencent_root, paid_root, witness)
            # Reuse captured bytes, never issue another request for the more
            # permissive numerical policy. It still refuses missing cells/rows.
            row["bounded_diagnostic"] = ledger._evaluate(
                code, refs, tencent_root, paid_root, witness, quality_policy=bounded,
            )
            row["tencent_acquisition_error"] = fault
            old = next(r for r in old_rows if r["stock_code"] == code)
            row["raw_sha_same_as_previous"] = {
                s["provider"]: (
                    s.get("raw_sha256") is not None
                    and s.get("raw_sha256") == p.get("raw_sha256")
                )
                for s, p in zip(row["sources"], old["sources"], strict=True)
            }
            new_rows.append(row)
            (output / "probe-records.jsonl").write_bytes(
                b"".join(ledger._json_bytes(r) for r in new_rows)
            )
            print(json.dumps({"processed": len(new_rows), "total": len(gaps),
                              "stock_code": code, "disposition": row["disposition"]}), flush=True)
            if probe_status_stops(receipt):
                stop = "relay_acquisition_stop_" + receipt["status"]
                break
            tencent = row["sources"][0]
            if witness is None or "PROVIDER_RESPONSE_FAULT" in tencent.get("reason_codes", []):
                stop = "tencent_acquisition_fault"
                break
            time.sleep(1)
    counts = Counter(r["disposition"] for r in new_rows)
    result = {
        "status": "capability_evaluated_not_published", "market_date": refs.market_date,
        "hard_gap_count": len(gaps), "offline_evaluated": len(old_rows),
        "probe_processed": len(new_rows), "probe_dispositions": dict(counts),
        "bounded_diagnostic_dispositions": dict(Counter(
            r["bounded_diagnostic"]["disposition"] for r in new_rows
        )),
        "stop_reason": stop, "all_hard_gaps_qualified": (
            probe and len(new_rows) == len(gaps)
            and all(r["disposition"] == "qualified_whole_security_candidate" for r in new_rows)
        ),
        "publication_authorized": False, "evaluation_seconds": round(time.monotonic()-started, 3),
        "record_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in output.glob("*-records.jsonl")},
    }
    (output / "summary.json").write_bytes(ledger._json_bytes(result))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--expected-records-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--probe-existing", action="store_true")
    args = p.parse_args()
    key = sys.stdin.readline().strip() if args.probe_existing else ""
    try:
        print(json.dumps(run(args.ledger, args.expected_records_sha256, args.output,
                             probe=args.probe_existing, key=key), sort_keys=True))
    finally:
        key = ""


if __name__ == "__main__":
    main()
