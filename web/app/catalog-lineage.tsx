"use client";
import { useId, useState } from "react";
import type { CatalogLineageGraph, CatalogLineageNode, CatalogLineageOverview } from "./platform-api";
import styles from "./catalog-browser.module.css";

const grains: Record<string, string> = { dataset: "数据集级", field: "字段级", record: "记录级", definition: "定义级", mixed: "混合输入", consumer: "消费者" };
type NodeView = { id: string; label: string; subtitle: string; column: number; process?: boolean; center?: boolean };
type EdgeView = { id: string; source: string; target: string; label: string };

function Canvas({ nodes, edges, onNode, title }: { nodes: NodeView[]; edges: EdgeView[]; onNode: (id: string) => void; title: string }) {
  const marker = useId().replaceAll(":", "");
  const columns = [...new Set(nodes.map(node => node.column))].sort((a, b) => a - b);
  const rows = columns.map(column => nodes.filter(node => node.column === column));
  const height = Math.max(1, ...rows.map(row => row.length)) * 116 + 60;
  const width = columns.length * 242 + 40;
  const positions = new Map<string, { x: number; y: number }>();
  rows.forEach((row, columnIndex) => row.forEach((node, rowIndex) => positions.set(node.id,
    { x: columnIndex * 242 + 20, y: (height - row.length * 116) / 2 + rowIndex * 116 })));
  return <div className={styles.graphScroll} tabIndex={0} aria-label={`${title}，可横向和纵向滚动`}>
    <svg width={width} height={height} role="group" aria-label={title} data-testid="lineage-svg">
      <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" /></marker></defs>
      {edges.map(edge => {
        const start = positions.get(edge.source), end = positions.get(edge.target);
        if (!start || !end) return null;
        const forward = end.x >= start.x;
        const sx = start.x + (forward ? 196 : 0), ex = end.x + (forward ? 0 : 196);
        const sy = start.y + 46, ey = end.y + 46, middle = (sx + ex) / 2;
        const path = start.x === end.x ? `M ${sx} ${sy} C ${sx + 30} ${sy}, ${sx + 30} ${ey}, ${end.x + 196} ${ey}`
          : `M ${sx} ${sy} C ${middle} ${sy}, ${middle} ${ey}, ${ex} ${ey}`;
        return <path key={edge.id} d={path} className={styles.graphEdge} markerEnd={`url(#${marker})`}><title>{edge.label}</title></path>;
      })}
      {nodes.map(node => {
        const position = positions.get(node.id)!;
        const characters = Array.from(node.label);
        const lines = [characters.slice(0, 13).join(""), characters.slice(13, 26).join("") + (characters.length > 26 ? "…" : "")];
        return <g key={node.id} transform={`translate(${position.x},${position.y})`} role="button" tabIndex={0}
          aria-label={node.label} className={`${styles.graphNode} ${node.process ? styles.processNode : ""} ${node.center ? styles.centerNode : ""}`}
          onClick={() => onNode(node.id)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onNode(node.id); } }} data-lineage-node={node.id}>
          <title>{node.label}</title><rect width="196" height="92" rx="10" />
          <text x="12" y="28">{lines[0]}</text><text x="12" y="48">{lines[1]}</text>
          <text x="12" y="75" className={styles.nodeSubtitle}>{node.subtitle}</text>
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
    <Canvas title="全局分层加工地图" nodes={data.nodes.map((node, index) => ({ id: node.id, label: node.label, column: index,
      subtitle: `${node.members.length} 个节点 · ${node.process_count} 个加工步骤` }))}
      edges={data.edges.map(edge => ({ ...edge, id: `${edge.source}:${edge.target}`, label: `${edge.count} 条跨层依赖` }))} onNode={setGroupId} />
    <p className={styles.graphNote}>{data.coverage}</p>
    <p className={styles.graphNote}>共 {data.registered_edges} 条已登记加工边；同层的 {data.internal_edges} 条关系折叠在分层内，请进入焦点图查看。</p>
    <p className={styles.graphNote} data-testid="field-lineage-coverage">DWD 字段共 {data.field_coverage.total} 个：{data.field_coverage.field_proven} 个已有字段级加工证据，{data.field_coverage.table_context_only} 个仅能展示所属表加工上下文，{data.field_coverage.unregistered} 个连表级上下文也未登记。这里明确区分，不把表级关系冒充字段级血缘。</p>
    {group ? <section className={styles.groupMembers} aria-label={`${group.label}节点`}>
      <h3>{group.label} · 选择一个节点</h3><div>{group.members.map(node => <button type="button" key={node.id} onClick={() => onNavigate(node)}>
        <strong>{node.label}</strong><span>{grains[node.grain] ?? node.grain}</span>
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
      subtitle: `${node.process ? "加工步骤 · " : ""}${grains[node.grain] ?? node.grain}`, column: node.distance ?? 0,
      process: node.process, center: node.id === graph.center }))}
      edges={graph.edges} onNode={id => { const node = graph.nodes.find(item => item.id === id); if (node) onNavigate(node); }} />
    {!graph.edges.length && !graph.table_context ? <p className={styles.graphNote}>当前目录尚未登记此节点的加工关系；不能据此认定它没有上下游。</p> : null}
    {graph.table_context ? <section className={styles.contextPanel} data-testid="table-lineage-context">
      <h3>所属表已证明的加工上下文</h3><p>{graph.table_context.message}</p>
      <Canvas title="所属表加工上下文（非字段映射）" nodes={graph.table_context.nodes.map(node => ({ id: node.id, label: node.label,
        subtitle: `${node.process ? "加工步骤 · " : ""}${grains[node.grain] ?? node.grain}`, column: node.distance ?? 0,
        process: node.process, center: node.id === graph.table_context?.center }))}
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
