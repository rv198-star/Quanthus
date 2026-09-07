"use client";
import { useId, useState } from "react";
import type { CatalogLineageGraph, CatalogLineageNode, CatalogLineageOverview } from "./platform-api";
import styles from "./catalog-browser.module.css";

const grains: Record<string, string> = { dataset: "数据集级", field: "字段级", record: "记录级", definition: "定义级", mixed: "混合输入", consumer: "消费者" };
type NodeView = { id: string; label: string; technicalId: string; meta: string; column: number; process?: boolean; center?: boolean };
type EdgeView = { id: string; source: string; target: string; label: string };

function wrapText(value: string, width: number, maximum: number) {
  const characters = Array.from(value);
  const lines = Array.from({ length: maximum }, (_, index) => characters.slice(index * width, (index + 1) * width).join(""));
  if (characters.length > width * maximum) lines[maximum - 1] = `${lines[maximum - 1].slice(0, -1)}…`;
  return lines;
}

function Canvas({ nodes, edges, onNode, title }: { nodes: NodeView[]; edges: EdgeView[]; onNode: (id: string) => void; title: string }) {
  const marker = useId().replaceAll(":", "");
  const nodeWidth = 268, nodeHeight = 124, columnPitch = 326, rowPitch = 148;
  const columns = [...new Set(nodes.map(node => node.column))].sort((a, b) => a - b);
  const rows = columns.map(column => nodes.filter(node => node.column === column));
  const height = Math.max(1, ...rows.map(row => row.length)) * rowPitch + 52;
  const width = columns.length * columnPitch + 28;
  const positions = new Map<string, { x: number; y: number }>();
  rows.forEach((row, columnIndex) => row.forEach((node, rowIndex) => positions.set(node.id,
    { x: columnIndex * columnPitch + 14, y: (height - row.length * rowPitch) / 2 + rowIndex * rowPitch })));
  return <div className={styles.graphScroll} tabIndex={0} aria-label={`${title}，可横向和纵向滚动`}>
    <svg width={width} height={height} role="group" aria-label={title} data-testid="lineage-svg">
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
          aria-label={`${node.label}，${node.technicalId}`} className={`${styles.graphNode} ${node.process ? styles.processNode : ""} ${node.center ? styles.centerNode : ""}`}
          onClick={() => onNode(node.id)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onNode(node.id); } }} data-lineage-node={node.id}>
          <title>{node.label} · {node.technicalId}</title><rect width={nodeWidth} height={nodeHeight} rx="10" />
          <text x="14" y="25">{labelLines[0]}</text><text x="14" y="44">{labelLines[1]}</text>
          <text x="14" y="69" className={styles.nodeTechnical}>{idLines[0]}</text><text x="14" y="85" className={styles.nodeTechnical}>{idLines[1]}</text>
          <text x="14" y="108" className={styles.nodeSubtitle}>{node.meta}</text>
        </g>;
      })}
    </svg>
  </div>;
}

export function LineageOverview({ data, onNavigate }: { data: CatalogLineageOverview; onNavigate: (node: CatalogLineageNode) => void }) {
  const [groupId, setGroupId] = useState<string | null>(null);
  const group = data.nodes.find(node => node.id === groupId);
  return <section className={styles.mapSection} data-testid="lineage-overview">
    <h2>数据从哪里来，又流向哪里？</h2><p>点击分层查看已登记节点，再进入具体字段。箭头统一从输入指向输出。</p>
    <Canvas title="全局分层加工地图" nodes={data.nodes.map((node, index) => ({ id: node.id, label: node.label,
      technicalId: `layer:${node.id}`, meta: `${node.members.length} 个节点 · ${node.process_count} 个加工步骤`, column: index }))}
      edges={data.edges.map(edge => ({ ...edge, id: `${edge.source}:${edge.target}`, label: `${edge.count} 条跨层依赖` }))} onNode={setGroupId} />
    <p className={styles.graphNote}>{data.coverage}</p>
    <p className={styles.graphNote}>共 {data.registered_edges} 条已登记加工边；同层的 {data.internal_edges} 条关系折叠在分层内，请进入焦点图查看。</p>
    <p className={styles.graphNote} data-testid="field-lineage-coverage">DWD 字段共 {data.field_coverage.total} 个：{data.field_coverage.field_proven} 个已有字段级加工证据，{data.field_coverage.table_context_only} 个仅能展示所属表加工上下文，{data.field_coverage.unregistered} 个连表级上下文也未登记。这里明确区分，不把表级关系冒充字段级血缘。</p>
    {group ? <section className={styles.groupMembers} aria-label={`${group.label}节点`}>
      <h3>{group.label} · 选择一个节点</h3><div>{group.members.map(node => <button type="button" key={node.id} onClick={() => onNavigate(node)}>
        <strong>{node.label}</strong><code>{node.technical_id}</code><span>{grains[node.grain] ?? node.grain}</span>
      </button>)}</div>
    </section> : null}
  </section>;
}

export function LineageFocus({ graph, onNavigate, depth, onDepth, busy }: {
  graph: CatalogLineageGraph; onNavigate: (node: CatalogLineageNode) => void; depth: number; onDepth: (depth: number) => void; busy: boolean;
}) {
  const [edgeId, setEdgeId] = useState<string | null>(null);
  const center = graph.nodes.find(node => node.id === graph.center);
  const evidence = graph.edges.find(edge => edge.id === edgeId);
  const names = new Map(graph.nodes.map(node => [node.id, node.label]));
  return <section className={styles.mapSection} aria-busy={busy} data-testid="lineage-focus">
    <div className={styles.sectionHeading}><div><h2>上游来源与下游使用</h2><p>当前焦点：{center?.label}。点击节点继续追查，下方可查看每条关系的加工依据。</p></div>
      <label>展开范围 <select aria-label="血缘展开层数" value={depth} onChange={event => onDepth(Number(event.target.value))}>
        {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value} 跳</option>)}
      </select></label>
    </div>
    {busy ? <p role="status">正在读取血缘…</p> : null}
    <div className={styles.coverageNotice} data-level={graph.coverage_level} data-testid="lineage-coverage-level">
      <strong>{graph.coverage_level === "field_proven" ? "字段级血缘已证明" : graph.coverage_level === "table_context_only" ? "当前仅有表级加工上下文" : "已登记加工关系"}</strong>
      <p>{graph.coverage_message}</p>
    </div>
    <Canvas title="字段双向血缘图" nodes={graph.nodes.map(node => ({ id: node.id, label: node.label,
      technicalId: node.technical_id, meta: `${node.process ? "加工步骤 · " : ""}${grains[node.grain] ?? node.grain}`,
      column: node.distance ?? 0, process: node.process, center: node.id === graph.center }))}
      edges={graph.edges} onNode={id => { const node = graph.nodes.find(item => item.id === id); if (node) onNavigate(node); }} />
    {!graph.edges.length && !graph.table_context ? <p className={styles.graphNote}>当前目录尚未登记此节点的加工关系；不能据此认定它没有上下游。</p> : null}
    {graph.table_context ? <section className={styles.contextPanel} data-testid="table-lineage-context">
      <h3>所属表已证明的加工上下文</h3><p>{graph.table_context.message}</p>
      <Canvas title="所属表加工上下文（非字段映射）" nodes={graph.table_context.nodes.map(node => ({ id: node.id, label: node.label,
        technicalId: node.technical_id, meta: `${node.process ? "加工步骤 · " : ""}${grains[node.grain] ?? node.grain}`,
        column: node.distance ?? 0, process: node.process, center: node.id === graph.table_context?.center }))}
        edges={graph.table_context.edges} onNode={id => { const node = graph.table_context?.nodes.find(item => item.id === id); if (node) onNavigate(node); }} />
      <p className={styles.graphNote}>此图只证明所属表参与这些加工链；没有字段级证据时，不能推断当前字段参与其中每一条关系。</p>
    </section> : null}
    <p className={styles.graphNote}>实线箭头表示已登记加工依赖；归属和概念关联不进入主图。当前字段图显示 {graph.nodes.length} 个节点、{graph.edges.length} 条边。</p>
    {graph.boundary_edges || graph.omitted_nodes ? <p className={styles.graphNote} data-testid="lineage-boundary">
      受展开层数和节点预算限制，{graph.boundary_edges} 条边连接到当前视图之外；另有 {graph.omitted_nodes} 个范围内节点未显示。可增加层数或点击节点继续展开。
    </p> : null}
    <p className={styles.graphNote}>{graph.scope}。{graph.coverage}</p>
    {center && !center.entry_id ? <div className={styles.explanation}><strong>{center.label}</strong><p>{center.description}</p>
      {center.evidence ? <details><summary>该节点的定义依据</summary><code>{center.evidence.path} · {center.evidence.locator}</code><p>{center.evidence.sha256}</p></details> : null}
    </div> : null}
    <details className={styles.technical}><summary>以列表阅读关系与加工依据</summary><ul>{graph.edges.map(edge => <li key={edge.id}>
      <button type="button" onClick={() => setEdgeId(edge.id)}>{names.get(edge.source)} → {names.get(edge.target)}：{edge.label}</button>
    </li>)}</ul></details>
    {evidence ? <div className={styles.explanation} data-testid="lineage-evidence">
      <strong>{names.get(evidence.source)} → {names.get(evidence.target)}</strong><p>{evidence.label} · {grains[evidence.grain]}</p>
      <p>加工定义依据（非运行回执）</p><code>{evidence.evidence.path} · {evidence.evidence.locator}</code><p>{evidence.evidence.sha256}</p>
      <button type="button" onClick={() => setEdgeId(null)}>收起依据</button>
    </div> : null}
  </section>;
}
