"""Three-operator, original two-expert engineering review on fixed real inputs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import platform
import resource
import signal
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone
from datetime import time as day_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.expert_evaluation.experts.trend_stability import (
    classify_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_protocols import EvidenceKind, ExpertEvidenceRef
from quanthus.expert_strategy import (
    ExpertStrategyRegistry,
    FormalExpertContext,
    FormalStrategyRunner,
)
from quanthus.feature_registry import FeatureLineage, FeatureSourceEvidence, load_feature_registry
from quanthus.label_registry import FeatureFact
from quanthus.r7_native import file_sha, materialize, require
from quanthus.structure_facts import build_continuous_structure_facts
from quanthus.structure_intelligence import StructureSourceRow

REQUEST = "config/research/research-r7/request.v1.json"
SCHEMA = "quanthus.r7-decision-review.v1"
PAYLOADS = {"rows.jsonl", "summary.json"}
FILES = PAYLOADS | {"manifest.json", "READY"}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(item) for item in value]
    if isinstance(value, (Decimal, date, datetime)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    if isinstance(value, float):
        require(math.isfinite(value), "R7_NONFINITE_VALUE")
    return value


def encoded(value: Any) -> bytes:
    return (
        json.dumps(
            clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(encoded(value)).hexdigest()


def load_request(project: Path) -> dict[str, Any]:
    request: dict[str, Any] = json.loads((project / REQUEST).read_bytes())
    require(request["schema_version"] == "quanthus.r7-multiop-request.v1", "R7_REQUEST_SCHEMA")
    require(request["request_id"] == "RESEARCH-R7-MULTIOP-001", "R7_REQUEST_ID")
    require(request["decisions"]["entry_evidence_ready"] is False, "R7_NO_OPPORTUNITY_AUTHORITY")
    require(request["decisions"]["p4_opportunity_qualified"] is False, "R7_NO_P4_OPPORTUNITY")
    known = datetime.fromisoformat(request["source_knowledge_cutoff"])
    require(known.tzinfo is not None and known.year == 2026, "R7_SOURCE_CLOCK")
    for name, expected in request["implementation_bindings"].items():
        require(file_sha(project / name) == expected, "R7_IMPLEMENTATION_BINDING:" + name)
    return request


def _valid(row: dict[str, Any]) -> bool:
    values = [row[key] for key in ("open", "high", "low", "close", "volume", "turnover")]
    if any(value is None for value in values):
        return False
    op, high, low, close, volume, amount = (Decimal(str(value)) for value in values)
    return (
        all(value.is_finite() for value in (op, high, low, close, volume, amount))
        and low > 0
        and low <= op <= high
        and low <= close <= high
        and volume >= 0
        and amount >= 0
        and row["currency"] == "CNY"
        and row["price_basis"] == "UNADJUSTED"
    )


def _context(
    native: dict[str, Any],
    feature: BaselineFeatureRow,
    request: dict[str, Any],
    project: Path,
    event_id: str,
    observation: dict[str, Any],
    snapshot: str,
) -> FormalExpertContext:
    registry = load_feature_registry(project / "config/p3-baseline-features.research.v1.json")
    cutoff = datetime.fromisoformat(request["source_knowledge_cutoff"])
    facts = []
    for name, value in (
        ("price.rsi14", feature.rsi14),
        ("risk.realized_volatility20", feature.realized_volatility20),
    ):
        definition = registry.active(name)
        lineage = FeatureLineage(
            feature_id=name,
            feature_version=definition.version,
            definition_digest=definition.digest,
            snapshot_id=snapshot,
            market_date=native["market_date"],
            knowledge_cutoff=cutoff,
            source_revision=request["authority"]["revision"],
            universe_policy_version="r7-fixed-three-security-day-v1",
            sources=(
                FeatureSourceEvidence(
                    dataset="r7-native-action-free-feature-panel",
                    column=name,
                    dataset_version="1.0.0",
                    manifest_id=digest(observation),
                    knowledge_cutoff=cutoff,
                ),
            ),
            history_sessions=max(1, feature.listing_sessions),
            cross_section_complete=None,
            cutoff_rule=definition.cutoff_rule,
        )
        facts.append(
            FeatureFact(
                security_code=native["security_id"],
                feature_id=name,
                feature_version=definition.version,
                lineage=lineage,
                value=value,
                unavailable_reason="feature_unavailable" if value is None else None,
            )
        )
    refs = tuple(
        ExpertEvidenceRef(EvidenceKind.SNAPSHOT, name, "1.0.0", value)
        for name, value in (
            (event_id, digest(native)),
            ("trend-observation:" + event_id, digest(observation)),
        )
    )
    return FormalExpertContext(
        security_code=native["security_id"],
        snapshot_id=snapshot,
        knowledge_cutoff=cutoff,
        position_state="empty",
        feature_facts=tuple(facts),
        label_results=(),
        additional_evidence_refs=refs,
    )


def project_rows(data: dict[str, Any], project: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Construct no orders and never attach optional sidecars to the original context."""
    registry = ExpertStrategyRegistry(project)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    trend_config = load_trend_stability_config(
        project / "config/expert-evaluations/experts/trend-stability.v1.json"
    )
    request_digest = digest(request)
    snapshot = "r7-native-" + request_digest.removeprefix("sha256:")
    cutoff = datetime.fromisoformat(request["source_knowledge_cutoff"])
    features = {(row["security_id"], row["market_date"]): row for row in data["features"]}
    sources = []
    resets: dict[str, bool] = {}
    invalid: set[tuple[str, date]] = set()
    for row in data["native"]:
        code = row["code"]
        if data["holds"][code] or not _valid(row):
            resets[code] = True
            invalid.add((code, row["market_date"]))
            continue
        sources.append(
            StructureSourceRow(
                market_date=row["market_date"],
                security_code=code,
                dwd_security_id=row["security_id"],
                snapshot_id=snapshot,
                knowledge_cutoff=cutoff,
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                source_revision=request["authority"]["revision"],
                source_artifact_sha256=row["bar_source_sha256"],
                history_reset=resets.pop(code, False),
            )
        )
    structure = build_continuous_structure_facts(
        sources,
        upstream_identity=request["authority"]["content_digest"],
        policy_digest=file_sha(project / "config/structure-continuous-facts.research.v1.json"),
    )
    facts = {(row.security_code, row.market_date): row for row in structure.rows}
    first, end = (
        date.fromisoformat(request["scope"][name]) for name in ("report_start", "report_end")
    )
    output = []
    for native in data["native"]:
        day, code = native["market_date"], native["code"]
        if not first <= day <= end:
            continue
        values = dict(features[native["security_id"], day])
        reasons = list(data["holds"][code])
        if (code, day) in invalid:
            reasons.append("invalid_or_missing_native_bar")
        if not values["feature_ready"]:
            reasons.append("feature_history_or_continuity_unavailable")
        if reasons:
            for name in (
                "total_return_index",
                "sma20",
                "sma60",
                "momentum20",
                "prior_high20",
                "rsi14",
                "rsi14_previous",
                "realized_volatility20",
            ):
                values[name] = None
            values["feature_ready"] = False
        # Missing native close is represented by an inert placeholder only in the typed
        # intermediate. No unavailable value becomes a displayed observation or an order.
        if values["close_price"] is None:
            values["close_price"] = Decimal("0")
        feature = BaselineFeatureRow.from_mapping(values)
        state, reason = classify_trend_stability(feature, trend_config)
        simulated_close = datetime.combine(day, day_time(15), ZoneInfo("Asia/Shanghai"))
        observation = {
            "subject_kind": "r7-security-day-review",
            "security_id": native["security_id"],
            "listing_id": native["listing_id"],
            "business_date": day,
            "simulated_t_close": simulated_close,
            "actual_source_cutoff": cutoff,
            "snapshot_id": snapshot,
            "feature_digest": digest(asdict(feature)),
            "state": state,
            "reason": reason,
            "semantics_digest": trend_config.semantic_digest,
            "historical_pit_qualified": False,
        }
        event_id = "r7-review:" + digest(observation).removeprefix("sha256:")
        context = _context(native, feature, request, project, event_id, observation, snapshot)
        baseline = runner.evaluate_entry(
            event_id=event_id, context=context, trend_state=state, entry_evidence_ready=False
        )
        structural = facts.get((code, day))
        sidecar = {
            "trend": {
                "status": "available" if state is not None else "ABSTAIN",
                "state": state,
                "reason": reason,
                "coordinate": "total_return_index_initial100_action_free",
                "values": {
                    key: values[key]
                    for key in ("total_return_index", "sma20", "sma60", "momentum20")
                },
                "consumed_by": "expert.trend-stability via accepted classification",
            },
            "structure": {
                "status": "available" if structural and structural.available else "X",
                "coordinate": "UNADJUSTED",
                "fact": None if structural is None else structural.to_dict(),
                "consumed_by": "explanation_only",
            },
            "volume": {
                "status": "available"
                if structural and structural.volume_ratio20 is not None
                else "ABSTAIN",
                "coordinate": "native_volume_prior20_excluding_T",
                "ratio20": None if structural is None else structural.volume_ratio20,
                "consumed_by": "explanation_only",
            },
        }
        after = runner.evaluate_entry(
            event_id=event_id, context=context, trend_state=state, entry_evidence_ready=False
        )
        require(baseline.digest == after.digest, "R7_SIDECAR_ACTION_DRIFT")
        output.append(
            {
                "identity": {
                    "code": code,
                    "security_id": native["security_id"],
                    "listing_id": native["listing_id"],
                    "venue_id": native["venue_id"],
                    "date": day,
                    "snapshot_id": snapshot,
                    "review_id": event_id,
                },
                "clock": {
                    "actual_source_cutoff": cutoff,
                    "simulated_t_close": simulated_close,
                    "historical_pit_qualified": False,
                },
                "source": native,
                "trend_observation": observation,
                "operators": sidecar,
                "expert_context": asdict(context),
                "decision": baseline.to_dict(),
                "baseline_digest": baseline.digest,
                "after_sidecar_digest": after.digest,
                "unavailable_reasons": sorted(set(reasons)),
                "execution": {
                    "status": "NOT_RUN_DECISION_REVIEW",
                    "p4_opportunity_qualified": False,
                    "orders": [],
                    "fills": [],
                    "position_change": None,
                },
            }
        )
    complete = sum(
        all(
            row["operators"][key]["status"] == "available"
            for key in ("trend", "structure", "volume")
        )
        for row in output
    )
    summary = {
        "schema_version": SCHEMA,
        "status": "complete" if complete else "partial",
        "scope": request["scope"],
        "row_count": len(output),
        "three_operator_available_rows": complete,
        "per_security": dict(Counter(row["identity"]["code"] for row in output)),
        "trend_states": dict(Counter(str(row["trend_observation"]["state"]) for row in output)),
        "final_actions": dict(Counter(row["decision"]["action"]["final_action"] for row in output)),
        "source_holds": data["holds"],
        "source_action_counts": data["action_counts"],
        "strategy_card_digest": registry.card.semantic_digest,
        "topology_digest": registry.topology_manifest.digest,
        "sidecar_changes": 0,
        "execution_status": "NOT_RUN_DECISION_REVIEW",
    }
    require(
        len({(row["identity"]["code"], row["identity"]["date"]) for row in output}) == len(output),
        "R7_REVIEW_DUPLICATE",
    )
    result: dict[str, Any] = clean({"rows": output, "summary": summary})
    return result


def build(source: Path, output: Path, project: Path) -> dict[str, Any]:
    request = load_request(project)
    require(not output.exists() and output.parent.is_dir(), "R7_NEW_OUTPUT")
    for root in (source, project):
        require(
            root.resolve() not in output.resolve().parents
            and output.resolve() not in root.resolve().parents,
            "R7_OUTPUT_OVERLAP",
        )
    began = time.monotonic()
    output.mkdir()
    scratch = output / "scratch"
    scratch.mkdir()
    try:
        data = materialize(source, project, request, scratch)
        result = project_rows(data, project, request)
        payload = b"".join(encoded(row) for row in result["rows"])
        require(len(payload) <= request["runtime"]["max_output_bytes"], "R7_OUTPUT_SIZE")
        (output / "rows.jsonl").write_bytes(payload)
        (output / "summary.json").write_bytes(encoded(result["summary"]))
        scratch.rmdir()
        code_paths = ["src/quanthus/r7_native.py", "src/quanthus/r7_decision_review.py"]
        manifest = {
            "schema_version": SCHEMA,
            "request": request,
            "request_sha256": file_sha(project / REQUEST),
            "input_members": data["members"],
            "code": {p: file_sha(project / p) for p in code_paths},
            "git": subprocess.check_output(
                ["git", "-C", str(project), "rev-parse", "HEAD"], text=True
            ).strip(),
            "python": platform.python_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - began,
            "files": {
                name: {"sha256": file_sha(output / name), "bytes": (output / name).stat().st_size}
                for name in sorted(PAYLOADS)
            },
            "semantic_digest": digest(result),
        }
        (output / "manifest.json").write_bytes(encoded(manifest))
        trusted = file_sha(output / "manifest.json")
        (output / "READY").write_bytes(encoded({"manifest_sha256": trusted}))
        return {
            "manifest_sha256": trusted,
            "summary": result["summary"],
            "semantic_digest": manifest["semantic_digest"],
        }
    finally:
        # Failed attempts remain visibly incomplete; no READY marker is manufactured.
        pass


def read_package(package: Path, trusted: str) -> dict[str, Any]:
    require(package.is_dir() and package.absolute() == package.resolve(), "R7_PACKAGE_PATH")
    require({p.name for p in package.iterdir()} == FILES, "R7_PACKAGE_FILES")
    require(all(p.is_file() and not p.is_symlink() for p in package.iterdir()), "R7_PACKAGE_LINK")
    require(file_sha(package / "manifest.json") == trusted, "R7_MANIFEST_HASH")
    manifest = json.loads((package / "manifest.json").read_bytes())
    require(manifest["schema_version"] == SCHEMA, "R7_PACKAGE_SCHEMA")
    require(
        json.loads((package / "READY").read_bytes())["manifest_sha256"] == trusted,
        "R7_READY_BINDING",
    )
    require(set(manifest["files"]) == PAYLOADS, "R7_DECLARED_FILES")
    for name, item in manifest["files"].items():
        path = package / name
        require(path.stat().st_size == item["bytes"] <= 16 * 1024**2, "R7_PAYLOAD_BYTES")
        require(file_sha(path) == item["sha256"], "R7_PAYLOAD_HASH")
    rows = [json.loads(line) for line in (package / "rows.jsonl").read_bytes().splitlines()]
    summary = json.loads((package / "summary.json").read_bytes())
    require(len(rows) == summary["row_count"], "R7_READER_DENOMINATOR")
    require(
        digest({"rows": rows, "summary": summary}) == manifest["semantic_digest"],
        "R7_SEMANTIC_BINDING",
    )
    return {"rows": rows, "summary": summary, "manifest_sha256": trusted}


def query(
    data: dict[str, Any],
    *,
    security: str | None = None,
    day: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    require(1 <= limit <= 1000, "R7_QUERY_LIMIT")
    rows = [
        row
        for row in data["rows"]
        if (security is None or row["identity"]["code"] == security)
        and (day is None or row["identity"]["date"] == day)
        and (action is None or row["decision"]["action"]["final_action"] == action)
        and (
            reason is None
            or reason in json.dumps(row["decision"])
            or reason in json.dumps(row["unavailable_reasons"])
        )
    ]
    return {
        "matched": len(rows),
        "returned": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "rows": rows[:limit],
        "summary": data["summary"],
        "manifest_sha256": data["manifest_sha256"],
    }


def render_html(result: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    cards = []
    for row in result["rows"]:
        identity, decision = row["identity"], row["decision"]
        action = decision["action"]
        reasons = ", ".join(action["reason_codes"])
        search = esc(
            " ".join(
                [
                    identity["code"],
                    identity["date"],
                    action["final_action"],
                    reasons,
                    json.dumps(row["unavailable_reasons"]),
                ]
            )
        )
        operators = "".join(
            f'<div class="operator"><strong>{label}</strong><span>{esc(op["status"])}</span>'
            f"<pre>{esc(json.dumps(op, ensure_ascii=False, indent=2))}</pre></div>"
            for label, op in (
                ("趋势", row["operators"]["trend"]),
                ("结构位置", row["operators"]["structure"]),
                ("量能", row["operators"]["volume"]),
            )
        )
        cards.append(f'''<article data-search="{search}"><h2>{esc(identity["code"])}
          <small>{esc(identity["date"])}</small></h2><p class="action">提议
          {esc(action["proposed_action"])} → 最终 {esc(action["final_action"])}</p>
          <p>系统限制：{esc(reasons)}。固定样本尚未取得 P4 机会资格。</p>
          <div class="operators">{operators}</div>
          <details><summary>两位专家、约束与否决回执</summary>
          <pre>{esc(json.dumps(decision, ensure_ascii=False, indent=2))}</pre></details>
          <details><summary>来源、时钟与完整记录</summary>
          <pre>{esc(json.dumps(row, ensure_ascii=False, indent=2))}</pre></details></article>''')
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';
      style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
      <title>Quanthus · R7 决策复核</title><style>
      :root{{color-scheme:light dark}}body{{font:16px/1.6 system-ui;margin:0 auto;padding:32px;
      max-width:1200px;background:light-dark(#f5f3ed,#151a1c);color:light-dark(#182b2b,#dce8e6)}}
      h1{{font-size:32px;margin-bottom:6px}}small{{font-weight:400;margin-left:12px}}
      input{{width:min(680px,90%);padding:12px;font:inherit;margin:16px 0}}
      article{{padding:24px;margin:20px 0;border:1px solid #738986;border-radius:8px}}
      .operators{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
      .operator{{min-width:0}}.operator span{{display:block;font-size:13px;color:#608f83}}
      pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace}}
      .action{{font-weight:600}}
      details{{border-top:1px solid #738986;padding-top:12px;margin-top:16px}}
      @media(max-width:750px){{.operators{{grid-template-columns:1fr}}body{{padding:16px}}}}
      </style><h1>三组证据，如何走到一次决定</h1>
      <p>固定真实开发数据 · 既有趋势与持仓风险双专家 · 研究决策复核</p>
      <p>当前报告 {result["returned"]} / 匹配 {result["matched"]} 条；完整分母
      {result["summary"]["row_count"]} 条，三算子共同可用
      {result["summary"]["three_operator_available_rows"]} 条。</p>
      <p>执行状态：仅求值，未撮合。结构位置和量能用于解释；源实际观察截止与模拟 T 日分别记录。</p>
      <label for="filter">筛选证券、日期、动作或原因</label><br>
      <input id="filter" placeholder="例如 000001 或 2020-04-10"><span id="count"></span>
      <main>{"".join(cards)}</main><footer>固定包 {esc(result["manifest_sha256"])}</footer>
      <script>const filter=document.getElementById('filter');
      function update(){{let count=0;for(const card of document.querySelectorAll('article')){{
      const show=card.dataset.search.includes(filter.value.trim());
      card.hidden=!show;if(show)count++}}
      document.getElementById('count').textContent=' 当前显示 '+count+' 条'}}
      filter.addEventListener('input',update);update();</script></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "verify", "query"])
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--security")
    parser.add_argument("--date")
    parser.add_argument("--action")
    parser.add_argument("--reason")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        require(args.source is not None, "R7_SOURCE_REQUIRED")
        request = load_request(args.project)
        resource.setrlimit(resource.RLIMIT_CPU, (request["runtime"]["cpu_seconds"],) * 2)
        signal.alarm(request["runtime"]["timeout_seconds"])
        result = build(args.source, args.package, args.project)
    else:
        require(args.manifest_sha256 is not None, "R7_TRUSTED_MANIFEST_REQUIRED")
        data = read_package(args.package, args.manifest_sha256)
        result = query(
            data,
            security=args.security,
            day=args.date,
            action=args.action,
            reason=args.reason,
            limit=args.limit,
        )
        if args.html:
            require(not args.html.exists() and args.html.parent.is_dir(), "R7_NEW_HTML")
            require(args.package.resolve() not in args.html.resolve().parents, "R7_HTML_OVERLAP")
            args.html.write_text(render_html(result), encoding="utf-8")
    print(encoded(result).decode(), end="")


if __name__ == "__main__":
    main()
