"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { BookOpen, Copy, Database, GitBranch, LoaderCircle, Search } from "lucide-react";
import { LineageFocus, LineageOverview } from "./catalog-lineage";
import {
  clearStoredSession, loadStoredSession, PlatformApiError, platformRequest,
  type CatalogEntry, type CatalogEntrySummary, type CatalogKind,
  type DataCatalogRelations, type DataCatalogSearchResult, type DataCatalogSummary,
} from "./platform-api";
import styles from "./catalog-browser.module.css";

const kindLabels: Record<CatalogKind, string> = {
  ods_dataset: "原始来源数据集", dwd_table: "规范数据表", dwd_field: "数据字段",
  adm_dataset: "Research ADM 数据资产", adm_field: "Research ADM 字段", feature_spec: "指标规格",
  label_card: "规则标签", semantic_family: "概念分类", semantic_concept: "语义概念",
};
const statusLabels: Record<string, string> = {
  accepted: "已验收", active: "有效定义", contract_only: "仅定义校验", declared_only: "仅概念定义",
  deprecated: "已废弃", implemented: "已实现", planned: "尚在规划", registry_validated: "规格已校验",
  reserved: "已登记概念", source_available: "来源已登记", source_gap: "来源受限", unverified: "尚未验证", unknown: "状态未知",
};
const searchStatuses = ["accepted", "implemented", "registry_validated", "declared_only", "reserved", "active", "deprecated", "source_available", "source_gap", "contract_only", "planned", "unverified", "unknown"];
type Reading = { facts: Array<{ label: string; value: string }>; cautions: string[] };

function StatusBadge({ value }: { value: string }) {
  return <span className={`${styles.badge} ${["accepted", "implemented", "registry_validated"].includes(value) ? styles.good : styles.neutral}`}>
    {statusLabels[value] ?? "详见技术状态"}
  </span>;
}

function contextLinks(relations: DataCatalogRelations | null) {
  const result = new Map<string, { label: string; entry: CatalogEntrySummary }>();
  const labels: Record<string, [string, string]> = {
    contains: ["包含字段", "所属数据表"], field_of: ["所属数据表", "包含字段"],
    references: ["引用对象", "被此对象引用"], belongs_to_family: ["所属分类", "分类成员"],
    related_not_equivalent: ["相关，但不等价", "相关，但不等价"], exact_equivalent: ["已证明等价", "已证明等价"],
    replacement: ["登记替代关系", "登记被替代关系"],
  };
  for (const relation of relations?.outgoing ?? []) {
    const label = labels[relation.type]?.[0];
    if (label) result.set(`${label}:${relation.target.entry_id}`, { label, entry: relation.target });
  }
  for (const relation of relations?.incoming ?? []) {
    const label = labels[relation.type]?.[1];
    if (label) result.set(`${label}:${relation.source.entry_id}`, { label, entry: relation.source });
  }
  return [...result.values()];
}

function subscribeSession(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}
function readSessionToken() { return loadStoredSession()?.token ?? null; }
function serverSessionToken() { return null; }

export function CatalogBrowser() {
  // The server and first hydration render both have no session. The external store then
  // restores the browser token without replacing server HTML with a conflicting first tree.
  const storedToken = useSyncExternalStore(subscribeSession, readSessionToken, serverSessionToken);
  const [expired, setExpired] = useState(false);
  const token = expired ? null : storedToken;
  const [summary, setSummary] = useState<DataCatalogSummary | null>(null);
  const [results, setResults] = useState<CatalogEntrySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [relations, setRelations] = useState<DataCatalogRelations | null>(null);
  const [lineageRange, setLineageRange] = useState<"full" | "1" | "2" | "3" | "5">("full");
  const [mode, setMode] = useState<"browse" | "map">("browse");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"" | CatalogKind>("");
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [family, setFamily] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [graphBusy, setGraphBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyResult, setCopyResult] = useState("");
  const searchSequence = useRef(0);

  const handleError = useCallback((observed: unknown) => {
    if (observed instanceof PlatformApiError && observed.status === 401) {
      clearStoredSession(); setExpired(true);
      return "会话已过期，请重新登录。";
    }
    return "目录暂时不可读取，请检查查询条件或稍后重试。";
  }, []);
  const selectEntry = useCallback((id: string) => {
    setSelectedId(id); setRelations(null); setMode("browse"); setCopyResult("");
  }, []);
  const navigate = useCallback((node: { entry_id: string | null }) => {
    setMode("browse");
    if (node.entry_id) selectEntry(node.entry_id);
  }, [selectEntry]);

  const runSearch = useCallback(async (clear = false) => {
    if (!token) return;
    const sequence = ++searchSequence.current;
    setBusy(true); setError(null);
    const params = new URLSearchParams({ limit: "100" });
    if (!clear) {
      if (query.trim()) params.set("q", query.trim());
      if (kind) params.set("kind", kind);
      if (status) params.set("status", status);
      if (domain.trim()) params.set("domain", domain.trim());
      if (family.trim()) params.set("family", family.trim());
    }
    try {
      const response = await platformRequest<DataCatalogSearchResult>(`/api/platform/v1/catalog/search?${params}`, { token });
      if (sequence !== searchSequence.current) return;
      setResults(response.items); setTotal(response.total);
      if (response.items.length) selectEntry(response.items[0].entry_id);
      else { setSelectedId(null); setSelected(null); setRelations(null); }
    } catch (observed) {
      if (sequence === searchSequence.current) setError(handleError(observed));
    } finally {
      if (sequence === searchSequence.current) setBusy(false);
    }
  }, [token, query, kind, status, domain, family, handleError, selectEntry]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const sequence = ++searchSequence.current;
    (async () => {
      setBusy(true);
      try {
        const [observedSummary, response] = await Promise.all([
          platformRequest<DataCatalogSummary>("/api/platform/v1/catalog/summary", { token }),
          platformRequest<DataCatalogSearchResult>("/api/platform/v1/catalog/search?limit=100", { token }),
        ]);
        if (cancelled) return;
        setSummary(observedSummary);
        if (sequence === searchSequence.current) {
          setResults(response.items); setTotal(response.total);
          if (response.items.length) selectEntry(response.items[0].entry_id);
        }
      } catch (observed) { if (!cancelled) setError(handleError(observed)); }
      finally { if (!cancelled && sequence === searchSequence.current) setBusy(false); }
    })();
    return () => { cancelled = true; };
  }, [token, handleError, selectEntry]);

  useEffect(() => {
    if (!token || !selectedId) return;
    let cancelled = false;
    (async () => {
      setDetailBusy(true); setSelected(null);
      try {
        const item = await platformRequest<CatalogEntry>(`/api/platform/v1/catalog/items/${encodeURIComponent(selectedId)}`, { token });
        if (!cancelled) setSelected(item);
      } catch (observed) { if (!cancelled) setError(handleError(observed)); }
      finally { if (!cancelled) setDetailBusy(false); }
    })();
    return () => { cancelled = true; };
  }, [token, selectedId, handleError]);

  useEffect(() => {
    if (!token || !selectedId) return;
    let cancelled = false;
    (async () => {
      setGraphBusy(true);
      try {
        const assetScope = lineageRange === "full" ? "full" : "focus";
        const depth = lineageRange === "full" ? 2 : Number(lineageRange);
        const response = await platformRequest<DataCatalogRelations>(
          `/api/platform/v1/catalog/items/${encodeURIComponent(selectedId)}/relations?asset_scope=${assetScope}&depth=${depth}&limit=100`, { token });
        if (!cancelled) setRelations(response);
      } catch (observed) { if (!cancelled) { setRelations(null); setError(handleError(observed)); } }
      finally { if (!cancelled) setGraphBusy(false); }
    })();
    return () => { cancelled = true; };
  }, [token, selectedId, lineageRange, handleError]);

  if (!token) return <main className={styles.loginShell} data-testid="catalog-login-required"><div className={styles.loginCard}>
    <span className={styles.brandMark}>Q</span><p>Quanthus 数据目录</p><h1>数据、指标与语义目录</h1>
    <p>查清含义、口径、来源和用途。请使用现有平台账号登录。</p><Link href="/operator" className={styles.primaryLink}>前往登录</Link>
  </div></main>;

  const reading = selected?.details.reading as Reading | undefined;
  const technicalDetails = selected ? Object.fromEntries(Object.entries(selected.details).filter(([key]) => key !== "reading")) : {};
  const links = contextLinks(relations);
  return <div className={styles.shell} data-testid="catalog-page">
    <header className={styles.header}><Link href="/catalog" className={styles.brand}><span className={styles.brandMark}>Q</span><strong>Quanthus · 数据目录</strong></Link>
      <Link href="/operator">返回控制台</Link></header>
    <section className={styles.hero}><div><p className={styles.eyebrow}>数据说明与加工关系</p><h1>先读懂数据，再使用数据</h1>
      <p>用中文查找字段、指标和结构事实，了解它们是什么、如何形成，以及使用时需要注意什么。</p></div>
      {summary ? <div className={styles.summaryGrid}>
        <div><strong>{summary.summary.entry_count}</strong><span>目录条目</span></div>
        <div><strong>{summary.summary.counts_by_kind.dwd_table ?? 0}</strong><span>规范数据表</span></div>
        <div><strong>{summary.summary.counts_by_kind.feature_spec ?? 0}</strong><span>指标规格</span></div>
        <div><strong>{summary.summary.counts_by_kind.label_card ?? 0}</strong><span>规则标签</span></div>
      </div> : null}
    </section>
    <nav className={styles.viewSwitch} aria-label="目录视图">
      <button type="button" aria-pressed={mode === "browse"} onClick={() => setMode("browse")}><BookOpen size={18} />查数据与指标</button>
      <button type="button" aria-pressed={mode === "map"} onClick={() => setMode("map")}><GitBranch size={18} />全局血缘地图</button>
    </nav>
    {error ? <p className={styles.errorBanner} role="alert">{error}</p> : null}
    {mode === "map" && summary ? <LineageOverview data={summary.asset_lineage_overview} /> : null}
    {mode === "browse" ? <>
      <form className={styles.searchBar} onSubmit={event => { event.preventDefault(); void runSearch(); }}>
        <label className={styles.searchInput}><Search size={20} /><input value={query} maxLength={120} onChange={event => setQuery(event.target.value)}
          placeholder="搜索收盘价、成交额、20期均线、MACD 或精确字段 ID" aria-label="搜索目录" /></label>
        <select aria-label="条目类型" value={kind} onChange={event => setKind(event.target.value as "" | CatalogKind)}>
          <option value="">全部类型</option>{Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select aria-label="条目状态" value={status} onChange={event => setStatus(event.target.value)}><option value="">全部状态</option>
          {searchStatuses.map(value => <option key={value} value={value}>{statusLabels[value]}</option>)}
        </select><button type="submit" className={styles.searchButton} disabled={busy}>{busy ? <LoaderCircle size={18} /> : <Search size={18} />}搜索</button>
      </form>
      <details className={styles.filterDetails}><summary>按业务领域或分类精确筛选</summary><div className={styles.filterStrip}>
        <label>业务领域<input value={domain} onChange={event => setDomain(event.target.value)} placeholder="例如 structure、indicator" /></label>
        <label>概念分类<input value={family} onChange={event => setFamily(event.target.value)} placeholder="例如 structure.anchor" /></label>
        <button type="button" onClick={() => { setQuery(""); setKind(""); setStatus(""); setDomain(""); setFamily(""); void runSearch(true); }}>清除筛选</button>
      </div></details>
      <p className={styles.resultCount}>{total} 个匹配，显示 {results.length} 个。{total > results.length ? "当前最多显示前100项，请用搜索或筛选缩小范围。" : ""}</p>
      <main className={styles.workspace}>
        <aside className={styles.resultPane} aria-label="目录搜索结果">{results.length ? results.map(item => <button type="button" key={item.entry_id}
          className={`${styles.resultItem} ${selectedId === item.entry_id ? styles.selected : ""}`} onClick={() => selectEntry(item.entry_id)}>
          <strong>{item.display_name}</strong><small>{kindLabels[item.kind]}</small><code>{item.canonical_id}</code>
        </button>) : <p className={styles.emptyState}>{busy ? "正在搜索…" : "没有匹配条目，请调整关键词。"}</p>}</aside>
        <article className={styles.detailPane} aria-live="polite" data-testid="catalog-detail">
          {detailBusy ? <p className={styles.emptyState}>正在读取说明…</p> : selected ? <>
            <div className={styles.detailHeader}><div><p className={styles.eyebrow}>{kindLabels[selected.kind]}</p><h2>{selected.display_name}</h2><code>{selected.canonical_id}</code></div>
              <button type="button" className={styles.copyButton} onClick={async () => {
                try { await navigator.clipboard.writeText(selected.canonical_id); setCopyResult("已复制标识"); }
                catch { setCopyResult("复制失败，可手动选择标识"); }
              }}><Copy size={16} />复制标识</button>
            </div>
            {copyResult ? <p role="status">{copyResult}</p> : null}
            <div className={styles.badges}><StatusBadge value={selected.implementation_status} /><StatusBadge value={selected.verification_status} /></div>
            <p className={styles.description} data-testid="business-description">{selected.description}</p>
            <section className={styles.detailSection}><h3>如何理解和使用</h3><dl className={styles.businessFacts}>
              {reading?.facts.map(fact => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}
            </dl></section>
            {reading?.cautions.length ? <section className={styles.cautionBox}><h3>使用时请注意</h3>{reading.cautions.map(note => <p key={note}>{note}</p>)}</section> : null}
            {links.length ? <details className={styles.technical}><summary>所属对象与概念关联（不作为加工箭头）</summary><div className={styles.relationsGrid}>
              {links.map(({ label, entry }) => <button type="button" key={`${label}:${entry.entry_id}`} onClick={() => selectEntry(entry.entry_id)}>
                <small>{label}</small><strong>{entry.display_name}</strong>
              </button>)}
            </div></details> : null}
            <details className={styles.technical} data-testid="technical-details"><summary>技术详情：精确规格、版本与来源依据</summary>
              <dl className={styles.businessFacts}>
                <div><dt>精确标识</dt><dd>{selected.entry_id}</dd></div><div><dt>注册 / 生命周期</dt><dd>{statusLabels[selected.lifecycle] ?? selected.lifecycle}</dd></div>
                <div><dt>实现状态</dt><dd>{statusLabels[selected.implementation_status]}</dd></div><div><dt>验证状态</dt><dd>{statusLabels[selected.verification_status]}</dd></div>
                <div><dt>验收范围</dt><dd>{selected.accepted_scope ?? "未声明"}</dd></div><div><dt>来源路径</dt><dd>{selected.authority.path}</dd></div>
                <div><dt>定义位置</dt><dd>{selected.authority.pointer}</dd></div><div><dt>来源校验摘要</dt><dd>{selected.authority.sha256}</dd></div>
              </dl><h4>原始机器规格（只读）</h4><pre>{JSON.stringify(technicalDetails, null, 2)}</pre>
            </details>
          </> : <p className={styles.emptyState}><Database size={24} />选择左侧条目查看说明。</p>}
        </article>
      </main>
      {relations ? <div className={styles.focusArea}>
        <LineageFocus
          key={`${selectedId}:${lineageRange}`}
          relations={relations}
          onNavigate={navigate}
          range={lineageRange}
          onRange={setLineageRange}
          busy={graphBusy}
        />
      </div> : null}
    </> : null}
  </div>;
}
