"""Governed ODS -> DWD -> Research ADM asset lineage for the Catalog.

Data Lineage nodes are formal data assets only. Fields are nested coordinates on those assets;
transformation steps, formulas, projections and consumers are edge metadata or separate views.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "quanthus.catalog-asset-lineage.v2"
ASSET_KINDS = {"ods_dataset": "ods", "dwd_table": "dwd", "adm_dataset": "adm"}
FIELD_KINDS = {"dwd_field", "adm_field"}
LAYERS = ("ods", "dwd", "adm")
LAYER_NAMES = {"ods": "ODS", "dwd": "DWD", "adm": "Research ADM"}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _source(sources: list[dict[str, str]], adapter: str) -> dict[str, str]:
    return next(item for item in sources if item["adapter"] == adapter)


def _evidence(
    root: Path,
    raw: Mapping[str, object],
    frozen_hashes: Mapping[str, str],
) -> dict[str, str]:
    path_text = str(raw.get("path") or "").strip()
    locator = str(raw.get("locator") or "").strip()
    if not path_text or not locator:
        raise ValueError("asset lineage evidence requires path and locator")
    expected_sha = str(frozen_hashes.get(path_text) or "").strip()
    if not expected_sha.startswith("sha256:"):
        raise ValueError(f"asset lineage evidence digest is not frozen: {path_text}")
    path = (root / path_text).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"asset lineage evidence path escapes repository: {path_text}")
    # Development/review checkouts verify the frozen digest against the actual source. The lean
    # Platform image deliberately excludes backtest/event-library implementation modules; there the
    # digest remains trustworthy because this registry/config is itself snapshot- and source-bound.
    if path.is_file() and _file_sha(path) != expected_sha:
        raise ValueError(f"asset lineage evidence digest differs: {path_text}")
    return {"path": path_text, "locator": locator, "sha256": expected_sha}


def _field_entry_parent(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    if entry["kind"] == "dwd_field":
        return f"dwd_table:{entry['details']['table']}", str(entry["details"]["field"])
    if entry["kind"] == "adm_field":
        return f"adm_dataset:{entry['details']['dataset_id']}", str(entry["details"]["field"])
    return None


def build_asset_lineage(
    entries: dict[str, dict[str, Any]],
    registry: dict[str, Any],
    definition: dict[str, Any],
    dwd: dict[str, Any],
    sources: list[dict[str, str]],
    root: Path,
) -> dict[str, Any]:
    if registry.get("schema_version") != "1.0.0" or registry.get("authority") != "governed_research_adm_asset_registry":
        raise ValueError("ADM asset registry authority differs")
    if definition.get("schema_version") != "2.0.0" or definition.get("authority") != "reviewed_governed_asset_lineage":
        raise ValueError("asset lineage definition authority differs")

    evidence_hashes: dict[str, str] = {}
    for source in (registry, definition):
        raw_hashes = source.get("evidence_sha256")
        if not isinstance(raw_hashes, Mapping):
            raise ValueError("asset lineage evidence digest registry is missing")
        for path_text, sha in raw_hashes.items():
            path_key, sha_text = str(path_text), str(sha)
            if path_key in evidence_hashes and evidence_hashes[path_key] != sha_text:
                raise ValueError("asset lineage evidence digest registry conflicts")
            evidence_hashes[path_key] = sha_text

    nodes: dict[str, dict[str, Any]] = {}
    for entry_id, item in sorted(entries.items()):
        if item["kind"] not in ASSET_KINDS:
            continue
        nodes[entry_id] = {
            "id": entry_id,
            "entry_id": entry_id,
            "asset_kind": item["kind"],
            "layer": ASSET_KINDS[item["kind"]],
            "label": item["display_name"],
            "technical_id": item["canonical_id"],
            "description": item["description"],
        }

    edges_by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    def add_edge(
        source: str,
        target: str,
        *,
        transformation: str,
        evidence: list[dict[str, str]],
        field_mappings: list[dict[str, str]] | None = None,
        notes: list[str] | None = None,
    ) -> None:
        if source not in nodes or target not in nodes:
            raise ValueError(f"asset lineage endpoint is not a formal asset: {source} -> {target}")
        key = source, target
        mappings = list(field_mappings or [])
        if key in edges_by_pair:
            current = edges_by_pair[key]
            current["evidence"] = sorted(
                {json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in [*current["evidence"], *evidence]}.values(),
                key=lambda item: (item["path"], item["locator"], item["sha256"]),
            )
            current["field_mappings"] = sorted(
                {(
                    item["source_field"], item["target_field"]
                ): item for item in [*current["field_mappings"], *mappings]}.values(),
                key=lambda item: (item["source_field"], item["target_field"]),
            )
            current["notes"] = list(dict.fromkeys([*current["notes"], *(notes or [])]))
            if transformation not in current["transformations"]:
                current["transformations"].append(transformation)
            current["proof_level"] = "field_proven" if current["field_mappings"] else "asset_declared"
            return
        payload = {
            "source": source,
            "target": target,
            "proof_level": "field_proven" if mappings else "asset_declared",
            "transformations": [transformation],
            "field_mappings": sorted(mappings, key=lambda item: (item["source_field"], item["target_field"])),
            "evidence": sorted(evidence, key=lambda item: (item["path"], item["locator"], item["sha256"])),
            "notes": list(notes or []),
        }
        edges_by_pair[key] = {"id": _digest(payload), **payload}

    dwd_source = _source(sources, "dwd_domain_model")
    contract = dwd.get("ods_source_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("DWD ODS source contract is unavailable")
    static_sources = contract.get("static_sources")
    if not isinstance(static_sources, list):
        raise ValueError("DWD static source contract is unavailable")
    for index, item in enumerate(static_sources):
        if not isinstance(item, Mapping) or item.get("disposition") != "modeled":
            continue
        source_id = f"ods_dataset:{item.get('source_id')}"
        if source_id not in nodes:
            continue
        contract_evidence = [{
            "path": dwd_source["path"],
            "locator": f"/ods_source_contract/static_sources/{index}",
            "sha256": dwd_source["sha256"],
        }]
        for table in item.get("target_tables", []):
            target_id = f"dwd_table:{table}"
            if target_id in nodes:
                add_edge(
                    source_id,
                    target_id,
                    transformation="ODS 记录按 DWD 机器契约规范化为正式业务表。",
                    evidence=contract_evidence,
                    notes=["该边证明资产级来源；没有 field_mappings 时不代表所有源字段与目标字段一一对应。"],
                )

    # Additional reviewed logical ODS assets collapse recurring/container physical members into
    # their governed business dataset identity. The physical artifact/member stays in evidence;
    # it is never promoted to a separate lineage node.
    for raw in definition.get("additional_ods_dwd_relations", []):
        if not isinstance(raw, Mapping) or set(raw) != {
            "source_asset", "contract_section", "contract_index"
        }:
            raise ValueError("additional ODS/DWD relation fields differ")
        source_asset = str(raw.get("source_asset") or "").strip()
        section = str(raw.get("contract_section") or "").strip()
        index = raw.get("contract_index")
        records = contract.get(section)
        if not source_asset or type(index) is not int or not isinstance(records, list) or not 0 <= index < len(records):
            raise ValueError("additional ODS/DWD contract pointer is invalid")
        record = records[index]
        if not isinstance(record, Mapping) or record.get("disposition") != "modeled":
            raise ValueError("additional ODS/DWD relation is not modeled")
        contract_identity = record.get("artifact") or record.get("member")
        expected_source = record.get("container_source_id") if section == "container_members" else contract_identity
        if expected_source != source_asset:
            raise ValueError("additional ODS/DWD relation source differs from contract")
        source_id = f"ods_dataset:{source_asset}"
        if source_id not in nodes:
            raise ValueError("additional ODS/DWD relation source is not a formal ODS asset")
        evidence = [{
            "path": dwd_source["path"],
            "locator": f"/ods_source_contract/{section}/{index}",
            "sha256": dwd_source["sha256"],
        }]
        description = str(record.get("reason") or contract_identity or source_asset)
        for table in record.get("target_tables", []):
            target_id = f"dwd_table:{table}"
            if target_id not in nodes:
                continue
            add_edge(
                source_id,
                target_id,
                transformation=f"ODS 业务资产经受治理 {section} 输入形成 DWD；{description}",
                evidence=evidence,
                notes=[
                    f"物理输入身份：{contract_identity}；Data Lineage 节点保持逻辑 ODS 资产 {source_asset}。"
                ],
            )

    for raw in definition.get("ods_dwd_field_mappings", []):
        if not isinstance(raw, Mapping):
            raise ValueError("ODS/DWD field mapping must be an object")
        source_id = f"ods_dataset:{raw.get('source_asset')}"
        target_id = f"dwd_table:{raw.get('target_asset')}"
        mappings = [
            {"source_field": str(pair[0]), "target_field": str(pair[1])}
            for pair in raw.get("field_mappings", [])
            if isinstance(pair, list) and len(pair) == 2
        ]
        for mapping in mappings:
            if f"dwd_field:{raw.get('target_asset')}.{mapping['target_field']}" not in entries:
                raise ValueError("ODS/DWD lineage target field is not a governed DWD field")
        add_edge(
            source_id,
            target_id,
            transformation=str(raw.get("transformation") or "精确字段映射"),
            evidence=[_evidence(root, raw.get("evidence") or {}, evidence_hashes)],
            field_mappings=mappings,
        )

    registry_source = _source(sources, "adm_registry")
    for asset_index, asset in enumerate(registry.get("assets", [])):
        if not isinstance(asset, Mapping):
            raise ValueError("ADM registry asset must be an object")
        target_id = f"adm_dataset:{asset.get('dataset_id')}"
        if target_id not in nodes:
            raise ValueError(f"ADM registry dataset is not catalogued: {target_id}")
        registry_evidence = {
            "path": registry_source["path"],
            "locator": f"/assets/{asset_index}",
            "sha256": registry_source["sha256"],
        }
        for upstream in asset.get("upstream", []):
            if not isinstance(upstream, Mapping):
                raise ValueError("ADM upstream declaration must be an object")
            source_kind = str(upstream.get("source_kind") or "dwd")
            if source_kind not in {"dwd", "adm"}:
                raise ValueError("ADM upstream source kind is invalid")
            source_id = (
                f"dwd_table:{upstream.get('source_asset')}"
                if source_kind == "dwd"
                else f"adm_dataset:{upstream.get('source_asset')}"
            )
            mappings = [
                {"source_field": str(pair[0]), "target_field": str(pair[1])}
                for pair in upstream.get("field_mappings", [])
                if isinstance(pair, list) and len(pair) == 2
            ]
            for mapping in mappings:
                source_field_id = (
                    f"dwd_field:{upstream.get('source_asset')}.{mapping['source_field']}"
                    if source_kind == "dwd"
                    else f"adm_field:{upstream.get('source_asset')}.{mapping['source_field']}"
                )
                if source_field_id not in entries:
                    raise ValueError("ADM lineage source field is not a governed source field")
                if f"adm_field:{asset.get('dataset_id')}.{mapping['target_field']}" not in entries:
                    raise ValueError("ADM lineage target field is not a registered ADM field")
            add_edge(
                source_id,
                target_id,
                transformation=str(upstream.get("transformation") or "DWD 形成 Research ADM"),
                evidence=[registry_evidence, _evidence(root, upstream.get("evidence") or {}, evidence_hashes)],
                field_mappings=mappings,
                notes=["Research ADM 是应用数据资产，不改写 DWD 权威。"],
            )

    graph = {
        "schema_version": SCHEMA,
        "scope": str(definition.get("scope")),
        "layers": [{"id": layer, "label": LAYER_NAMES[layer]} for layer in LAYERS],
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": sorted(edges_by_pair.values(), key=lambda item: (item["source"], item["target"])),
    }
    validate_asset_lineage(graph)
    return graph


def validate_asset_lineage(graph: Mapping[str, Any]) -> None:
    if graph.get("schema_version") != SCHEMA:
        raise ValueError("asset lineage schema differs")
    nodes = {item["id"]: item for item in graph.get("nodes", [])}
    if len(nodes) != len(graph.get("nodes", [])):
        raise ValueError("duplicate asset lineage node")
    for item in nodes.values():
        if item.get("asset_kind") not in ASSET_KINDS or item.get("layer") not in LAYERS:
            raise ValueError("non-asset node entered Data Lineage")
    indegree = dict.fromkeys(nodes, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for edge in graph.get("edges", []):
        key = str(edge.get("source")), str(edge.get("target"))
        if key in seen or key[0] not in nodes or key[1] not in nodes or key[0] == key[1]:
            raise ValueError("invalid asset lineage edge")
        seen.add(key)
        if edge.get("proof_level") not in {"asset_declared", "field_proven"}:
            raise ValueError("asset lineage proof level is invalid")
        mappings = edge.get("field_mappings")
        if not isinstance(mappings, list) or any(
            not isinstance(item, Mapping) or set(item) != {"source_field", "target_field"}
            for item in mappings
        ):
            raise ValueError("asset lineage field mappings are invalid")
        evidence = edge.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(item, Mapping) or set(item) != {"path", "locator", "sha256"}
            or not str(item["sha256"]).startswith("sha256:") for item in evidence
        ):
            raise ValueError("asset lineage evidence is invalid")
        indegree[key[1]] += 1
        adjacency[key[0]].append(key[1])
    pending = deque(sorted(node for node, degree in indegree.items() if not degree))
    visited = 0
    while pending:
        node = pending.popleft()
        visited += 1
        for target in adjacency[node]:
            indegree[target] -= 1
            if not indegree[target]:
                pending.append(target)
    if visited != len(nodes):
        raise ValueError("asset lineage contains cycle")


def overview(graph: Mapping[str, Any]) -> dict[str, Any]:
    participating = {edge[side] for edge in graph["edges"] for side in ("source", "target")}
    by_id = {node["id"]: node for node in graph["nodes"]}
    participating_counts = {layer: 0 for layer in LAYERS}
    registered_counts = {layer: 0 for layer in LAYERS}
    for node in graph["nodes"]:
        registered_counts[node["layer"]] += 1
    for node_id in participating:
        participating_counts[by_id[node_id]["layer"]] += 1
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)
    for edge in graph["edges"]:
        pair = by_id[edge["source"]]["layer"], by_id[edge["target"]]["layer"]
        edge_counts[pair] += 1
    return {
        "scope": graph["scope"],
        "registered_asset_count": len(graph["nodes"]),
        "participating_asset_count": len(participating),
        "edge_count": len(graph["edges"]),
        "layers": [
            {
                "id": layer,
                "label": LAYER_NAMES[layer],
                "registered_asset_count": registered_counts[layer],
                "participating_asset_count": participating_counts[layer],
            }
            for layer in LAYERS
        ],
        "edges": [
            {"source": source, "target": target, "count": count}
            for (source, target), count in sorted(edge_counts.items())
        ],
    }


def focus(
    graph: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    center: str,
    *,
    scope: str = "full",
    depth: int = 2,
    limit: int = 100,
) -> dict[str, Any]:
    if scope not in {"full", "focus"}:
        raise ValueError("catalog_asset_lineage_scope_invalid")
    if type(depth) is not int or not 1 <= depth <= 5:
        raise ValueError("catalog_asset_lineage_depth_invalid")
    if type(limit) is not int or not 10 <= limit <= 100:
        raise ValueError("catalog_asset_lineage_limit_invalid")
    nodes = {item["id"]: item for item in graph["nodes"]}
    entry = entries.get(center)
    if entry is None:
        raise ValueError("catalog_entry_not_found")
    parent = _field_entry_parent(entry)
    center_asset, center_field = (parent if parent is not None else (center, None))
    if center_asset not in nodes:
        raise ValueError("catalog_asset_lineage_center_not_asset")

    upstream: dict[str, list[dict[str, Any]]] = defaultdict(list)
    downstream: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph["edges"]:
        upstream[edge["target"]].append(edge)
        downstream[edge["source"]].append(edge)

    max_depth = None if scope == "full" else depth

    def asset_closure() -> tuple[dict[str, int], list[dict[str, Any]], int, int]:
        positions = {center_asset: 0}
        used_edge_ids: set[str] = set()
        depth_boundary_ids: set[str] = set()
        for adjacency, sign in ((upstream, -1), (downstream, 1)):
            frontier = {center_asset}
            distance = 0
            visited = {center_asset}
            while frontier and (max_depth is None or distance < max_depth):
                distance += 1
                next_frontier: set[str] = set()
                for asset in frontier:
                    for edge in adjacency[asset]:
                        target = edge["source"] if sign < 0 else edge["target"]
                        used_edge_ids.add(edge["id"])
                        if target not in visited:
                            next_frontier.add(target)
                            positions.setdefault(target, sign * distance)
                visited.update(next_frontier)
                frontier = next_frontier
            if max_depth is not None and frontier:
                for asset in frontier:
                    for edge in adjacency[asset]:
                        target = edge["source"] if sign < 0 else edge["target"]
                        if target not in visited:
                            depth_boundary_ids.add(edge["id"])
        ordered = sorted(positions, key=lambda key: (abs(positions[key]), positions[key], key))
        selected_order = ordered[:limit]
        selected = set(selected_order)
        relevant = [edge for edge in graph["edges"] if edge["id"] in used_edge_ids]
        visible = [edge for edge in relevant if edge["source"] in selected and edge["target"] in selected]
        budget_boundary = sum((edge["source"] in selected) != (edge["target"] in selected) for edge in relevant)
        return (
            {key: positions[key] for key in selected_order},
            visible,
            max(0, len(ordered) - len(selected_order)),
            len(depth_boundary_ids) + budget_boundary,
        )

    def field_closure() -> tuple[dict[str, int], list[dict[str, Any]], dict[str, set[str]], int, int]:
        field_positions: dict[tuple[str, str], int] = {(center_asset, str(center_field)): 0}
        focus_fields: dict[str, set[str]] = defaultdict(set)
        focus_fields[center_asset].add(str(center_field))
        used_edge_ids: set[str] = set()
        depth_boundary_ids: set[str] = set()
        for adjacency, sign, source_key, target_key in (
            (upstream, -1, "target_field", "source_field"),
            (downstream, 1, "source_field", "target_field"),
        ):
            frontier = {(center_asset, str(center_field))}
            visited = set(frontier)
            distance = 0
            while frontier and (max_depth is None or distance < max_depth):
                distance += 1
                next_frontier: set[tuple[str, str]] = set()
                for asset, field in frontier:
                    for edge in adjacency[asset]:
                        for mapping in edge["field_mappings"]:
                            if mapping[source_key] != field:
                                continue
                            next_asset = edge["source"] if sign < 0 else edge["target"]
                            next_field = mapping[target_key]
                            pair = (next_asset, next_field)
                            used_edge_ids.add(edge["id"])
                            if pair not in visited:
                                next_frontier.add(pair)
                            field_positions.setdefault(pair, sign * distance)
                            focus_fields[next_asset].add(next_field)
                visited.update(next_frontier)
                frontier = next_frontier
            if max_depth is not None and frontier:
                for asset, field in frontier:
                    for edge in adjacency[asset]:
                        if any(mapping[source_key] == field for mapping in edge["field_mappings"]):
                            next_asset = edge["source"] if sign < 0 else edge["target"]
                            if any((next_asset, mapping[target_key]) not in visited for mapping in edge["field_mappings"] if mapping[source_key] == field):
                                depth_boundary_ids.add(edge["id"])
        asset_positions: dict[str, int] = {center_asset: 0}
        for (asset, _field), distance in field_positions.items():
            current = asset_positions.get(asset)
            if current is None or abs(distance) < abs(current):
                asset_positions[asset] = distance
        ordered = sorted(asset_positions, key=lambda key: (abs(asset_positions[key]), asset_positions[key], key))
        selected_order = ordered[:limit]
        selected = set(selected_order)
        relevant = [edge for edge in graph["edges"] if edge["id"] in used_edge_ids]
        visible = [edge for edge in relevant if edge["source"] in selected and edge["target"] in selected]
        budget_boundary = sum((edge["source"] in selected) != (edge["target"] in selected) for edge in relevant)
        return (
            {key: asset_positions[key] for key in selected_order},
            visible,
            focus_fields,
            max(0, len(ordered) - len(selected_order)),
            len(depth_boundary_ids) + budget_boundary,
        )

    focus_fields: dict[str, set[str]] = defaultdict(set)
    omitted_nodes = 0
    boundary_edges = 0
    if center_field is not None:
        positions, visible_edges, focus_fields, omitted_nodes, boundary_edges = field_closure()
        field_proven = bool(visible_edges)
        if not field_proven:
            positions, visible_edges, omitted_nodes, boundary_edges = asset_closure()
            focus_fields[center_asset].add(center_field)
            coverage_level = "asset_context_only"
            coverage_message = "当前字段尚无完整精确字段链；下图展示所属正式数据资产的完整已登记上下游，不推断字段映射。"
        else:
            coverage_level = "field_proven"
            coverage_message = "字段按已登记精确映射跨正式数据资产追溯；加工过程位于箭头说明而不是独立节点。"
    else:
        positions, visible_edges, omitted_nodes, boundary_edges = asset_closure()
        coverage_level = "asset_full" if scope == "full" else "asset_focus"
        coverage_message = "展示当前正式数据资产的完整已登记上游与下游。" if scope == "full" else f"展示当前正式数据资产上下游 {depth} 跳。"

    selected = set(positions)
    ordered_nodes = sorted(selected, key=lambda key: (abs(positions[key]), positions[key], key))
    return {
        "schema_version": SCHEMA,
        "center": center_asset,
        "center_entry_id": center,
        "center_field": center_field,
        "scope_mode": scope,
        "depth": depth,
        "limit": limit,
        "coverage_level": coverage_level,
        "coverage_message": coverage_message,
        "scope": graph["scope"],
        "omitted_nodes": omitted_nodes,
        "boundary_edges": boundary_edges,
        "nodes": [
            {**nodes[key], "distance": positions[key], "focus_fields": sorted(focus_fields.get(key, set()))}
            for key in ordered_nodes
        ],
        "edges": [
            {
                **edge,
                "field_mappings": [
                    mapping for mapping in edge["field_mappings"]
                    if not center_field
                    or mapping["source_field"] in focus_fields.get(edge["source"], set())
                    or mapping["target_field"] in focus_fields.get(edge["target"], set())
                ],
            }
            for edge in visible_edges
        ],
    }
