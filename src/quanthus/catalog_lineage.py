"""Bounded static processing graph, separate from membership and actual Run consumption."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any

LAYERS = ("source", "warehouse", "projection", "indicator", "structure", "label", "consumer")
LAYER_NAMES = dict(zip(
    LAYERS, ("原始来源", "规范数据", "研究投影", "指标计算", "结构事实", "规则标签", "消费者"),
    strict=True,
))
GRAINS = {"dataset", "field", "record", "definition", "consumer", "mixed"}
ROLES = {"calculation", "validation", "reference", "declared_consumer", "declared_input"}
SCOPE = "静态定义血缘；非运行审计、非全量字段覆盖、非交易授权"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _layer(item: dict[str, Any]) -> str:
    kind = item["kind"]
    if kind == "ods_dataset":
        return "source"
    if kind.startswith("dwd_"):
        return "warehouse"
    if kind == "feature_spec":
        return "indicator"
    if kind == "label_card":
        return "label"
    return "structure" if item["domain"] == "structure" else "indicator"


def build_lineage(entries: dict[str, dict[str, Any]], definition: dict[str, Any],
                  dwd: dict[str, Any], sources: list[dict[str, str]]) -> dict[str, Any]:
    if (definition.get("schema_version") != "1.0.0"
            or definition.get("authority") != "reviewed_declared_processing_dependencies"):
        raise ValueError("catalog lineage authority differs")
    source_by_adapter = {item["adapter"]: item for item in sources}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(identifier: str, label: str, layer: str, grain: str, description: str,
             *, entry_id: str | None = None, evidence: dict | None = None,
             process: bool = False) -> None:
        if identifier in nodes:
            raise ValueError(f"duplicate lineage node: {identifier}")
        nodes[identifier] = {"id": identifier, "label": label, "layer": layer, "grain": grain,
                             "description": description, "entry_id": entry_id,
                             "evidence": evidence, "process": process}

    for key, item in sorted(entries.items()):
        grain = "field" if item["kind"] == "dwd_field" else (
            "dataset" if item["kind"] in {"dwd_table", "ods_dataset"} else "definition")
        node(key, item["display_name"], _layer(item), grain, item["description"], entry_id=key)
    for item in definition["nodes"]:
        node(item["id"], item["label"], item["layer"], item["grain"], item["description"])

    def step(identifier: str, label: str, inputs: list[str], outputs: list[str],
             evidence: dict, grain: str, role: str) -> None:
        if not inputs or not outputs or any(key not in nodes for key in [*inputs, *outputs]):
            raise ValueError(f"lineage step has unregistered endpoint: {identifier}")
        process_id = "process:" + identifier
        node(process_id, label, nodes[outputs[0]]["layer"], grain,
             f"{label}。仅描述已登记的加工依赖，不证明某次运行已执行。",
             evidence=evidence, process=True)
        pairs = [(key, process_id) for key in inputs] + [(process_id, key) for key in outputs]
        for source, target in pairs:
            edge = {"source": source, "target": target, "role": role, "grain": grain,
                    "evidence": evidence, "label": label}
            edges.append({"id": _digest(edge), **edge})

    model_source = source_by_adapter["dwd_domain_model"]
    for index, item in enumerate(dwd["ods_source_contract"]["static_sources"]):
        if item["disposition"] != "modeled":
            continue
        source_id = "ods_dataset:" + item["source_id"]
        if source_id not in nodes:
            continue
        outputs = ["dwd_table:" + name for name in item["target_tables"]]
        evidence = {"path": model_source["path"], "sha256": model_source["sha256"],
                    "locator": f"/ods_source_contract/static_sources/{index}"}
        step("ods-" + item["source_id"], "源记录规范化 / 证据使用", [source_id], outputs,
             evidence, "dataset", "declared_input")

    # These are declared logical source artifacts, not invented physical ODS package members.
    sections = {"release_artifacts": "日发布工件", "weekly_artifacts": "周发布工件",
                "container_members": "源容器成员", "hotfix_artifacts": "修正来源工件"}
    for section, section_name in sections.items():
        for index, item in enumerate(dwd["ods_source_contract"][section]):
            if item["disposition"] != "modeled" or not item["target_tables"]:
                continue
            identifier = str(item.get("artifact") or item.get("member"))
            source_id = f"declared-source:{section}:{identifier}"
            outputs = ["dwd_table:" + name for name in item["target_tables"]]
            names = [entries[key]["display_name"] for key in outputs]
            evidence = {"path": model_source["path"], "sha256": model_source["sha256"],
                        "locator": f"/ods_source_contract/{section}/{index}"}
            node(source_id, f"{names[0]}来源 · {section_name}", "source", "dataset",
                 f"机器契约登记的 {identifier}，用于{'、'.join(names)}。"
                 "此处不宣称已核验实际包成员。",
                 evidence=evidence)
            step(f"{section}-{identifier}", f"{section_name}规范化", [source_id], outputs,
                 evidence, "dataset", "declared_input")

    for source_field, target_field in sorted(definition["market_field_mapping"].items()):
        dwd_id = "dwd_field:dwd_security_bar." + target_field
        source_id = "source-field:daily_ohlcv_unadjusted." + source_field
        name = entries[dwd_id]["display_name"].split(" · ")[0]
        node(source_id, "源日行情 · " + name, "source", "field",
             "转换代码声明读取的源字段；不等同于本目录已检验完整物理包成员。",
             evidence=definition["evidence"]["market"])
        step("market-" + source_field, "历史身份 / 时段对齐与定点数转换", [source_id], [dwd_id],
             definition["evidence"]["market"], "field", "calculation")
        projection_id = "projection:daily_ohlcv_none." + source_field
        node(projection_id, "研究日线 · " + name, "projection", "field",
             "Direct-DWD 研究读取器的投影字段；不是新的权威行情表。",
             evidence=definition["evidence"]["provider"])
        step("replay-" + source_field, "按日线范围导出研究字段", [dwd_id], [projection_id],
             definition["evidence"]["provider"], "field", "calculation")
        if target_field in {"high", "low", "close"}:
            exact_id = "projection:daily_ohlcv_none.dwd_" + target_field + "_decimal"
            node(exact_id, "精确回溯日线 · " + name, "projection", "field",
                 "Direct-DWD 保留的精确十进制价格成员，Facts V2 校验其与决策帧的一致性。",
                 evidence=definition["evidence"]["provider"])
            step("exact-" + target_field, "保留精确价格成员", [dwd_id], [exact_id],
                 definition["evidence"]["provider"], "field", "calculation")
    for item in definition["steps"]:
        step(item["id"], item["label"], item["inputs"], item["outputs"],
             definition["evidence"][item["evidence"]], item["grain"], item["role"])
    for item in entries.values():
        if item["kind"] != "label_card":
            continue
        requirements = [rel["target_entry_id"] for rel in item["relations"]
                        if rel["type"] == "requires_feature"]
        if requirements:
            authority = item["authority"]
            evidence = {"path": authority["path"], "sha256": authority["sha256"],
                        "locator": authority["pointer"]}
            step("label-" + item["canonical_id"], "按冻结规则判定标签",
                 requirements, [item["entry_id"]], evidence, "definition", "calculation")
    graph = {"schema_version": "quanthus.catalog-lineage.v1", "scope": SCOPE,
             "coverage": definition["coverage"], "nodes": [nodes[key] for key in sorted(nodes)],
             "edges": sorted(edges, key=lambda edge: (edge["source"], edge["target"], edge["id"]))}
    validate_lineage(graph)
    return graph


def validate_lineage(graph: dict[str, Any]) -> None:
    if graph.get("schema_version") != "quanthus.catalog-lineage.v1" or graph.get("scope") != SCOPE:
        raise ValueError("lineage scope or schema differs")
    nodes = {item["id"]: item for item in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("duplicate lineage node")
    for item in nodes.values():
        if item["layer"] not in LAYERS or item["grain"] not in GRAINS or not item["label"]:
            raise ValueError("invalid lineage node")
    indegree = dict.fromkeys(nodes, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)
    seen = set()
    for edge in graph["edges"]:
        if edge["source"] not in nodes or edge["target"] not in nodes:
            raise ValueError("dangling lineage endpoint")
        key = edge["source"], edge["target"]
        if key in seen or key[0] == key[1]:
            raise ValueError("duplicate or self lineage edge")
        seen.add(key)
        if edge["role"] not in ROLES or edge["grain"] not in GRAINS:
            raise ValueError("invalid processing relationship")
        evidence = edge["evidence"]
        if (not evidence.get("path") or not evidence.get("locator")
                or not SHA.fullmatch(evidence.get("sha256", ""))):
            raise ValueError("lineage requires exact evidence binding")
        indegree[key[1]] += 1
        adjacency[key[0]].append(key[1])
    pending = deque(sorted(key for key, degree in indegree.items() if not degree))
    visited = 0
    while pending:
        key = pending.popleft()
        visited += 1
        for target in adjacency[key]:
            indegree[target] -= 1
            if not indegree[target]:
                pending.append(target)
    if visited != len(nodes):
        raise ValueError("static processing graph contains cycle")


def overview(graph: dict[str, Any]) -> dict[str, Any]:
    """Collapse processing nodes and datasets by layer/domain; preserve drill-down members."""
    participating = {edge[side] for edge in graph["edges"] for side in ("source", "target")}
    groups: dict[str, dict[str, Any]] = {}
    node_groups = {}
    for node in graph["nodes"]:
        if node["id"] not in participating:
            continue
        group_id = node["layer"]
        group = groups.setdefault(group_id, {"id": group_id, "label": LAYER_NAMES[group_id],
                                           "layer": group_id, "members": [], "process_count": 0})
        node_groups[node["id"]] = group_id
        if node["process"]:
            group["process_count"] += 1
        else:
            group["members"].append(node)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    internal = 0
    for edge in graph["edges"]:
        pair = node_groups[edge["source"]], node_groups[edge["target"]]
        if pair[0] == pair[1]:
            internal += 1
        else:
            counts[pair] += 1
    return {"scope": SCOPE, "coverage": graph["coverage"], "internal_edges": internal,
            "registered_edges": len(graph["edges"]),
            "nodes": [groups[layer] for layer in LAYERS if layer in groups],
            "edges": [{"source": source, "target": target, "count": count}
                      for (source, target), count in sorted(counts.items())]}


def focus(graph: dict[str, Any], center: str, *, depth: int = 2, limit: int = 60) -> dict[str, Any]:
    if type(depth) is not int or not 1 <= depth <= 5:
        raise ValueError("catalog_lineage_depth_invalid")
    if type(limit) is not int or not 10 <= limit <= 100:
        raise ValueError("catalog_lineage_limit_invalid")
    nodes = {item["id"]: item for item in graph["nodes"]}
    if center not in nodes:
        raise ValueError("catalog_entry_not_found")
    downstream: dict[str, set[str]] = defaultdict(set)
    upstream: dict[str, set[str]] = defaultdict(set)
    for edge in graph["edges"]:
        downstream[edge["source"]].add(edge["target"])
        upstream[edge["target"]].add(edge["source"])
    positions = {center: 0}
    for adjacency, sign in ((upstream, -1), (downstream, 1)):
        frontier, visited = {center}, {center}
        for distance in range(1, depth + 1):
            frontier = {target for source in frontier for target in adjacency[source]} - visited
            visited.update(frontier)
            for key in sorted(frontier):
                positions.setdefault(key, sign * distance)
    ordered = sorted(positions, key=lambda key: (abs(positions[key]), positions[key], key))
    selected = set(ordered[:limit])
    visible_edges = [edge for edge in graph["edges"]
                     if edge["source"] in selected and edge["target"] in selected]
    boundary_edges = sum((edge["source"] in selected) != (edge["target"] in selected)
                         for edge in graph["edges"])
    return {"center": center, "depth": depth, "limit": limit, "scope": SCOPE,
            "coverage": graph["coverage"], "omitted_nodes": max(0, len(ordered) - limit),
            "boundary_edges": boundary_edges,
            "nodes": [{**nodes[key], "distance": positions[key]} for key in ordered[:limit]],
            "edges": visible_edges}
