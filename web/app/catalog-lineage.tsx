"use client";
import { useEffect, useId, useRef, useState } from "react";
import type {
  CatalogAssetLineageOverview,
  DataCatalogRelations,
} from "./platform-api";
import styles from "./catalog-browser.module.css";

type NodeView = { id: string; label: string; technicalId: string; meta: string; column: number; center?: boolean };
type EdgeView = { id: string; source: string; target: string; label: string };

function wrapText(value: string, width: number, maximum: number) {
  const characters = Array.from(value);
  const lines = Array.from({ length: maximum }, (_, index) => characters.slice(index * width, (index + 1) * width).join(""));
  if (characters.length > width * maximum) lines[maximum - 1] = `${lines[maximum - 1].slice(0, -1)}…`;
  return lines;
}

function Canvas({ nodes, edges, onNode, title }: { nodes: NodeView[]; edges: EdgeView[]; onNode: (id: string) => void; title: string }) {
  const marker = useId().replaceAll(":", "");
  const viewportRef = useRef<HTMLDivElement>(null);
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const gestureRef = useRef<
    | { mode: "pan"; pointerId: number; startX: number; startY: number; originX: number; originY: number }
    | { mode: "pinch"; distance: number; zoom: number; worldX: number; worldY: number }
    | null
  >(null);
  const miniMapPointerRef = useRef<number | null>(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const viewportStateRef = useRef(viewport);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [grabbing, setGrabbing] = useState(false);
  const nodeWidth = 268, nodeHeight = 124, columnPitch = 326, rowPitch = 148;
  const minZoom = 0.35, maxZoom = 2.2, zoomStep = 0.15, fitPadding = 42;
  const columns = [...new Set(nodes.map(node => node.column))].sort((a, b) => a - b);
  const rows = columns.map(column => nodes.filter(node => node.column === column));
  const worldHeight = Math.max(1, ...rows.map(row => row.length)) * rowPitch + 52;
  const worldWidth = columns.length * columnPitch + 28;
  const positions = new Map<string, { x: number; y: number }>();
  rows.forEach((row, columnIndex) => row.forEach((node, rowIndex) => positions.set(node.id,
    { x: columnIndex * columnPitch + 14, y: (worldHeight - row.length * rowPitch) / 2 + rowIndex * rowPitch })));
  const focusNode = nodes.find(node => node.center);
  const geometryKey = `${worldWidth}:${worldHeight}:${nodes.map(node => `${node.id}@${node.column}`).join("|")}`;
  const canvasReady = canvasSize.width > 0 && canvasSize.height > 0;

  function clampZoom(value: number) {
    return Math.min(maxZoom, Math.max(minZoom, value));
  }

  function applyViewport(next: { x: number; y: number; zoom: number }) {
    viewportStateRef.current = next;
    setViewport(next);
  }

  function centerOn(worldX: number, worldY: number, zoom = viewportStateRef.current.zoom) {
    if (!canvasReady) return;
    applyViewport({
      x: canvasSize.width / 2 - worldX * zoom,
      y: canvasSize.height / 2 - worldY * zoom,
      zoom,
    });
  }

  function fitView() {
    if (!canvasReady) return;
    const nextZoom = clampZoom(Math.min(
      1,
      Math.max(1, canvasSize.width - fitPadding * 2) / worldWidth,
      Math.max(1, canvasSize.height - fitPadding * 2) / worldHeight,
    ));
    applyViewport({
      x: (canvasSize.width - worldWidth * nextZoom) / 2,
      y: (canvasSize.height - worldHeight * nextZoom) / 2,
      zoom: nextZoom,
    });
  }

  function zoomAt(rawZoom: number, screenX = canvasSize.width / 2, screenY = canvasSize.height / 2) {
    if (!canvasReady) return;
    const current = viewportStateRef.current;
    const nextZoom = clampZoom(rawZoom);
    if (Math.abs(nextZoom - current.zoom) < 0.0001) return;
    const worldX = (screenX - current.x) / current.zoom;
    const worldY = (screenY - current.y) / current.zoom;
    applyViewport({ x: screenX - worldX * nextZoom, y: screenY - worldY * nextZoom, zoom: nextZoom });
  }

  function centerView() {
    const position = focusNode ? positions.get(focusNode.id) : undefined;
    centerOn(
      position ? position.x + nodeWidth / 2 : worldWidth / 2,
      position ? position.y + nodeHeight / 2 : worldHeight / 2,
    );
  }

  function miniMapCenter(clientX: number, clientY: number, element: SVGSVGElement) {
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const scale = Math.min(rect.width / worldWidth, rect.height / worldHeight);
    const offsetX = (rect.width - worldWidth * scale) / 2;
    const offsetY = (rect.height - worldHeight * scale) / 2;
    const worldX = Math.max(0, Math.min(worldWidth, (clientX - rect.left - offsetX) / scale));
    const worldY = Math.max(0, Math.min(worldHeight, (clientY - rect.top - offsetY) / scale));
    centerOn(worldX, worldY);
  }

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const update = () => setCanvasSize({ width: element.clientWidth, height: element.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!canvasReady) return;
    const frame = requestAnimationFrame(fitView);
    return () => cancelAnimationFrame(frame);
    // Geometry changes intentionally reset the viewport to an actual centered fitView.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometryKey, canvasReady]);

  const current = viewport;
  const dotPitch = Math.max(7, 18 * current.zoom);
  const visibleWorld = canvasReady ? {
    x: -current.x / current.zoom,
    y: -current.y / current.zoom,
    width: canvasSize.width / current.zoom,
    height: canvasSize.height / current.zoom,
  } : { x: 0, y: 0, width: worldWidth, height: worldHeight };

  return <div className={styles.graphFrame} data-testid="lineage-canvas">
    <div ref={viewportRef} className={styles.graphViewport} tabIndex={0} data-testid="lineage-viewport"
      data-grabbing={grabbing ? "true" : "false"}
      aria-label={`${title}，无限画布；拖动平移，滚轮或双指缩放，左下角提供视图控制`}
      style={{
        backgroundSize: `${dotPitch}px ${dotPitch}px`,
        backgroundPosition: `${current.x % dotPitch}px ${current.y % dotPitch}px`,
      }}
      onWheel={event => {
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        const factor = Math.exp(-event.deltaY * 0.0015);
        zoomAt(viewportStateRef.current.zoom * factor, event.clientX - rect.left, event.clientY - rect.top);
      }}
      onPointerDown={event => {
        if (event.button !== 0 || (event.target as Element).closest("[data-lineage-node]")) return;
        event.preventDefault();
        const element = event.currentTarget;
        const rect = element.getBoundingClientRect();
        pointersRef.current.set(event.pointerId, { x: event.clientX - rect.left, y: event.clientY - rect.top });
        element.setPointerCapture(event.pointerId);
        const points = [...pointersRef.current.values()];
        if (points.length === 1) {
          const view = viewportStateRef.current;
          gestureRef.current = { mode: "pan", pointerId: event.pointerId, startX: points[0].x, startY: points[0].y,
            originX: view.x, originY: view.y };
        } else if (points.length >= 2) {
          const [a, b] = points;
          const centerX = (a.x + b.x) / 2, centerY = (a.y + b.y) / 2;
          const distance = Math.hypot(a.x - b.x, a.y - b.y);
          const view = viewportStateRef.current;
          gestureRef.current = { mode: "pinch", distance: Math.max(1, distance), zoom: view.zoom,
            worldX: (centerX - view.x) / view.zoom, worldY: (centerY - view.y) / view.zoom };
        }
        setGrabbing(true);
      }}
      onPointerMove={event => {
        if (!pointersRef.current.has(event.pointerId)) return;
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        pointersRef.current.set(event.pointerId, { x: event.clientX - rect.left, y: event.clientY - rect.top });
        const points = [...pointersRef.current.values()];
        const gesture = gestureRef.current;
        if (points.length >= 2 && gesture?.mode === "pinch") {
          const [a, b] = points;
          const centerX = (a.x + b.x) / 2, centerY = (a.y + b.y) / 2;
          const nextZoom = clampZoom(gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance);
          applyViewport({ x: centerX - gesture.worldX * nextZoom, y: centerY - gesture.worldY * nextZoom, zoom: nextZoom });
        } else if (points.length === 1 && gesture?.mode === "pan") {
          const point = points[0];
          applyViewport({ x: gesture.originX + point.x - gesture.startX, y: gesture.originY + point.y - gesture.startY,
            zoom: viewportStateRef.current.zoom });
        }
      }}
      onPointerUp={event => {
        pointersRef.current.delete(event.pointerId);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        const remaining = [...pointersRef.current.entries()];
        if (remaining.length === 1) {
          const [pointerId, point] = remaining[0];
          const view = viewportStateRef.current;
          gestureRef.current = { mode: "pan", pointerId, startX: point.x, startY: point.y, originX: view.x, originY: view.y };
        } else {
          gestureRef.current = null;
        }
        setGrabbing(remaining.length > 0);
      }}
      onPointerCancel={event => {
        pointersRef.current.delete(event.pointerId);
        gestureRef.current = null;
        setGrabbing(false);
      }}>
      <svg width={worldWidth} height={worldHeight} className={styles.graphWorld}
        style={{ transform: `translate(${current.x}px, ${current.y}px) scale(${current.zoom})` }}
        role="group" aria-label={title} data-testid="lineage-svg" data-viewport-x={Math.round(current.x)}
        data-viewport-y={Math.round(current.y)} data-viewport-zoom={current.zoom.toFixed(3)}>
        <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" /></marker></defs>
        {edges.map(edge => {
          const start = positions.get(edge.source), end = positions.get(edge.target);
          if (!start || !end) return null;
          const forward = end.x >= start.x;
          const sx = start.x + (forward ? nodeWidth : 0), ex = end.x + (forward ? 0 : nodeWidth);
          const sy = start.y + nodeHeight / 2, ey = end.y + nodeHeight / 2, middle = (sx + ex) / 2;
          const path = start.x === end.x ? `M ${sx} ${sy} C ${sx + 34} ${sy}, ${sx + 34} ${ey}, ${end.x + nodeWidth} ${ey}`
            : `M ${sx} ${sy} C ${middle} ${sy}, ${middle} ${ey}, ${ex} ${ey}`;
          return <path key={edge.id} d={path} className={styles.graphEdge} markerEnd={`url(#${marker})`}><title>{edge.label}</title></path>;
        })}
        {nodes.map(node => {
          const position = positions.get(node.id)!;
          const labelLines = wrapText(node.label, 16, 2);
          const idLines = wrapText(node.technicalId, 34, 2);
          return <g key={node.id} transform={`translate(${position.x},${position.y})`} role="button" tabIndex={0}
            aria-label={`${node.label}，${node.technicalId}`} className={`${styles.graphNode} ${node.center ? styles.centerNode : ""}`}
            onClick={() => onNode(node.id)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onNode(node.id); } }} data-lineage-node={node.id}>
            <title>{node.label} · {node.technicalId}</title><rect width={nodeWidth} height={nodeHeight} rx="10" />
            <text x="14" y="25">{labelLines[0]}</text><text x="14" y="44">{labelLines[1]}</text>
            <text x="14" y="69" className={styles.nodeTechnical}>{idLines[0]}</text><text x="14" y="85" className={styles.nodeTechnical}>{idLines[1]}</text>
            <text x="14" y="108" className={styles.nodeSubtitle}>{node.meta}</text>
          </g>;
        })}
      </svg>
    </div>

    <div className={styles.graphControls} role="group" aria-label={`${title}视图控制`} data-testid="lineage-controls">
      <span className={styles.zoomBadge} data-testid="lineage-zoom">{Math.round(current.zoom * 100)}%</span>
      <button type="button" aria-label="放大血缘图" title="放大" disabled={current.zoom >= maxZoom}
        onClick={() => zoomAt(current.zoom + zoomStep)}>+</button>
      <button type="button" aria-label="缩小血缘图" title="缩小" disabled={current.zoom <= minZoom}
        onClick={() => zoomAt(current.zoom - zoomStep)}>−</button>
      <button type="button" aria-label="适配并居中全部血缘图" title="适配全部" onClick={fitView}>适</button>
      <button type="button" aria-label="居中当前焦点" title="居中当前焦点" onClick={centerView}>◎</button>
      <button type="button" aria-label="恢复100%缩放" title="恢复 100%" onClick={() => zoomAt(1)}>1:1</button>
    </div>

    <div className={styles.graphMiniMap} aria-label={`${title}小地图`} data-testid="lineage-minimap">
      <svg viewBox={`0 0 ${worldWidth} ${worldHeight}`} preserveAspectRatio="xMidYMid meet" role="img"
        onPointerDown={event => {
          event.preventDefault(); event.stopPropagation();
          miniMapPointerRef.current = event.pointerId;
          event.currentTarget.setPointerCapture(event.pointerId);
          miniMapCenter(event.clientX, event.clientY, event.currentTarget);
        }}
        onPointerMove={event => {
          if (miniMapPointerRef.current !== event.pointerId) return;
          miniMapCenter(event.clientX, event.clientY, event.currentTarget);
        }}
        onPointerUp={event => {
          if (miniMapPointerRef.current === event.pointerId) miniMapPointerRef.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}>
        {nodes.map(node => {
          const position = positions.get(node.id)!;
          return <rect key={node.id} x={position.x} y={position.y} width={nodeWidth} height={nodeHeight} rx="8"
            className={`${styles.miniMapNode} ${node.center ? styles.miniMapCenter : ""}`} />;
        })}
        <rect x={visibleWorld.x} y={visibleWorld.y} width={visibleWorld.width} height={visibleWorld.height}
          className={styles.miniMapViewport} />
      </svg>
    </div>
  </div>;
}

export function LineageOverview({ data }: { data: CatalogAssetLineageOverview }) {
  return <section className={styles.mapSection} data-testid="lineage-overview">
    <h2>正式数据资产全景</h2>
    <p>主血缘只展示受治理数据资产：ODS → DWD → Research ADM。加工过程、临时投影、指标定义和消费者不会成为这里的节点。</p>
    <Canvas
      title="ODS DWD Research ADM 正式资产分层图"
      nodes={data.layers.map((layer, index) => ({
        id: layer.id,
        label: layer.label,
        technicalId: `layer:${layer.id}`,
        meta: `${layer.participating_asset_count}/${layer.registered_asset_count} 个资产已接入血缘`,
        column: index,
      }))}
      edges={data.edges.map(edge => ({ ...edge, id: `${edge.source}:${edge.target}`, label: `${edge.count} 条资产血缘` }))}
      onNode={() => undefined}
    />
    <p className={styles.graphMeta}>{data.participating_asset_count}/{data.registered_asset_count} 个正式资产已接入血缘 · {data.edge_count} 条资产关系</p>
    <p className={styles.graphNote}>{data.scope}</p>
  </section>;
}

type RangeValue = "full" | "1" | "2" | "3" | "5";
type RelationView = "data" | "definition" | "usage";
type EvidenceView = {
  id: string;
  source: string;
  target: string;
  transformations: string[];
  fieldMappings: Array<{ source_field: string; target_field: string }>;
  evidence: Array<{ path: string; locator: string; sha256: string }>;
  notes: string[];
};

const assetLayers: Record<string, string> = { ods: "ODS", dwd: "DWD", adm: "Research ADM" };
const dependencyKinds: Record<string, string> = {
  dwd_field: "DWD 字段", adm_field: "ADM 字段", feature_spec: "指标规格",
  label_card: "规则标签", semantic_concept: "语义定义", adm_dataset: "ADM 数据资产", consumer: "消费者",
};

export function LineageFocus({ relations, onNavigate, range, onRange, busy }: {
  relations: DataCatalogRelations;
  onNavigate: (node: { entry_id: string | null }) => void;
  range: RangeValue;
  onRange: (range: RangeValue) => void;
  busy: boolean;
}) {
  const [view, setView] = useState<RelationView>(relations.asset_lineage ? "data" : relations.definition_dependency ? "definition" : "usage");
  const [edgeId, setEdgeId] = useState<string | null>(null);

  let nodes: NodeView[] = [];
  let edges: EdgeView[] = [];
  let scope = "";
  let statusTitle = "";
  let statusMessage = "";
  let omittedNodes = 0;
  let boundaryEdges = 0;
  let normalizedEvidence: EvidenceView[] = [];
  let sourceNodes: Array<{ id: string; entry_id: string | null; label: string }> = [];

  if (view === "data" && relations.asset_lineage) {
    const graph = relations.asset_lineage;
    scope = graph.scope;
    omittedNodes = graph.omitted_nodes;
    boundaryEdges = graph.boundary_edges;
    statusTitle = graph.coverage_level === "field_proven" ? "字段全链已登记"
      : graph.coverage_level === "asset_context_only" ? "仅资产级上下文"
      : graph.scope_mode === "full" ? "完整资产全链" : "资产聚焦视图";
    statusMessage = graph.coverage_message;
    nodes = graph.nodes.map(node => ({
      id: node.id,
      label: node.label,
      technicalId: node.technical_id,
      meta: `${assetLayers[node.layer] ?? node.layer}${node.focus_fields.length ? ` · 字段：${node.focus_fields.slice(0, 2).join(" / ")}${node.focus_fields.length > 2 ? ` +${node.focus_fields.length - 2}` : ""}` : " · 数据资产"}`,
      column: node.distance,
      center: node.id === graph.center,
    }));
    edges = graph.edges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.transformations.join(" / "),
    }));
    normalizedEvidence = graph.edges.map(edge => ({
      id: edge.id, source: edge.source, target: edge.target,
      transformations: edge.transformations, fieldMappings: edge.field_mappings,
      evidence: edge.evidence, notes: edge.notes,
    }));
    sourceNodes = graph.nodes.map(node => ({ id: node.id, entry_id: node.entry_id, label: node.label }));
  } else if (view !== "data") {
    const graph = view === "definition" ? relations.definition_dependency : relations.usage_dependency;
    if (graph) {
      scope = graph.scope;
      omittedNodes = graph.omitted_nodes;
      boundaryEdges = graph.boundary_edges;
      statusTitle = view === "definition" ? "计算 / 语义依赖" : "静态使用关系";
      statusMessage = view === "definition"
        ? "指标、Fact、Label 等定义在此视图出现；加工步骤已折叠到箭头，不属于数据资产血缘。"
        : "消费者只在使用关系中出现；静态登记不等于某次 Job 实际使用。";
      nodes = graph.nodes.map(node => ({
        id: node.id,
        label: node.label,
        technicalId: node.technical_id,
        meta: dependencyKinds[node.kind] ?? node.kind,
        column: node.distance,
        center: node.id === graph.center,
      }));
      edges = graph.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, label: edge.transformations.join(" / ") }));
      normalizedEvidence = graph.edges.map(edge => ({
        id: edge.id, source: edge.source, target: edge.target,
        transformations: edge.transformations, fieldMappings: [], evidence: edge.evidence, notes: [],
      }));
      sourceNodes = graph.nodes.map(node => ({ id: node.id, entry_id: node.entry_id, label: node.label }));
    }
  }

  const evidence = normalizedEvidence.find(edge => edge.id === edgeId);
  const names = new Map(sourceNodes.map(node => [node.id, node.label]));
  const noGraphMessage = view === "data"
    ? "当前对象不是已登记正式数据资产；数据血缘只接受 ODS、DWD 和 Research ADM。"
    : view === "definition" ? "当前对象尚未登记计算 / 语义依赖。" : "当前对象尚未登记静态使用关系。";

  return <section className={styles.mapSection} aria-busy={busy} data-testid="lineage-focus">
    <div className={styles.sectionHeading}>
      <div><h2>关系图</h2><p>数据血缘、计算依赖和使用关系分开查看，不再混成一张工程图。</p></div>
      <label>查看范围 <select aria-label="血缘查看范围" value={range} onChange={event => onRange(event.target.value as RangeValue)}>
        <option value="full">全链</option><option value="1">1 跳</option><option value="2">2 跳</option><option value="3">3 跳</option><option value="5">5 跳</option>
      </select></label>
    </div>
    <div className={styles.relationTabs} role="tablist" aria-label="关系类型">
      <button type="button" role="tab" aria-selected={view === "data"} onClick={() => { setView("data"); setEdgeId(null); }}>数据血缘</button>
      <button type="button" role="tab" aria-selected={view === "definition"} onClick={() => { setView("definition"); setEdgeId(null); }}>计算依赖</button>
      <button type="button" role="tab" aria-selected={view === "usage"} onClick={() => { setView("usage"); setEdgeId(null); }}>使用关系</button>
    </div>
    {busy ? <p role="status">正在读取关系…</p> : null}
    <div className={styles.lineageStatus} data-testid="lineage-coverage-level">
      <strong>{nodes.length ? statusTitle : "暂无该类关系"}</strong><span>{nodes.length ? statusMessage : noGraphMessage}</span>
    </div>
    {nodes.length ? <Canvas
      title={view === "data" ? "正式数据资产血缘" : view === "definition" ? "计算与语义依赖" : "静态使用关系"}
      nodes={nodes}
      edges={edges}
      onNode={id => { const node = sourceNodes.find(item => item.id === id); if (node?.entry_id) onNavigate(node); }}
    /> : null}
    {nodes.length ? <p className={styles.graphMeta}>{nodes.length} 个节点 · {edges.length} 条关系 · {range === "full" ? "全链" : `${range} 跳`}</p> : null}
    {nodes.length ? <details className={styles.technical}><summary>范围、限制与关系依据</summary>
      {boundaryEdges || omittedNodes ? <p className={styles.graphNote} data-testid="lineage-boundary">当前视图还有 {boundaryEdges} 条边越过边界；{omittedNodes} 个闭包内节点因预算未显示。</p> : null}
      <p className={styles.graphNote}>{scope}</p>
      <ul>{normalizedEvidence.map(edge => <li key={edge.id}><button type="button" onClick={() => setEdgeId(edge.id)}>
        {names.get(edge.source)} → {names.get(edge.target)}：{edge.transformations.join(" / ")}
      </button></li>)}</ul>
    </details> : null}
    {evidence ? <div className={styles.explanation} data-testid="lineage-evidence">
      <strong>{names.get(evidence.source)} → {names.get(evidence.target)}</strong>
      <p>{evidence.transformations.join("；")}</p>
      {evidence.fieldMappings.length ? <><p>精确字段映射</p><ul>{evidence.fieldMappings.map(mapping => <li key={`${mapping.source_field}:${mapping.target_field}`}><code>{mapping.source_field} → {mapping.target_field}</code></li>)}</ul></> : null}
      {evidence.notes.map(note => <p key={note}>{note}</p>)}
      <p>定义依据（非运行回执）</p>{evidence.evidence.map(item => <p key={`${item.path}:${item.locator}`}><code>{item.path} · {item.locator}</code><br /><code>{item.sha256}</code></p>)}
      <button type="button" onClick={() => setEdgeId(null)}>收起依据</button>
    </div> : null}
  </section>;
}
