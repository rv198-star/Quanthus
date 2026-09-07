export const PLATFORM_SESSION_KEY = "quanthus.platform.session.v1";

export type PlatformRole = "admin" | "operator" | "viewer";

export interface PlatformPrincipal {
  user_id: string;
  username: string;
  role: PlatformRole;
  mfa_enabled: boolean;
}

export interface PlatformUser extends PlatformPrincipal {
  active: boolean;
  created_at: string;
}

export interface LoginResult {
  token: string;
  expires_at: string;
  user: Omit<PlatformPrincipal, "mfa_enabled">;
}

export interface GateFacts {
  port_version: string;
  gate_version: string;
  current_gate: string;
  current_status: string;
  gates: Array<{ id: string; name: string; status: string }>;
  acceptance_results: Record<string, string>;
  source_ref: string;
}

export interface PlatformJob {
  job_id: string;
  kind: string;
  actor_user_id: string;
  idempotency_key: string;
  input_ref: { release_id?: string };
  state: "pending" | "running" | "succeeded" | "failed" | "canceled";
  progress: number;
  cancel_requested: boolean;
  attempt: number;
  result_ref: Record<string, unknown> | null;
  error_reason: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface PublishedRelease {
  release_id: string;
  release_path: string;
  workspace_path: string;
  checksum_path: string;
  ready_path: string;
}

export interface PublicationCatalog {
  schema_version: string;
  releases: PublishedRelease[];
}

export type CatalogKind =
  | "ods_dataset"
  | "dwd_table"
  | "dwd_field"
  | "feature_spec"
  | "label_card"
  | "semantic_family"
  | "semantic_concept";

export interface CatalogEntrySummary {
  entry_id: string;
  kind: CatalogKind;
  canonical_id: string;
  display_name: string;
  domain: string;
  family: string;
  lifecycle: string;
  implementation_status: string;
  verification_status: string;
  accepted_scope: string | null;
}

export interface CatalogRelation {
  type: string;
  target_entry_id: string;
}

export interface CatalogEntry extends CatalogEntrySummary {
  description: string;
  authority: { path: string; pointer: string; sha256: string };
  aliases: string[];
  keywords: string[];
  relations: CatalogRelation[];
  details: Record<string, unknown>;
}

export interface CatalogGap {
  gap_id: string;
  category: string;
  description: string;
  affected_entry_ids: string[];
  authority_refs: string[];
}

export interface DataCatalogSummary {
  schema_version: string;
  catalog_id: string;
  builder_version: string;
  snapshot_digest: string;
  release_identity: Record<string, string>;
  summary: {
    entry_count: number;
    counts_by_kind: Record<string, number>;
  };
  lineage_overview: CatalogLineageOverview;
}

export interface DataCatalogSearchResult {
  query: string;
  filters: Record<string, string | null>;
  total: number;
  limit: number;
  items: CatalogEntrySummary[];
}

export interface CatalogLineageNode {
  id: string;
  label: string;
  layer: string;
  grain: string;
  description: string;
  entry_id: string | null;
  process: boolean;
  evidence: { path: string; sha256: string; locator: string } | null;
  parent_entry_id: string | null;
  distance?: number;
}

export interface CatalogLineageEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  role: string;
  grain: string;
  evidence: { path: string; sha256: string; locator: string };
}

export interface CatalogLineageContext {
  center: string;
  scope: string;
  message: string;
  omitted_nodes: number;
  boundary_edges: number;
  nodes: CatalogLineageNode[];
  edges: CatalogLineageEdge[];
}

export interface CatalogLineageGraph {
  center: string;
  depth: number;
  limit: number;
  scope: string;
  coverage: string;
  coverage_level: "registered_node" | "field_proven" | "table_context_only" | "field_unregistered";
  coverage_message: string;
  table_context: CatalogLineageContext | null;
  omitted_nodes: number;
  boundary_edges: number;
  nodes: CatalogLineageNode[];
  edges: CatalogLineageEdge[];
}

export interface CatalogLineageOverview {
  scope: string;
  coverage: string;
  registered_edges: number;
  internal_edges: number;
  field_coverage: { total: number; field_proven: number; table_context_only: number; unregistered: number };
  nodes: Array<{ id: string; label: string; layer: string; members: CatalogLineageNode[]; process_count: number }>;
  edges: Array<{ source: string; target: string; count: number }>;
}

export interface DataCatalogRelations {
  entry_id: string;
  lineage: CatalogLineageGraph;
  outgoing: Array<CatalogRelation & { target: CatalogEntrySummary }>;
  incoming: Array<{
    type: string;
    source_entry_id: string;
    source: CatalogEntrySummary;
  }>;
}

export class PlatformApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly reason: string,
    public readonly requestId: string | null,
  ) {
    super(reason);
  }
}

export function loadStoredSession(): { token: string; expiresAt: string } | null {
  try {
    const raw = sessionStorage.getItem(PLATFORM_SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as { token?: unknown; expiresAt?: unknown };
    if (typeof value.token !== "string" || typeof value.expiresAt !== "string") {
      sessionStorage.removeItem(PLATFORM_SESSION_KEY);
      return null;
    }
    if (Date.parse(value.expiresAt) <= Date.now()) {
      sessionStorage.removeItem(PLATFORM_SESSION_KEY);
      return null;
    }
    return { token: value.token, expiresAt: value.expiresAt };
  } catch {
    sessionStorage.removeItem(PLATFORM_SESSION_KEY);
    return null;
  }
}

export function storeSession(result: LoginResult) {
  sessionStorage.setItem(
    PLATFORM_SESSION_KEY,
    JSON.stringify({ token: result.token, expiresAt: result.expires_at }),
  );
}

export function clearStoredSession() {
  sessionStorage.removeItem(PLATFORM_SESSION_KEY);
}

export async function platformRequest<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "DELETE";
    token?: string | null;
    body?: Record<string, unknown>;
    idempotencyKey?: string;
  } = {},
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.idempotencyKey) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => null)) as
    | { error?: { reason?: string; request_id?: string } }
    | T
    | null;
  if (!response.ok) {
    const error = payload as { error?: { reason?: string; request_id?: string } } | null;
    throw new PlatformApiError(
      response.status,
      error?.error?.reason ?? "request_failed",
      error?.error?.request_id ?? response.headers.get("x-request-id"),
    );
  }
  return payload as T;
}

export async function loadPublicationCatalog(): Promise<PublicationCatalog> {
  const response = await fetch("/data/attribution-releases/catalog.json", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (response.status === 404) {
    return {
      schema_version: "quanthus.strategy-attribution-publication-catalog.v1",
      releases: [],
    };
  }
  if (!response.ok) throw new Error("release_catalog_unavailable");
  const value = (await response.json()) as PublicationCatalog;
  if (
    value.schema_version !==
      "quanthus.strategy-attribution-publication-catalog.v1" ||
    !Array.isArray(value.releases)
  ) {
    throw new Error("release_catalog_invalid");
  }
  return value;
}
