"""Read-only projection of one already-consumed historical held-decision archive.

Build authenticates the exact selection. Verify/query use only the sealed package;
archived source paths are inert provenance. No policy evaluation or market reader.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_strategy.held_decision_census import census_held_decisions

CONTRACT = "config/research/decision-review-delivery.v1.json"
SCHEMA = "quanthus.historical-decision-review.v1"
PURPOSE = "CONSUMED_RETROSPECTIVE_DECISION_REVIEW_ONLY"
MAX_PACKAGE_BYTES = 64 * 1024**2
MAX_QUERY_ROWS = 100


class DecisionReviewError(ValueError):
    """A retained identity, scope or package cannot support the requested review."""


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise DecisionReviewError(reason)


def _json(value: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in items:
            require(key not in result, "REVIEW_DUPLICATE_JSON_KEY")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=pairs)


def _physical(path: Path) -> Path:
    absolute = path.absolute()
    require(
        absolute.resolve(strict=True) == absolute and not path.is_symlink(), "REVIEW_PATH_ALIAS"
    )
    return absolute


def _stat(path: Path) -> tuple[int, int, int, int]:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def load_contract(root: Path) -> dict[str, Any]:
    contract: dict[str, Any] = _json((root / CONTRACT).read_bytes())
    require(
        contract["schema_version"] == "quanthus.historical-decision-review-contract.v1"
        and contract["purpose"] == PURPOSE
        and contract["first_date"] == "2012-01-01"
        and contract["last_date"] == "2021-12-31"
        and contract["historical_PIT_qualified"] is False
        and contract["model_or_trading_admission"] == "NOT_GRANTED",
        "REVIEW_UNSUPPORTED_CONTRACT",
    )
    return contract


def _code_identity() -> dict[str, str]:
    # Imported ordinary Quanthus modules are identified, not copied into the data product.
    members = {"quanthus.expert_strategy.decision_review": sha(Path(__file__).read_bytes())}
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("quanthus") and path and path.endswith(".py"):
            members[name] = sha(Path(path).read_bytes())
    return members


def _load_archive(selection: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    selection = _physical(selection)
    raw = selection.read_bytes()
    require(sha(raw) == contract["selection_sha256"], "REVIEW_SELECTION_CHANGED")
    frozen = _json(raw)
    pins = frozen["pins"]
    require(
        len(pins) == contract["members"]
        and sum(p["bytes"] for p in pins.values()) == contract["input_bytes"]
        and frozen["expected_population"] == contract["expected_population"],
        "REVIEW_SELECTION_SCOPE",
    )
    source = _physical(Path(frozen["source_job"]))
    held_names = [n for n in pins if Path(n).name == "held-identities.TMP.private.json"]
    require(len(held_names) == 1, "REVIEW_HELD_SELECTION")
    targets = {
        held_names[0]: "held",
        str(source / "inputs.final.frozen.json"): "inputs",
        str(source / "trace/held-contexts.private.jsonl.gz"): "trace",
        str(source / "decision-shadow.private.json.gz"): "shadow",
    }
    require(set(targets) <= pins.keys(), "REVIEW_MISSING_INPUT_MEMBER")
    loaded: dict[str, Any] = {"support": [], "support_refs": {}}
    members, stats, refs = [], {}, {}
    for number, (name, identity) in enumerate(sorted(pins.items())):
        path = _physical(Path(name))
        before = _stat(path)
        raw = path.read_bytes()
        require(
            len(raw) == identity["bytes"] and sha(raw) == identity["sha256"],
            "REVIEW_INPUT_CHANGED:" + path.name,
        )
        require(_stat(path) == before, "REVIEW_INPUT_MUTATED")
        member_id = f"member-{number:04d}"
        members.append({"id": member_id, "archive_path": name, **identity})
        stats[name] = before
        if name in targets:
            kind = targets[name]
            body = gzip.decompress(raw) if path.suffix == ".gz" else raw
            value: Any = (
                [_json(line) for line in body.splitlines()] if kind == "trace" else _json(body)
            )
            loaded[kind] = value["rows"]["B"] if kind == "held" else value
            refs[kind] = member_id
        elif path.parent == source / "facts":
            value = _json(gzip.decompress(raw) if path.suffix == ".gz" else raw)
            for index, row in enumerate(value["held_decision_inputs"]):
                key = row["security_code"], row["market_date"]
                require(key not in loaded["support_refs"], "REVIEW_DUPLICATE_SUPPORT")
                loaded["support"].append(row)
                loaded["support_refs"][key] = {
                    "member_id": member_id,
                    "row": f"/held_decision_inputs/{index}",
                }
    require(loaded["inputs"]["source_cutoff"] == contract["source_cutoff"], "REVIEW_CUTOFF_CHANGED")
    require(
        all(r["context"]["knowledge_cutoff"] == contract["source_cutoff"] for r in loaded["trace"]),
        "REVIEW_CONTEXT_CUTOFF",
    )
    loaded.update(members=members, source_stats=stats, refs=refs, selection=str(selection))
    return loaded


def _number(value: Any) -> None:
    require(
        value is None or (isinstance(value, str) and Decimal(value).is_finite()),
        "REVIEW_DECIMAL_REPRESENTATION",
    )


def project_archive(archive: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """Join original rows once; the existing census enforces the complete denominator."""
    held, trace, shadow, support = (archive[k] for k in ("held", "trace", "shadow", "support"))
    census = census_held_decisions(
        held,
        trace,
        shadow,
        support,
        expected=contract["expected_population"],
        first_date=date.fromisoformat(contract["first_date"]),
        last_date=date.fromisoformat(contract["last_date"]),
    )
    traces = {(r["market_date"], r["unit_id"]): (i, r) for i, r in enumerate(trace)}
    shadows = {(r["market_date"], r["unit_id"]): (i, r) for i, r in enumerate(shadow)}
    facts = {(r["security_code"], r["market_date"]): r for r in support}
    rows = []
    for i, identity in enumerate(held):
        key = identity["market_date"], identity["unit_id"]
        ti, original = traces[key]
        si, delta = shadows[key]
        context = original["context"]
        require(
            isinstance(context["position_state"], str)
            and bool(context["position_state"])
            and context["position_state"] != "empty",
            "REVIEW_POSITION_STATE",
        )
        features = []
        seen = set()
        for index, feature in enumerate(context["feature_facts"]):
            lineage = feature["lineage"]
            require(
                feature["security_code"] == identity["security_code"]
                and lineage["market_date"] == identity["market_date"]
                and lineage["snapshot_id"] == context["snapshot_id"]
                and lineage["knowledge_cutoff"] == context["knowledge_cutoff"],
                "REVIEW_FEATURE_IDENTITY",
            )
            fkey = feature["feature_id"], feature["feature_version"]
            require(fkey not in seen, "REVIEW_DUPLICATE_FEATURE")
            seen.add(fkey)
            _number(feature["value"])
            features.append(
                {
                    k: feature[k]
                    for k in ("feature_id", "feature_version", "value", "unavailable_reason")
                }
                | {
                    "retained_lineage_sha256": sha(encoded(lineage)),
                    "source_field": f"/context/feature_facts/{index}",
                }
            )
        fact = facts[identity["security_code"], identity["market_date"]]
        _number(fact.get("signed_distance"))
        rows.append(
            {
                "identity": {
                    k: identity[k]
                    for k in ("market_date", "security_code", "unit_id", "entry_key", "entry_date")
                },
                "snapshot": {
                    "snapshot_id": context["snapshot_id"],
                    "knowledge_cutoff": context["knowledge_cutoff"],
                    "position_state": context["position_state"],
                    "historical_PIT_qualified": False,
                },
                "baseline": {
                    k: original[k]
                    for k in (
                        "baseline_action",
                        "base_required_action",
                        "base_reason_code",
                        "baseline_decision_digest",
                        "event_id",
                        "intervention_id",
                    )
                },
                "features": features,
                "structure": dict(fact),
                "parked_shadow": {
                    k: delta[k]
                    for k in (
                        "candidate_action",
                        "candidate_digest",
                        "proposed",
                        "final_action_changed",
                        "reason",
                        "baseline_decision_digest",
                    )
                },
                "sources": {
                    "held": {"member_id": archive["refs"]["held"], "row": f"/rows/B/{i}"},
                    "trace": {"member_id": archive["refs"]["trace"], "row": f"line:{ti + 1}"},
                    "shadow": {"member_id": archive["refs"]["shadow"], "row": f"/{si}"},
                    "structure": archive["support_refs"][
                        identity["security_code"], identity["market_date"]
                    ],
                },
            }
        )
    rows.sort(key=row_key)
    return {"rows": rows, "census": census}


def row_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    r = row["identity"]
    return r["market_date"], r["security_code"], r["unit_id"], r["entry_key"]


def _write(path: Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def write_query_output(
    path: Path, value: bytes, package: Path, manifest: Mapping[str, Any]
) -> None:
    """A query may create an external view, never mutate its package or archive."""
    path = path.absolute()
    _physical(path.parent)
    protected = [package.absolute(), *(Path(m["archive_path"]).parent for m in manifest["members"])]
    require(
        not any(path == root or path.is_relative_to(root) for root in protected),
        "REVIEW_QUERY_OUTPUT_OVERLAP",
    )
    _write(path, value)


def build_package(selection: Path, output: Path, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    contract = load_contract(root)
    code = _code_identity()
    output = output.absolute()
    require(not output.exists() and not output.is_symlink(), "REVIEW_OUTPUT_EXISTS")
    parent = _physical(output.parent)
    archive = _load_archive(selection, contract)
    source_roots = {Path(n).parent for n in archive["source_stats"]}
    source_roots.add(Path(archive["selection"]).parent)
    for source in source_roots:
        require(
            not (
                output == source or output.is_relative_to(source) or source.is_relative_to(output)
            ),
            "REVIEW_OUTPUT_SOURCE_OVERLAP",
        )
    projection = project_archive(archive, contract)
    records = b"".join(encoded(row) + b"\n" for row in projection["rows"])
    source_inputs = archive["inputs"]
    manifest = {
        "schema_version": SCHEMA,
        "purpose": PURPOSE,
        "execution_authorized": False,
        "consumer_admission": "RESEARCH_DIAGNOSTIC_ONLY",
        "historical_PIT_qualified": False,
        "scope": {k: contract[k] for k in ("first_date", "last_date", "source_cutoff")},
        "selection_sha256": contract["selection_sha256"],
        "contract_sha256": sha(encoded(contract)),
        "code_sha256": code,
        "retained_producer": {
            "code_config_sha256": source_inputs["code_config_sha256"],
            "source_manifest_sha256": source_inputs["source_manifest_sha256"],
        },
        "model_use": "NOT_OBSERVED_IN_RETAINED_HELD_TRACE",
        "unobserved_alternatives": projection["census"]["unobserved_needs"],
        "census": projection["census"],
        "members": archive["members"],
        "files": {"reviews.jsonl": {"bytes": len(records), "sha256": sha(records)}},
        "stages": {
            "projection_build_count": 1,
            "facts_materializations": 0,
            "baseline_evaluations": 0,
            "model_scores": 0,
        },
    }
    body = encoded(manifest) + b"\n"
    size = len(body) + len(records) + 65
    require(size <= min(MAX_PACKAGE_BYTES, contract["max_output_bytes"]), "REVIEW_OUTPUT_BUDGET")
    free = os.statvfs(parent)
    require(
        free.f_bavail * free.f_frsize - size >= contract["minimum_free_bytes"], "REVIEW_FREE_SPACE"
    )
    require(_code_identity() == code, "REVIEW_CODE_CHANGED")
    for name, before in archive["source_stats"].items():
        require(_stat(Path(name)) == before, "REVIEW_INPUT_MUTATED")
    # No directory/lock/output is created until source and semantic validation pass.
    output.mkdir(mode=0o700)
    _write(output / "reviews.jsonl", records)
    _write(output / "manifest.json", body)
    digest = sha(body)
    _write(output / "READY", (digest + "\n").encode())
    return {
        "status": "SEALED_RESEARCH_REVIEW_ONLY",
        "manifest_sha256": digest,
        "reviews": len(projection["rows"]),
        "output_bytes": size,
        "elapsed_seconds": time.monotonic() - started,
    }


def read_package(
    package: Path, manifest_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Authenticate a self-contained projection without dereferencing source paths."""
    package = _physical(package)
    require(re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is not None, "REVIEW_MANIFEST_IDENTITY")
    require(
        {p.name for p in package.iterdir()} == {"manifest.json", "reviews.jsonl", "READY"},
        "REVIEW_PHYSICAL_FILE_SET",
    )
    for path in package.iterdir():
        _physical(path)
        require(path.is_file() and path.stat().st_nlink == 1, "REVIEW_NONINDEPENDENT_FILE")
    require((package / "manifest.json").stat().st_size <= 2 * 1024**2, "REVIEW_MANIFEST_SIZE")
    raw = (package / "manifest.json").read_bytes()
    require(
        sha(raw) == manifest_sha256
        and (package / "READY").read_bytes() == (manifest_sha256 + "\n").encode(),
        "REVIEW_MANIFEST_CHANGED",
    )
    manifest = _json(raw)
    require(
        manifest["schema_version"] == SCHEMA
        and manifest["purpose"] == PURPOSE
        and manifest["execution_authorized"] is False
        and manifest["historical_PIT_qualified"] is False
        and manifest["consumer_admission"] == "RESEARCH_DIAGNOSTIC_ONLY",
        "REVIEW_PACKAGE_SCOPE",
    )
    require(
        manifest["scope"]["first_date"] == "2012-01-01"
        and manifest["scope"]["last_date"] == "2021-12-31",
        "REVIEW_PACKAGE_WINDOW",
    )
    size = (package / "reviews.jsonl").stat().st_size
    require(size <= MAX_PACKAGE_BYTES, "REVIEW_PACKAGE_SIZE")
    payload = (package / "reviews.jsonl").read_bytes()
    identity = manifest["files"]["reviews.jsonl"]
    require(
        len(payload) == identity["bytes"] and sha(payload) == identity["sha256"],
        "REVIEW_PAYLOAD_CHANGED",
    )
    rows = [_json(line) for line in payload.splitlines()]
    keys = [row_key(row) for row in rows]
    require(keys == sorted(set(keys)), "REVIEW_KEY_CENSUS")
    member_ids = {m["id"] for m in manifest["members"]}
    for row in rows:
        require(
            "2012-01-01" <= row["identity"]["market_date"] <= "2021-12-31",
            "REVIEW_DATE_OUTSIDE_SCOPE",
        )
        require(
            row["snapshot"]["knowledge_cutoff"] == manifest["scope"]["source_cutoff"]
            and row["snapshot"]["historical_PIT_qualified"] is False,
            "REVIEW_SNAPSHOT_BINDING",
        )
        require(
            set(row["sources"]) == {"held", "trace", "shadow", "structure"}
            and all(ref["member_id"] in member_ids for ref in row["sources"].values()),
            "REVIEW_MEMBER_CLOSURE",
        )
    observed = {
        "reviews": len(rows),
        "episodes": len({r["identity"]["entry_key"] for r in rows}),
        "issuers": len({r["identity"]["security_code"] for r in rows}),
        "unique_security_dates": len(
            {(r["identity"]["security_code"], r["identity"]["market_date"]) for r in rows}
        ),
    }
    require(observed == manifest["census"]["population"], "REVIEW_POPULATION_CHANGED")
    return manifest, rows


def query_rows(
    rows: list[dict[str, Any]],
    *,
    security: str | None = None,
    day: str | None = None,
    unit: str | None = None,
    episode: str | None = None,
    reason: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    require(type(limit) is int and 1 <= limit <= MAX_QUERY_ROWS, "REVIEW_QUERY_LIMIT")
    if day is not None:
        require(date.fromisoformat(day).isoformat() == day, "REVIEW_QUERY_DATE")
    matching = [
        r
        for r in rows
        if (security is None or r["identity"]["security_code"] == security)
        and (day is None or r["identity"]["market_date"] == day)
        and (unit is None or r["identity"]["unit_id"] == unit)
        and (episode is None or r["identity"]["entry_key"] == episode)
        and (
            reason is None
            or reason
            in {
                r["baseline"]["base_reason_code"],
                r["structure"]["reason"],
                r["parked_shadow"]["reason"],
            }
        )
    ]
    return {
        "matched": len(matching),
        "returned": min(limit, len(matching)),
        "truncated": len(matching) > limit,
        "rows": matching[:limit],
    }


REASONS = {
    "base_constraint_stop_exit": "原约束止损要求退出",
    "base_maximum_holding_exit": "达到原最长持有期",
    "base_structural_deterioration_exit": "原基准识别到结构风险",
    "base_position_management_clear": "原基准未要求退出",
    "insufficient_history": "历史长度不足，结构信息未知",
    "outside_declared_support_horizon": "超出原结构支持范围，信息未知",
    "zero_activity_not_observed_trading": "未观察到有效交易活动，结构信息未知",
    "identity_coordinate_or_calendar_discontinuity": "身份、价格坐标或日历不连续",
}
ACTIONS = {"no_action": "持有／无新增动作", "reduce": "减仓", "full_exit": "退出"}


def render_html(result: Mapping[str, Any], manifest: Mapping[str, Any], digest: str) -> str:
    """An offline human view of exact recorded values, never a calculation engine."""

    def escape(value: Any) -> str:
        return html.escape(str(value), quote=True)

    cards = []
    members = {m["id"]: m for m in manifest["members"]}
    for row in result["rows"]:
        identity, baseline, structure = (row[k] for k in ("identity", "baseline", "structure"))
        reason = baseline["base_reason_code"]
        feature_rows = "".join(
            "<tr><td>"
            + escape(f["feature_id"])
            + "</td><td>"
            + escape("未知：" + str(f["unavailable_reason"]) if f["value"] is None else f["value"])
            + "</td></tr>"
            for f in row["features"]
        )
        provenance = {
            role: {**ref, **members[ref["member_id"]]} for role, ref in row["sources"].items()
        }
        details = json.dumps(
            {"event": identity, "recorded": row, "source_members": provenance},
            ensure_ascii=False,
            indent=2,
        )
        cards.append(
            "<article><h2>"
            + escape(identity["security_code"])
            + " · "
            + escape(identity["market_date"])
            + "</h2><p>资金单元 "
            + escape(identity["unit_id"])
            + " · 持仓事件 "
            + escape(identity["entry_key"])
            + "</p><h3>原基准决定："
            + escape(ACTIONS.get(baseline["baseline_action"], baseline["baseline_action"]))
            + "</h3><p>"
            + escape(REASONS.get(reason, reason))
            + " <code>"
            + escape(reason)
            + "</code></p><p>原安全要求："
            + escape(baseline["base_required_action"])
            + "</p><h3>当次保留事实</h3><table><tbody>"
            + feature_rows
            + "</tbody></table><p>结构信息："
            + escape("可用" if structure["available"] else "未知")
            + "；"
            + escape(REASONS.get(structure["reason"], structure["reason"]))
            + "</p><p>旧研究建议仅用于解释，原停放结论保留。</p>"
            + "<details><summary>查看原始值、事件身份和来源</summary><pre>"
            + escape(details)
            + "</pre></details></article>"
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'\">"
        "<title>Quanthus 历史决策复盘</title><style>"
        "body{font:16px/1.6 system-ui;max-width:1050px;margin:32px auto;padding:0 20px;"
        "color:#152330;background:#f5f7fa}article{background:white;padding:24px;"
        "margin:20px 0;border:1px solid #dce2e8;border-radius:12px}"
        "pre,code{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}"
        "td{padding:5px 20px 5px 0}.scope{border-left:4px solid #bc7915;padding:12px}"
        '</style></head><body><h1>历史决策复盘</h1><p class="scope">'
        "回溯研究材料；业务日期限2012–2021。来源观察截止："
        + escape(manifest["scope"]["source_cutoff"])
        + "。不能视为历史当时已知；未授予交易执行资格。</p><p>匹配 "
        + escape(result["matched"])
        + " 条，展示 "
        + escape(result["returned"])
        + " 条。证券日期可能有多个资金单元，请按完整事件身份查阅。</p>"
        + ("".join(cards) if cards else "<p>没有匹配记录；不会补造或替换来源。</p>")
        + "<footer>封存清单 SHA256：<code>"
        + escape(digest)
        + "</code></footer></body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Project the exact already-consumed archive once")
    build.add_argument("--selection", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    for name in ("verify", "query"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--package", type=Path, required=True)
        cmd.add_argument("--manifest-sha256", required=True)
        if name == "query":
            for field in ("security", "date", "unit", "episode", "reason"):
                cmd.add_argument("--" + field)
            cmd.add_argument("--limit", type=int, default=12)
            cmd.add_argument("--format", choices=("json", "html"), default="json")
            cmd.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build_package(args.selection, args.output, args.root)))
        return
    manifest, rows = read_package(args.package, args.manifest_sha256)
    if args.command == "verify":
        print(
            json.dumps(
                {
                    "status": "VERIFIED_REVIEW_ONLY",
                    "reviews": len(rows),
                    "population": manifest["census"]["population"],
                }
            )
        )
        return
    result = query_rows(
        rows,
        security=args.security,
        day=args.date,
        unit=args.unit,
        episode=args.episode,
        reason=args.reason,
        limit=args.limit,
    )
    payload = (
        render_html(result, manifest, args.manifest_sha256)
        if args.format == "html"
        else json.dumps(
            {"manifest_sha256": args.manifest_sha256, "purpose": PURPOSE, **result},
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.output:
        write_query_output(args.output, (payload + "\n").encode(), args.package, manifest)
        print(json.dumps({"output": str(args.output), "returned": result["returned"]}))
    else:
        print(payload)


if __name__ == "__main__":
    main()
