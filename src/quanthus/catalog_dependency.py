"""Definition and usage dependency projections, deliberately separate from Data Lineage."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Any, Mapping

DEFINITION_SCHEMA = "quanthus.catalog-definition-dependency.v1"
USAGE_SCHEMA = "quanthus.catalog-usage-dependency.v1"
DEFINITION_KINDS = {"feature_spec", "label_card", "semantic_concept", "dwd_field", "adm_field"}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _entry_node(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item["entry_id"],
        "entry_id": item["entry_id"],
        "label": item["display_name"],
        "technical_id": item["canonical_id"],
        "kind": item["kind"],
        "description": item["description"],
    }


def _collapse_definition_edges(
    entries: Mapping[str, Mapping[str, Any]], legacy: Mapping[str, Any]
) -> list[dict[str, Any]]:
    legacy_nodes = {item["id"]: item for item in legacy["nodes"]}
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in legacy["edges"]:
        incoming[edge["target"]].append(edge)
    kept = {entry_id for entry_id, item in entries.items() if item["kind"] in DEFINITION_KINDS}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for target in sorted(kept):
        if target not in legacy_nodes:
            continue
        queue: deque[tuple[str, tuple[str, ...], tuple[tuple[str, str, str], ...]]] = deque(
            [(target, (), ())]
        )
        visited: set[str] = {target}
        while queue:
            current, transformations, evidence_keys = queue.popleft()
            for edge in incoming[current]:
                source = edge["source"]
                next_transformations = tuple(dict.fromkeys([edge["label"], *transformations]))
                ev = edge["evidence"]
                next_evidence = tuple(dict.fromkeys([
                    (str(ev["path"]), str(ev["locator"]), str(ev["sha256"])),
                    *evidence_keys,
                ]))
                if source in kept:
                    if source == target:
                        continue
                    key = source, target
                    payload = result.get(key)
                    if payload is None:
                        body = {
                            "source": source,
                            "target": target,
                            "transformations": list(next_transformations),
                            "evidence": [
                                {"path": path, "locator": locator, "sha256": sha}
                                for path, locator, sha in next_evidence
                            ],
                        }
                        result[key] = {"id": _digest(body), **body}
                    else:
                        payload["transformations"] = list(dict.fromkeys([
                            *payload["transformations"], *next_transformations
                        ]))
                        existing = {(x["path"], x["locator"], x["sha256"]) for x in payload["evidence"]}
                        payload["evidence"].extend(
                            {"path": path, "locator": locator, "sha256": sha}
                            for path, locator, sha in next_evidence
                            if (path, locator, sha) not in existing
                        )
                    continue
                if source.startswith("consumer:"):
                    continue
                if source in visited:
                    continue
                visited.add(source)
                queue.append((source, next_transformations, next_evidence))
    return sorted(result.values(), key=lambda item: (item["source"], item["target"]))


def build_definition_dependency(
    entries: Mapping[str, Mapping[str, Any]], legacy: Mapping[str, Any]
) -> dict[str, Any]:
    edges = _collapse_definition_edges(entries, legacy)
    participating = {edge[side] for edge in edges for side in ("source", "target")}
    nodes = [_entry_node(entries[key]) for key in sorted(participating)]
    graph = {
        "schema_version": DEFINITION_SCHEMA,
        "scope": "静态计算/语义依赖；过程节点已折叠为边，不代表某次运行已执行。",
        "nodes": nodes,
        "edges": edges,
    }
    validate_dependency_graph(graph)
    return graph


def build_usage_dependency(
    entries: Mapping[str, Mapping[str, Any]],
    legacy: Mapping[str, Any],
    registry: Mapping[str, Any],
    registry_source: Mapping[str, str],
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def add_consumer(raw: Mapping[str, Any]) -> str:
        consumer_id = "consumer:" + str(raw["consumer_id"])
        nodes[consumer_id] = {
            "id": consumer_id,
            "entry_id": None,
            "label": str(raw["display_name"]),
            "technical_id": str(raw["consumer_id"]),
            "kind": "consumer",
            "description": str(raw["description"]),
        }
        return consumer_id

    def add_edge(source: str, target: str, label: str, evidence: list[dict[str, str]]) -> None:
        if source not in nodes and source in entries:
            nodes[source] = _entry_node(entries[source])
        body = {"source": source, "target": target, "transformations": [label], "evidence": evidence}
        edges.setdefault((source, target), {"id": _digest(body), **body})

    for asset_index, asset in enumerate(registry.get("assets", [])):
        source = f"adm_dataset:{asset.get('dataset_id')}"
        if source not in entries:
            continue
        for consumer in asset.get("consumers", []):
            if not isinstance(consumer, Mapping):
                continue
            target = add_consumer(consumer)
            add_edge(
                source,
                target,
                "登记的静态消费者关系",
                [{
                    "path": registry_source["path"],
                    "locator": f"/assets/{asset_index}/consumers",
                    "sha256": registry_source["sha256"],
                }],
            )

    legacy_nodes = {item["id"]: item for item in legacy["nodes"]}
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in legacy["edges"]:
        incoming[edge["target"]].append(edge)
    kept_sources = {
        entry_id for entry_id, item in entries.items()
        if item["kind"] in {"feature_spec", "label_card", "semantic_concept", "adm_dataset", "adm_field", "dwd_field"}
    }
    for consumer_id, consumer_node in sorted(legacy_nodes.items()):
        if not consumer_id.startswith("consumer:"):
            continue
        nodes.setdefault(consumer_id, {
            "id": consumer_id,
            "entry_id": None,
            "label": consumer_node["label"],
            "technical_id": consumer_node.get("technical_id") or consumer_id.split(":", 1)[-1],
            "kind": "consumer",
            "description": consumer_node["description"],
        })
        queue = deque([(consumer_id, (), ())])
        visited = {consumer_id}
        while queue:
            current, transformations, evidence_keys = queue.popleft()
            for edge in incoming[current]:
                source = edge["source"]
                ev = edge["evidence"]
                next_transformations = tuple(dict.fromkeys([edge["label"], *transformations]))
                next_evidence = tuple(dict.fromkeys([
                    (str(ev["path"]), str(ev["locator"]), str(ev["sha256"])),
                    *evidence_keys,
                ]))
                if source in kept_sources:
                    add_edge(
                        source,
                        consumer_id,
                        " / ".join(next_transformations),
                        [{"path": p, "locator": l, "sha256": s} for p, l, s in next_evidence],
                    )
                    continue
                if source in visited:
                    continue
                visited.add(source)
                queue.append((source, next_transformations, next_evidence))

    graph = {
        "schema_version": USAGE_SCHEMA,
        "scope": "静态登记使用关系；不等于某个历史 Job 的实际消费记录。",
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": sorted(edges.values(), key=lambda item: (item["source"], item["target"])),
    }
    validate_dependency_graph(graph)
    return graph


def validate_dependency_graph(graph: Mapping[str, Any]) -> None:
    if graph.get("schema_version") not in {DEFINITION_SCHEMA, USAGE_SCHEMA}:
        raise ValueError("dependency graph schema differs")
    nodes = {item["id"]: item for item in graph.get("nodes", [])}
    if len(nodes) != len(graph.get("nodes", [])):
        raise ValueError("duplicate dependency node")
    for node in nodes.values():
        if str(node["id"]).startswith(("process:", "projection:", "source-field:")):
            raise ValueError("implementation node leaked into dependency graph")
    for edge in graph.get("edges", []):
        if edge.get("source") not in nodes or edge.get("target") not in nodes:
            raise ValueError("dependency graph has dangling endpoint")
        if not isinstance(edge.get("transformations"), list) or not edge["transformations"]:
            raise ValueError("dependency edge lacks transformation")
        evidence = edge.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("dependency edge lacks evidence")
        if any(not str(item.get("sha256", "")).startswith("sha256:") for item in evidence):
            raise ValueError("dependency evidence digest is invalid")


def focus(
    graph: Mapping[str, Any],
    center: str,
    *,
    scope: str = "full",
    depth: int = 2,
    limit: int = 100,
) -> dict[str, Any] | None:
    nodes = {item["id"]: item for item in graph["nodes"]}
    if center not in nodes:
        return None
    if scope not in {"full", "focus"} or not 1 <= depth <= 5 or not 10 <= limit <= 100:
        raise ValueError("catalog_dependency_bounds_invalid")
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge)
        outgoing[edge["source"]].append(edge)
    max_depth = None if scope == "full" else depth
    positions = {center: 0}
    used: set[str] = set()
    boundary: set[str] = set()
    for adjacency, sign in ((incoming, -1), (outgoing, 1)):
        frontier = {center}
        visited = {center}
        distance = 0
        while frontier and (max_depth is None or distance < max_depth):
            distance += 1
            next_frontier: set[str] = set()
            for node in frontier:
                for edge in adjacency[node]:
                    used.add(edge["id"])
                    target = edge["source"] if sign < 0 else edge["target"]
                    if target not in visited:
                        next_frontier.add(target)
                        positions.setdefault(target, sign * distance)
            visited.update(next_frontier)
            frontier = next_frontier
        if max_depth is not None:
            for node in frontier:
                for edge in adjacency[node]:
                    target = edge["source"] if sign < 0 else edge["target"]
                    if target not in visited:
                        boundary.add(edge["id"])
    ordered = sorted(positions, key=lambda key: (abs(positions[key]), positions[key], key))
    selected_order = ordered[:limit]
    selected = set(selected_order)
    relevant = [edge for edge in graph["edges"] if edge["id"] in used]
    visible = [edge for edge in relevant if edge["source"] in selected and edge["target"] in selected]
    budget_boundary = sum((edge["source"] in selected) != (edge["target"] in selected) for edge in relevant)
    return {
        "schema_version": graph["schema_version"],
        "scope": graph["scope"],
        "scope_mode": scope,
        "center": center,
        "depth": depth,
        "limit": limit,
        "omitted_nodes": max(0, len(ordered) - len(selected_order)),
        "boundary_edges": len(boundary) + budget_boundary,
        "nodes": [{**nodes[key], "distance": positions[key]} for key in selected_order],
        "edges": visible,
    }
