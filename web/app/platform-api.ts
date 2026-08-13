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
