"""Bounded current-qualification diagnostics over immutable 1m captures.

No fetching, secrets, candidate materialization, publication or recovery reuse.
The existing adapters and exact whole-security selector own market semantics.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.providers import (
    minute_references,
    minute_source_selection,
    paid_minute_source,
    tencent_m1_normalize,
)
from quanthus.providers.minute_references import MinuteReferences, load_minute_references
from quanthus.providers.minute_source_selection import (
    MinuteCandidate,
    choose_whole_security,
    qualify_candidate,
)

SCHEMA = "quanthus.minute-offline-diagnostic.v1"
PRIORITY = ("tencent-mkline-validated", "paid-tushare-style-proxy-stk_mins")
POLICY = "tencent-then-paid-audited-raw-v1-with-no-inferred-minutes"
MAX_RAW = 16_000_000
MAX_COMPRESSED = 2_000_000


class EvidenceFault(ValueError):
    """Invalid capture identity cannot be hidden by a qualified peer."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceFault("duplicate JSON object key in capture witness")
        result[key] = value
    return result


def _json_capture(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceFault("invalid JSON capture witness") from exc
    if not isinstance(value, dict):
        raise EvidenceFault("capture witness must be an object")
    return value


def _capture(path: Path, *, limit: int) -> bytes:
    """Hash and interpretation consume this one regular-file byte capture."""
    if not stat.S_ISREG(path.lstat().st_mode):
        raise EvidenceFault("capture must be a regular non-symlink file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise EvidenceFault("opened capture is not regular")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise EvidenceFault("capture exceeds bounded byte limit")
    return raw


def _raw_capture(path: Path, expected: Any, report: dict[str, Any]) -> bytes:
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(c not in "0123456789abcdef" for c in expected)
    ):
        raise EvidenceFault("capture lacks a valid expected raw SHA-256")
    compressed = _capture(path, limit=MAX_COMPRESSED)
    try:
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
            raw = source.read(MAX_RAW + 1)
    except (OSError, EOFError) as exc:
        raise EvidenceFault("invalid gzip raw capture") from exc
    digest = hashlib.sha256(raw).hexdigest()
    report.update(
        {
            "raw_ref": str(path),
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            "compressed_bytes": len(compressed),
        }
    )
    if len(raw) > MAX_RAW:
        report.update(capture_truncated=True, decompressed_prefix_sha256=digest)
        raise EvidenceFault("raw capture decompressed size exceeds limit")
    report.update(raw_sha256=digest, raw_bytes=len(raw))
    if digest != expected:
        raise EvidenceFault("raw capture SHA-256 mismatch")
    return raw


def _quality_reasons(message: str) -> list[str]:
    """Reason tags explain existing rejection; never decide qualification."""
    rules = (
        ("volume mismatch", "SEALED_SHARE_VOLUME_MISMATCH"),
        ("close mismatch", "SEALED_CLOSE_MISMATCH"),
        ("incomplete material", "INCOMPLETE_MATERIAL_MINUTE"),
        ("missing", "MINUTE_GRID_OR_EVIDENCE_MISSING"),
        ("session boundary", "INCOMPLETE_SESSION_BOUNDARY"),
        ("timestamp", "SOURCE_TIMESTAMP_REJECTED"),
        ("symbol/frequency/date", "SOURCE_IDENTITY_REJECTED"),
        ("ambiguous", "AMBIGUOUS_SOURCE_INTERPRETATION"),
        ("OHLC", "INVALID_OHLCV"),
        ("response code", "PROVIDER_RESPONSE_FAULT"),
        ("not successful", "PROVIDER_RESPONSE_FAULT"),
    )
    return sorted({reason for match, reason in rules if match in message}) or [
        "CURRENT_ADAPTER_OR_EXACT_GATE_REJECTED"
    ]


def _tencent_candidate(
    root: Path,
    witness: Mapping[str, Any] | None,
    code: str,
    refs: MinuteReferences,
    report: dict[str, Any],
) -> tuple[MinuteCandidate | None, dict[str, Any]]:
    report["status"] = "capture_missing"
    if witness is None:
        return None, report
    raw_path = root / "provider_raw" / "tencent-m1" / f"{code}.json.gz"
    if (
        witness.get("market_date") != refs.market_date
        or witness.get("provider") != PRIORITY[0]
        or witness.get("source_raw_path") != str(raw_path)
    ):
        raise EvidenceFault("Tencent capture witness date/provider/path mismatch")
    report["witness_expected_raw_sha256"] = witness.get("source_raw_sha256")
    raw = _raw_capture(raw_path, witness.get("source_raw_sha256"), report)
    reference = refs[code]
    detail: dict[str, Any]
    if reference["tradestatus"] == "0":
        payload = json.loads(raw)
        symbol = ("sh" if code.startswith("6") else "sz") + code
        if payload.get("code") != 0:
            raise ValueError("Tencent response code is nonzero")
        source = payload["data"][symbol]["m1"]
        if any(str(item[0]).startswith(refs.market_date.replace("-", "")) for item in source):
            raise ValueError("suspended security has source minutes")
        rows: list[dict[str, Any]] = []
        detail = {"source_rows": 0, "inferred_zero_volume_minutes": []}
    else:
        rows, detail = tencent_m1_normalize.normalize_symbol(
            code,
            raw,
            market_date=refs.market_date,
            daily_volume=reference["volume"],
            daily_close=reference["close"],
        )
    report["interpretation"] = detail
    # P0's approved no-inference boundary, not a new normalizer or price rule.
    if detail.get("inferred_zero_volume_minutes"):
        report.update(
            status="unqualified_candidate",
            reason_codes=["INFERRED_SOURCE_MINUTES_FORBIDDEN"],
        )
        return None, report
    candidate = MinuteCandidate(
        provider=PRIORITY[0],
        raw_sha256=report["raw_sha256"],
        raw_ref=str(raw_path),
        rows=tuple(rows),
        auction_provenance="tencent-m1-normalize-v1",
        amount_status="source_absent_null_not_estimated",
    )
    return candidate, report


def _paid_candidate(
    root: Path,
    code: str,
    refs: MinuteReferences,
    report: dict[str, Any],
) -> tuple[MinuteCandidate | None, dict[str, Any]]:
    report["status"] = "capture_missing"
    if refs[code]["tradestatus"] == "0":
        report["status"] = "not_requested_suspended"
        return None, report
    path = root / "receipts" / f"{code}.json"
    if not path.exists() and not path.is_symlink():
        return None, report
    receipt_raw = _capture(path, limit=1_000_000)
    receipt = _json_capture(receipt_raw)
    report.update(receipt_ref=str(path), receipt_sha256=hashlib.sha256(receipt_raw).hexdigest())
    if receipt.get("stock_code") != code or receipt.get("market_date") != refs.market_date:
        raise EvidenceFault("paid capture witness code/date mismatch")
    http = receipt.get("http_status")
    status = receipt.get("status")
    if http != 200:
        expected = {202: "source_pending", 429: "rate_limited"}.get(http, "http_error")
        if status not in {expected, "transport_error"}:
            raise EvidenceFault("paid capture witness HTTP/status mismatch")
        report.update(
            status={202: "acquisition_pending", 429: "acquisition_rate_limited"}.get(
                http, "acquisition_fault"
            ),
            http_status=http,
        )
        return None, report
    if status not in {"qualified_candidate_not_published", "unqualified"}:
        raise EvidenceFault("paid HTTP 200 witness status is invalid")
    raw_path = root / "raw" / f"{code}.json.gz"
    report.update(http_status=200, witness_expected_raw_sha256=receipt.get("source_raw_sha256"))
    raw = _raw_capture(raw_path, receipt.get("source_raw_sha256"), report)
    daily = refs[code]
    if daily["first_5m_volume"] is None:
        raise EvidenceFault("paid replay lacks captured same-date native-5m reference")
    candidate = paid_minute_source.normalized_candidate(
        raw,
        symbol=code + (".SH" if code.startswith("6") else ".SZ"),
        market_date=refs.market_date,
        daily_volume=daily["volume"],
        daily_close=daily["close"],
        first_5m_volume=daily["first_5m_volume"],
        raw_ref=str(raw_path),
    )
    return candidate, report


def _evaluate(
    code: str,
    refs: MinuteReferences,
    tencent_root: Path,
    paid_root: Path,
    witness: Mapping[str, Any] | None,
) -> dict[str, Any]:
    candidates: list[MinuteCandidate] = []
    sources: list[dict[str, Any]] = []
    daily = refs[code]
    for provider in PRIORITY:
        # Pass the same report through capture and interpretation so a rejected
        # candidate still retains its actual raw/receipt identities.
        report: dict[str, Any] = {"provider": provider}
        try:
            candidate, report = (
                _tencent_candidate(tencent_root, witness, code, refs, report)
                if provider == PRIORITY[0]
                else _paid_candidate(paid_root, code, refs, report)
            )
            if candidate is not None:
                report.update(
                    normalized_rows=len(candidate.rows),
                    auction_provenance=candidate.auction_provenance,
                    amount_status=candidate.amount_status,
                )
                digest = qualify_candidate(
                    candidate,
                    symbol=code,
                    market_date=refs.market_date,
                    daily_volume=daily["volume"],
                    daily_close=daily["close"],
                    traded=daily["tradestatus"] == "1",
                )
                report.update(
                    status="qualified_candidate_not_published",
                    semantic_sha256=digest,
                    normalized_rows=len(candidate.rows),
                    auction_provenance=candidate.auction_provenance,
                    amount_status=candidate.amount_status,
                )
                candidates.append(candidate)
        except (EvidenceFault, OSError) as exc:
            report.update(
                status="integrity_fault",
                reason_codes=["CAPTURE_INTEGRITY_FAULT"],
                error=str(exc)[:500],
            )
        except (
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            ArithmeticError,
            AttributeError,
        ) as exc:
            report.update(
                status="unqualified_candidate",
                reason_codes=_quality_reasons(str(exc)),
                error=str(exc)[:500],
            )
        sources.append(report)
    decision = choose_whole_security(
        candidates,
        symbol=code,
        market_date=refs.market_date,
        daily_volume=daily["volume"],
        daily_close=daily["close"],
        traded=daily["tradestatus"] == "1",
        priority=PRIORITY,
    )
    integrity = any(source["status"] == "integrity_fault" for source in sources)
    disposition = "blocked_capture_integrity" if integrity else decision.status
    return {
        "stock_code": code,
        "market_date": refs.market_date,
        "tradestatus": daily["tradestatus"],
        "sources": sources,
        "disposition": disposition,
        "selected_provider": None if integrity else decision.selected_provider,
        "selected_semantic_sha256": None if integrity else decision.normalized_sha256,
        "qualified_providers": list(decision.qualified),
        "publication_authorized": False,
        "recovery_authorized": False,
    }


def _implementation_identity() -> dict[str, Any]:
    modules = (minute_references, minute_source_selection, paid_minute_source, tencent_m1_normalize)
    paths = [Path(__file__), *(Path(module.__file__) for module in modules)]
    return {
        "schema": SCHEMA,
        "policy": POLICY,
        "source_priority": list(PRIORITY),
        "code_files": {
            path.name: hashlib.sha256(_capture(path, limit=1_000_000)).hexdigest() for path in paths
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("pandas", "pyarrow")},
    }


def build_ledger(
    *,
    refs: MinuteReferences,
    tencent_root: Path,
    paid_root: Path,
    output: Path,
    node_label: str,
    budget_seconds: float = 900,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a fresh full-denominator diagnostic; partial output is not complete."""
    if not node_label or not 0 < budget_seconds <= 1800:
        raise ValueError("explicit node label and finite 0..1800 second budget required")
    started = time.monotonic()
    roots = (tencent_root, paid_root)
    for root in roots:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise EvidenceFault("input root must be an existing non-symlink directory")
    for directory in (
        tencent_root / "provider_raw",
        tencent_root / "provider_raw/tencent-m1",
        paid_root / "raw",
        paid_root / "receipts",
    ):
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise EvidenceFault("capture directory must be non-symlink")
    target = output.resolve()
    inputs = [*roots, *(Path(item.path) for item in refs.inputs)]
    if any(
        target.is_relative_to(path.resolve()) or path.resolve().is_relative_to(target)
        for path in inputs
    ):
        raise EvidenceFault("diagnostic output overlaps immutable inputs")
    checkpoint_path = tencent_root / "checkpoint.json"
    checkpoint_raw = _capture(checkpoint_path, limit=20_000_000)
    checkpoint = _json_capture(checkpoint_raw).get("symbols")
    if not isinstance(checkpoint, dict) or not set(checkpoint).issubset(refs):
        raise EvidenceFault("Tencent witness universe is not a subset of sealed references")
    if any(not isinstance(value, dict) for value in checkpoint.values()):
        raise EvidenceFault("Tencent per-security capture witness is not an object")
    refs.select(list(refs), require_first_five=True)
    identity = {
        **_implementation_identity(),
        "node_label": node_label,
        "market_date": refs.market_date,
        "frequency": "1m",
        "references": {k: v for k, v in refs.evidence().items() if k != "load_seconds"},
        "provenance": dict(provenance)
        if provenance is not None
        else {"kind": "programmatic_diagnostic_without_release_handoff_not_acceptance"},
        "tencent_root": str(tencent_root),
        "paid_root": str(paid_root),
        "tencent_checkpoint_sha256": hashlib.sha256(checkpoint_raw).hexdigest(),
        "universe_sha256": hashlib.sha256("\n".join(refs).encode()).hexdigest(),
        "lifecycle": "job-local-TMP-diagnostic-not-cache-or-recovery-authority",
    }
    output.mkdir(parents=True, exist_ok=False)
    job = {
        "identity": identity,
        "status": "started_not_complete",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "budget_seconds": budget_seconds,
    }
    (output / "job.json").write_bytes(_json_bytes(job))
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    source_counts = {provider: Counter() for provider in PRIORITY}
    reasons = {provider: Counter() for provider in PRIORITY}
    processed = 0
    with (output / "records.jsonl").open("xb") as sink:
        for code in refs:
            if time.monotonic() - started >= budget_seconds:
                raise TimeoutError("offline diagnostic budget exhausted; no complete summary")
            row = _evaluate(code, refs, tencent_root, paid_root, checkpoint.get(code))
            raw = _json_bytes(row)
            sink.write(raw)
            digest.update(raw)
            counts[row["disposition"]] += 1
            for source in row["sources"]:
                source_counts[source["provider"]][source["status"]] += 1
                reasons[source["provider"]].update(source.get("reason_codes", []))
            processed += 1
    if time.monotonic() - started >= budget_seconds:
        raise TimeoutError("offline diagnostic budget exhausted; no complete summary")
    summary = {
        "schema": SCHEMA,
        "status": "offline_evaluation_completed_not_published",
        "identity_sha256": hashlib.sha256(_json_bytes(identity)).hexdigest(),
        "records_sha256": digest.hexdigest(),
        "universe_count": len(refs),
        "processed": processed,
        "dispositions": dict(sorted(counts.items())),
        "source_counts": {key: dict(sorted(value.items())) for key, value in source_counts.items()},
        "reason_counts": {key: dict(sorted(value.items())) for key, value in reasons.items()},
        "reference_load_seconds": refs.load_seconds,
        "evaluation_seconds": round(time.monotonic() - started, 6),
        "publication_authorized": False,
        "recovery_authorized": False,
    }
    with (output / "summary.json").open("xb") as sink:
        sink.write(_json_bytes(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--shadow-root", type=Path, required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--tencent-root", type=Path, required=True)
    parser.add_argument("--paid-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node-label", required=True)
    parser.add_argument("--budget-seconds", type=float, default=900)
    args = parser.parse_args()
    from datetime import date

    from quanthus.pipelines.lane_runner import _sealed_minute_reference_artifacts, resolve_paths

    paths = resolve_paths(home=args.home, shadow_root=args.shadow_root)
    artifacts = _sealed_minute_reference_artifacts(
        paths=paths,
        market_date=date.fromisoformat(args.market_date),
        available_at=datetime.now(timezone.utc),
        require_five=True,
    )
    refs = load_minute_references(
        daily_path=artifacts["sealed_daily"][0],
        status_path=artifacts["sealed_status"][0],
        five_path=artifacts["sealed_native_5m"][0],
        market_date=args.market_date,
        require_first_five=True,
        expected_sha256={
            role: witness["artifact_sha256"] for role, (_, witness) in artifacts.items()
        },
    )
    # An isolated CLI must use a clean versioned checkout, not a dirty composed
    # author tree. Direct synthetic build_ledger tests claim no deployment gate.
    root = Path(__file__).resolve().parents[3]
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]).strip():
        raise EvidenceFault("offline CLI requires a clean versioned checkout")
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    summary = build_ledger(
        refs=refs,
        tencent_root=args.tencent_root,
        paid_root=args.paid_root,
        output=args.output,
        node_label=args.node_label,
        budget_seconds=args.budget_seconds,
        provenance={
            "code_revision": revision,
            "release_handoff": {key: value[1] for key, value in artifacts.items()},
        },
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
