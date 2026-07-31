import {
  isSyntheticFixture,
  parseWorkspaceArtifact,
} from "./artifact-envelope.ts";
import {
  createWorkspaceCatalogEntry,
  type PublishedReleaseReference,
  type WorkspaceCatalogEntry,
} from "./workspace-catalog.ts";

export const ATTRIBUTION_PUBLICATION_CATALOG_SCHEMA =
  "quanthus.strategy-attribution-publication-catalog.v1";
export const ATTRIBUTION_PUBLICATION_SCHEMA =
  "quanthus.strategy-attribution-publication.v1";
export const DEFAULT_PUBLICATION_CATALOG_URL =
  "/data/attribution-releases/catalog.json";

export interface AttributionPublicationCatalogRelease {
  release_id: string;
  release_path: string;
  workspace_path: string;
  checksum_path: string;
  ready_path: string;
}

export interface AttributionPublicationCatalog {
  schema_version: string;
  releases: AttributionPublicationCatalogRelease[];
}

interface AttributionReleaseManifest {
  schema_version: string;
  release_id: string;
  run_id: string;
  source_kind: string;
  synthetic_fixture: boolean;
  formal_strategy_evidence: boolean;
  workspace_digest: string;
  digest: string;
}

export interface PublishedWorkspaceCatalogResult {
  entries: WorkspaceCatalogEntry[];
  errors: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isReleaseId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)
  );
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

function expectedReleasePath(releaseId: string, fileName: string) {
  return `./${releaseId}/${fileName}`;
}

function isCatalogRelease(
  value: unknown,
): value is AttributionPublicationCatalogRelease {
  if (!isRecord(value) || !isReleaseId(value.release_id)) return false;
  return (
    value.release_path === expectedReleasePath(value.release_id, "release.json") &&
    value.workspace_path ===
      expectedReleasePath(value.release_id, "workspace.json") &&
    value.checksum_path ===
      expectedReleasePath(value.release_id, "CHECKSUMS.sha256") &&
    value.ready_path === expectedReleasePath(value.release_id, "READY")
  );
}

async function sha256Hex(text: string): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("当前环境不支持发布完整性校验");
  }
  const value = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(value), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function parseChecksumLedger(text: string): Map<string, string> {
  if (!text.endsWith("\n")) {
    throw new Error("发布校验账本不是规范文本");
  }
  const entries = new Map<string, string>();
  for (const line of text.slice(0, -1).split("\n")) {
    const match = /^([0-9a-f]{64})  ([^\\\r\n]+)$/.exec(line);
    if (!match) throw new Error("发布校验账本包含无效条目");
    const [, checksum, path] = match;
    const segments = path.split("/");
    if (
      path.startsWith("/") ||
      segments.some((segment) => !segment || segment === "." || segment === "..") ||
      path === "CHECKSUMS.sha256" ||
      path === "READY" ||
      entries.has(path)
    ) {
      throw new Error("发布校验账本包含越界或重复路径");
    }
    entries.set(path, checksum);
  }
  if (!entries.size) throw new Error("发布校验账本不能为空");
  const canonical = Array.from(entries)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([path, checksum]) => `${checksum}  ${path}\n`)
    .join("");
  if (canonical !== text) throw new Error("发布校验账本不是规范排序");
  return entries;
}

async function verifyChecksummedText(
  checksums: Map<string, string>,
  path: string,
  text: string,
): Promise<void> {
  const expected = checksums.get(path);
  if (!expected || (await sha256Hex(text)) !== expected) {
    throw new Error(`${path} 与发布校验账本不一致`);
  }
}

function parseReleaseManifest(text: string): AttributionReleaseManifest {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("发布信封不是有效的 JSON");
  }
  if (
    !isRecord(value) ||
    value.schema_version !== ATTRIBUTION_PUBLICATION_SCHEMA ||
    !isReleaseId(value.release_id) ||
    typeof value.run_id !== "string" ||
    !value.run_id.trim() ||
    typeof value.source_kind !== "string" ||
    typeof value.synthetic_fixture !== "boolean" ||
    typeof value.formal_strategy_evidence !== "boolean" ||
    !isDigest(value.workspace_digest) ||
    !isDigest(value.digest) ||
    value.synthetic_fixture === value.formal_strategy_evidence
  ) {
    throw new Error("发布信封不是受支持的 Quanthus 归因发布 v1 产物");
  }
  return value as unknown as AttributionReleaseManifest;
}

export function parsePublicationCatalog(
  text: string,
): AttributionPublicationCatalog {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("归因发布目录不是有效的 JSON");
  }
  if (
    !isRecord(value) ||
    value.schema_version !== ATTRIBUTION_PUBLICATION_CATALOG_SCHEMA ||
    !Array.isArray(value.releases) ||
    !value.releases.every(isCatalogRelease)
  ) {
    throw new Error("归因发布目录格式不受支持");
  }
  const releaseIds = value.releases.map((item) => item.release_id);
  if (new Set(releaseIds).size !== releaseIds.length) {
    throw new Error("归因发布目录包含重复发布标识");
  }
  return value as unknown as AttributionPublicationCatalog;
}

function resolveCatalogPath(catalogUrl: URL, relativePath: string): URL {
  const resolved = new URL(relativePath, catalogUrl);
  const catalogDirectory = new URL("./", catalogUrl);
  if (
    resolved.origin !== catalogUrl.origin ||
    !resolved.pathname.startsWith(catalogDirectory.pathname) ||
    resolved.search ||
    resolved.hash
  ) {
    throw new Error("归因发布目录包含越界路径");
  }
  return resolved;
}

async function readRequiredResponse(
  fetcher: typeof fetch,
  url: URL,
  description: string,
): Promise<string> {
  const response = await fetcher(url, {
    headers: { accept: "application/json, text/plain" },
  });
  if (!response.ok) {
    throw new Error(`${description}不可读取（${response.status}）`);
  }
  return response.text();
}

async function loadPublishedRelease(
  fetcher: typeof fetch,
  catalogUrl: URL,
  catalogRelease: AttributionPublicationCatalogRelease,
): Promise<WorkspaceCatalogEntry> {
  const readyUrl = resolveCatalogPath(catalogUrl, catalogRelease.ready_path);
  const checksumUrl = resolveCatalogPath(
    catalogUrl,
    catalogRelease.checksum_path,
  );
  const releaseUrl = resolveCatalogPath(catalogUrl, catalogRelease.release_path);
  const workspaceUrl = resolveCatalogPath(
    catalogUrl,
    catalogRelease.workspace_path,
  );

  const readyText = await readRequiredResponse(
    fetcher,
    readyUrl,
    "发布完成标记",
  );
  if (!/^[0-9a-f]{64}\n?$/.test(readyText)) {
    throw new Error("发布完成标记格式无效");
  }
  const checksumText = await readRequiredResponse(
    fetcher,
    checksumUrl,
    "发布校验账本",
  );
  if (`${await sha256Hex(checksumText)}\n` !== readyText) {
    throw new Error("发布完成标记未绑定校验账本");
  }
  const checksums = parseChecksumLedger(checksumText);
  const [releaseText, workspaceText] = await Promise.all([
    readRequiredResponse(fetcher, releaseUrl, "发布信封"),
    readRequiredResponse(fetcher, workspaceUrl, "归因工作区"),
  ]);
  await Promise.all([
    verifyChecksummedText(checksums, "release.json", releaseText),
    verifyChecksummedText(checksums, "workspace.json", workspaceText),
  ]);
  const release = parseReleaseManifest(releaseText);
  const workspace = parseWorkspaceArtifact(workspaceText);
  if (
    release.release_id !== catalogRelease.release_id ||
    release.workspace_digest !== workspace.digest ||
    release.source_kind !== workspace.source_kind ||
    release.synthetic_fixture !== isSyntheticFixture(workspace)
  ) {
    throw new Error("发布目录、发布信封与归因工作区身份不一致");
  }

  const publication: PublishedReleaseReference = {
    releaseId: release.release_id,
    runId: release.run_id,
    sourceKind: release.source_kind,
    syntheticFixture: release.synthetic_fixture,
    formalStrategyEvidence: release.formal_strategy_evidence,
    workspaceDigest: release.workspace_digest,
    releaseDigest: release.digest,
  };
  return createWorkspaceCatalogEntry(
    workspace,
    workspaceText,
    "published",
    `published:${release.release_id}:${workspace.digest}`,
    publication,
  );
}

export async function loadPublishedWorkspaceCatalog(
  fetcher: typeof fetch = fetch,
  catalogLocation = DEFAULT_PUBLICATION_CATALOG_URL,
): Promise<PublishedWorkspaceCatalogResult> {
  const browserOrigin =
    typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const catalogUrl = new URL(catalogLocation, browserOrigin);
  const catalogText = await readRequiredResponse(
    fetcher,
    catalogUrl,
    "归因发布目录",
  );
  const catalog = parsePublicationCatalog(catalogText);
  const loaded = await Promise.all(
    catalog.releases.map(async (release) => {
      try {
        return {
          entry: await loadPublishedRelease(fetcher, catalogUrl, release),
        };
      } catch (error) {
        return {
          error: `${release.release_id}：${
            error instanceof Error ? error.message : "无法读取发布产物"
          }`,
        };
      }
    }),
  );
  return {
    entries: loaded.flatMap((item) => (item.entry ? [item.entry] : [])),
    errors: loaded.flatMap((item) => (item.error ? [item.error] : [])),
  };
}
