"use client";

import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  CircleHelp,
  Copy,
  Database,
  FileCode2,
  GitBranch,
  LoaderCircle,
  LogIn,
  RefreshCw,
  Search,
  Sigma,
  Tag,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  clearStoredSession,
  type CatalogEntry,
  type CatalogEntrySummary,
  type CatalogKind,
  type DataCatalogRelations,
  type DataCatalogSearchResult,
  type DataCatalogSummary,
  loadStoredSession,
  PlatformApiError,
  platformRequest,
} from "./platform-api";
import styles from "./catalog-browser.module.css";

const kindLabels: Record<CatalogKind, string> = {
  ods_dataset: "ODS 数据集",
  dwd_table: "DWD 表",
  dwd_field: "DWD 字段",
  feature_spec: "指标 / Feature 规格",
  label_card: "Label Card",
  semantic_family: "语义分类",
  semantic_concept: "语义概念",
};

const statusLabels: Record<string, string> = {
  accepted: "已验收",
  active: "Active",
  contract_only: "仅合同",
  declared_only: "仅定义",
  deprecated: "已废弃",
  implemented: "已实现",
  planned: "计划中",
  registry_validated: "Registry 已校验",
  reserved: "Reserved",
  source_available: "源可用",
  source_gap: "源缺口",
  unverified: "未验证",
  unknown: "未知",
};

const kindIcons: Record<CatalogKind, typeof Database> = {
  ods_dataset: Database,
  dwd_table: Database,
  dwd_field: FileCode2,
  feature_spec: Sigma,
  label_card: Tag,
  semantic_family: BookOpen,
  semantic_concept: CircleHelp,
};

const searchKinds: Array<{ value: "" | CatalogKind; label: string }> = [
  { value: "", label: "全部类型" },
  ...Object.entries(kindLabels).map(([value, label]) => ({
    value: value as CatalogKind,
    label,
  })),
];

const searchStatuses = [
  ["", "全部状态"],
  ["accepted", "已验收"],
  ["registry_validated", "Registry 已校验"],
  ["contract_only", "仅合同"],
  ["reserved", "Reserved"],
  ["active", "Active"],
  ["source_available", "源可用"],
  ["declared_only", "仅定义"],
] as const;

function badgeTone(value: string) {
  if (["accepted", "active", "implemented", "registry_validated", "source_available"].includes(value)) {
    return styles.good;
  }
  if (["reserved", "contract_only", "declared_only", "planned"].includes(value)) {
    return styles.neutral;
  }
  if (["source_gap", "unverified", "unknown"].includes(value)) return styles.warn;
  return styles.muted;
}

function StatusBadge({ value }: { value: string }) {
  return (
    <span className={`${styles.badge} ${badgeTone(value)}`}>
      {statusLabels[value] ?? value}
    </span>
  );
}

function shortHash(value: string) {
  if (!value.startsWith("sha256:") || value.length < 30) return value;
  return `${value.slice(0, 15)}…${value.slice(-8)}`;
}

function stringify(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function DetailRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div className={styles.detailRow}>
      <dt>{label}</dt>
      <dd>{stringify(value)}</dd>
    </div>
  );
}

function RelationCard({
  label,
  type,
  entry,
  onSelect,
}: {
  label: string;
  type: string;
  entry: CatalogEntrySummary;
  onSelect: (entryId: string) => void;
}) {
  return (
    <button type="button" className={styles.relationCard} onClick={() => onSelect(entry.entry_id)}>
      <span>{label}</span>
      <strong>{entry.display_name}</strong>
      <small>{type} · {kindLabels[entry.kind]}</small>
    </button>
  );
}

export function CatalogBrowser() {
  const [session] = useState(() =>
    typeof window === "undefined" ? null : loadStoredSession(),
  );
  const [token, setToken] = useState<string | null>(session?.token ?? null);
  const [summary, setSummary] = useState<DataCatalogSummary | null>(null);
  const [results, setResults] = useState<CatalogEntrySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [relations, setRelations] = useState<DataCatalogRelations | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"" | CatalogKind>("");
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [family, setFamily] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gapsOpen, setGapsOpen] = useState(false);

  const expireSession = useCallback(() => {
    clearStoredSession();
    setToken(null);
  }, []);

  const handleError = useCallback((observed: unknown) => {
    if (observed instanceof PlatformApiError && observed.status === 401) {
      expireSession();
      return "会话已过期，请重新登录。";
    }
    if (observed instanceof PlatformApiError) {
      return `目录请求失败：${observed.reason}`;
    }
    return "目录暂时不可读取。";
  }, [expireSession]);

  const runSearch = useCallback(async (
    nextQuery = query,
    nextKind = kind,
    nextStatus = status,
    nextDomain = domain,
    nextFamily = family,
  ) => {
    if (!token) return;
    setBusy(true);
    setError(null);
    const params = new URLSearchParams();
    if (nextQuery.trim()) params.set("q", nextQuery.trim());
    if (nextKind) params.set("kind", nextKind);
    if (nextStatus) params.set("status", nextStatus);
    if (nextDomain.trim()) params.set("domain", nextDomain.trim());
    if (nextFamily.trim()) params.set("family", nextFamily.trim());
    params.set("limit", "80");
    try {
      const result = await platformRequest<DataCatalogSearchResult>(
        `/api/platform/v1/catalog/search?${params.toString()}`,
        { token },
      );
      setResults(result.items);
      setTotal(result.total);
      if (result.items.length && !selectedId) setSelectedId(result.items[0].entry_id);
    } catch (observed) {
      setError(handleError(observed));
    } finally {
      setBusy(false);
    }
  }, [domain, family, handleError, kind, query, selectedId, status, token]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      setBusy(true);
      try {
        const [observedSummary, observedSearch] = await Promise.all([
          platformRequest<DataCatalogSummary>("/api/platform/v1/catalog/summary", { token }),
          platformRequest<DataCatalogSearchResult>("/api/platform/v1/catalog/search?limit=80", { token }),
        ]);
        if (cancelled) return;
        setSummary(observedSummary);
        setResults(observedSearch.items);
        setTotal(observedSearch.total);
        if (observedSearch.items.length) setSelectedId(observedSearch.items[0].entry_id);
      } catch (observed) {
        if (!cancelled) setError(handleError(observed));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [handleError, token]);

  useEffect(() => {
    if (!token || !selectedId) return;
    let cancelled = false;
    (async () => {
      setDetailBusy(true);
      try {
        const encoded = encodeURIComponent(selectedId);
        const [item, relationResult] = await Promise.all([
          platformRequest<CatalogEntry>(`/api/platform/v1/catalog/items/${encoded}`, { token }),
          platformRequest<DataCatalogRelations>(
            `/api/platform/v1/catalog/items/${encoded}/relations`,
            { token },
          ),
        ]);
        if (!cancelled) {
          setSelected(item);
          setRelations(relationResult);
        }
      } catch (observed) {
        if (!cancelled) setError(handleError(observed));
      } finally {
        if (!cancelled) setDetailBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [handleError, selectedId, token]);

  const domains = useMemo(
    () => Array.from(new Set(results.map((item) => item.domain))).sort(),
    [results],
  );
  const families = useMemo(
    () => Array.from(new Set(results.map((item) => item.family))).sort(),
    [results],
  );

  if (!token) {
    return (
      <main className={styles.loginShell} data-testid="catalog-login-required">
        <div className={styles.loginCard}>
          <span className={styles.brandMark}>Q</span>
          <p className={styles.eyebrow}>Quanthus Catalog</p>
          <h1>数据、指标与语义目录</h1>
          <p>Catalog 是只读知识面，需要使用现有 Platform 账号登录后访问。</p>
          <Link href="/operator" className={styles.primaryLink}>
            <LogIn aria-hidden="true" />
            前往登录
          </Link>
        </div>
      </main>
    );
  }

  return (
    <div className={styles.shell} data-testid="catalog-page">
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>Q</span>
          <div>
            <strong>Quanthus Catalog</strong>
            <span>Data · Indicator · Facts · Label</span>
          </div>
        </div>
        <div className={styles.headerActions}>
          <button type="button" className={styles.iconButton} onClick={() => runSearch()} title="刷新搜索">
            <RefreshCw className={busy ? styles.spin : undefined} aria-hidden="true" />
          </button>
          <Link href="/operator" className={styles.iconButton} title="平台控制台">
            <ArrowLeft aria-hidden="true" />
          </Link>
        </div>
      </header>

      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>Read-only authority projection</p>
          <h1>Quanthus 到底知道些什么？</h1>
          <p>
            搜索数据集、字段、指标规格、Facts、Label 与语义概念，并直接查看定义、时间口径、
            权威来源、关系和当前可信范围。
          </p>
        </div>
        {summary ? (
          <div className={styles.summaryGrid}>
            <div><strong>{summary.summary.entry_count}</strong><span>目录项</span></div>
            <div><strong>{summary.summary.counts_by_kind.dwd_table ?? 0}</strong><span>DWD 表</span></div>
            <div><strong>{summary.summary.counts_by_kind.feature_spec ?? 0}</strong><span>Feature 规格</span></div>
            <button type="button" onClick={() => setGapsOpen((value) => !value)}>
              <strong>{summary.summary.gap_count}</strong><span>已知缺口</span>
            </button>
          </div>
        ) : null}
      </section>

      {summary && gapsOpen ? (
        <section className={styles.gapPanel} data-testid="catalog-gaps">
          <div className={styles.sectionTitle}>
            <AlertTriangle aria-hidden="true" />
            <div><strong>已知缺口不是“空值”</strong><span>Catalog 主动保留无法证明或尚未实现的部分。</span></div>
          </div>
          <div className={styles.gapList}>
            {summary.gaps.map((gap) => (
              <article key={gap.gap_id}>
                <code>{gap.category}</code>
                <p>{gap.description}</p>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <form
        className={styles.searchBar}
        onSubmit={(event) => {
          event.preventDefault();
          runSearch();
        }}
      >
        <label className={styles.searchInput}>
          <Search aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 MA20、RSI、MACD、dwd_security_bar.close、structure.anchor…"
            aria-label="搜索目录"
          />
        </label>
        <select value={kind} onChange={(event) => setKind(event.target.value as "" | CatalogKind)} aria-label="类型">
          {searchKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="状态">
          {searchStatuses.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <button type="submit" className={styles.searchButton} disabled={busy}>
          {busy ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Search aria-hidden="true" />}
          搜索
        </button>
      </form>

      <section className={styles.filterStrip}>
        <label>
          Domain
          <input list="catalog-domains" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="例如 indicator" />
          <datalist id="catalog-domains">{domains.map((value) => <option key={value} value={value} />)}</datalist>
        </label>
        <label>
          Family
          <input list="catalog-families" value={family} onChange={(event) => setFamily(event.target.value)} placeholder="例如 structure.anchor" />
          <datalist id="catalog-families">{families.map((value) => <option key={value} value={value} />)}</datalist>
        </label>
        <button type="button" onClick={() => {
          setQuery(""); setKind(""); setStatus(""); setDomain(""); setFamily("");
          runSearch("", "", "", "", "");
        }}>清除过滤</button>
        <span>{total} 个匹配 · 当前显示 {results.length}</span>
      </section>

      {error ? <div className={styles.errorBanner}>{error}</div> : null}

      <main className={styles.workspace}>
        <aside className={styles.resultPane} aria-label="目录搜索结果">
          {results.length ? results.map((item) => {
            const Icon = kindIcons[item.kind];
            return (
              <button
                type="button"
                key={item.entry_id}
                className={`${styles.resultItem} ${selectedId === item.entry_id ? styles.selected : ""}`}
                onClick={() => setSelectedId(item.entry_id)}
              >
                <Icon aria-hidden="true" />
                <span>
                  <strong>{item.display_name}</strong>
                  <code>{item.canonical_id}</code>
                  <small>{kindLabels[item.kind]} · {item.domain}</small>
                </span>
                <StatusBadge value={item.verification_status} />
              </button>
            );
          }) : (
            <div className={styles.emptyState}>没有找到匹配目录项。</div>
          )}
        </aside>

        <article className={styles.detailPane} aria-live="polite">
          {detailBusy ? (
            <div className={styles.detailLoading}><LoaderCircle className={styles.spin} /> 正在读取详情…</div>
          ) : selected ? (
            <>
              <div className={styles.detailHeader}>
                <div>
                  <p className={styles.eyebrow}>{kindLabels[selected.kind]}</p>
                  <h2>{selected.display_name}</h2>
                  <code>{selected.canonical_id}</code>
                </div>
                <button
                  type="button"
                  className={styles.copyButton}
                  onClick={() => navigator.clipboard.writeText(selected.canonical_id)}
                >
                  <Copy aria-hidden="true" /> 复制 ID
                </button>
              </div>

              <div className={styles.badges}>
                <StatusBadge value={selected.lifecycle} />
                <StatusBadge value={selected.implementation_status} />
                <StatusBadge value={selected.verification_status} />
                {selected.accepted_scope ? <span className={`${styles.badge} ${styles.good}`}>{selected.accepted_scope}</span> : null}
              </div>

              <p className={styles.description}>{selected.description}</p>

              {Array.isArray(selected.details.cautions) && selected.details.cautions.length ? (
                <section className={styles.cautionBox}>
                  <AlertTriangle aria-hidden="true" />
                  <div>
                    <strong>口径提醒</strong>
                    {(selected.details.cautions as string[]).map((note) => <p key={note}>{note}</p>)}
                  </div>
                </section>
              ) : null}

              <section className={styles.detailSection}>
                <div className={styles.sectionTitle}><CheckCircle2 aria-hidden="true" /><strong>身份与权威</strong></div>
                <dl>
                  <DetailRow label="Entry ID" value={selected.entry_id} />
                  <DetailRow label="Domain" value={selected.domain} />
                  <DetailRow label="Family" value={selected.family} />
                  <DetailRow label="Authority path" value={selected.authority.path} />
                  <DetailRow label="Authority pointer" value={selected.authority.pointer} />
                  <DetailRow label="Authority SHA" value={shortHash(selected.authority.sha256)} />
                </dl>
              </section>

              <section className={styles.detailSection}>
                <div className={styles.sectionTitle}><FileCode2 aria-hidden="true" /><strong>定义 / 计算规格</strong></div>
                <dl>
                  {Object.entries(selected.details).filter(([key]) => key !== "cautions").map(([key, value]) => (
                    <DetailRow key={key} label={key} value={value} />
                  ))}
                </dl>
              </section>

              <section className={styles.detailSection}>
                <div className={styles.sectionTitle}><GitBranch aria-hidden="true" /><strong>关系 / Lineage</strong></div>
                {relations && (relations.outgoing.length || relations.incoming.length) ? (
                  <div className={styles.relationsGrid}>
                    {relations.outgoing.map((relation) => (
                      <RelationCard key={`out-${relation.type}-${relation.target_entry_id}`} label="下游 / 引用" type={relation.type} entry={relation.target} onSelect={setSelectedId} />
                    ))}
                    {relations.incoming.map((relation) => (
                      <RelationCard key={`in-${relation.type}-${relation.source_entry_id}`} label="上游 / 被引用" type={relation.type} entry={relation.source} onSelect={setSelectedId} />
                    ))}
                  </div>
                ) : <div className={styles.emptyRelations}>当前目录没有登记可证明的关系。</div>}
              </section>
            </>
          ) : (
            <div className={styles.emptyState}>选择左侧目录项查看定义与血缘。</div>
          )}
        </article>
      </main>
    </div>
  );
}
