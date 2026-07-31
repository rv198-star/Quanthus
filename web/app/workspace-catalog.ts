import type { AttributionWorkspace } from "./types";

export type WorkspaceOrigin = "published" | "synthetic" | "imported";

export interface PublishedReleaseReference {
  releaseId: string;
  runId: string;
  sourceKind: string;
  syntheticFixture: boolean;
  formalStrategyEvidence: boolean;
  workspaceDigest: string;
  releaseDigest: string;
}

export interface WorkspaceCatalogEntry {
  entryId: string;
  origin: WorkspaceOrigin;
  workspace: AttributionWorkspace;
  artifactText: string;
  publication?: PublishedReleaseReference;
}

export interface StrategyWorkspaceGroup {
  strategyId: string;
  entries: WorkspaceCatalogEntry[];
}

export function createWorkspaceCatalogEntry(
  workspace: AttributionWorkspace,
  artifactText: string,
  origin: WorkspaceOrigin,
  localEntryId = `${origin}:${workspace.digest}`,
  publication?: PublishedReleaseReference,
): WorkspaceCatalogEntry {
  if ((origin === "published") !== (publication !== undefined)) {
    throw new Error("已发布工作区必须且只能携带服务端发布元数据");
  }
  if (publication && publication.workspaceDigest !== workspace.digest) {
    throw new Error("服务端发布元数据与工作区摘要不一致");
  }
  return {
    entryId: localEntryId,
    origin,
    workspace,
    artifactText,
    publication,
  };
}

export function upsertWorkspaceCatalogEntries(
  current: WorkspaceCatalogEntry[],
  incoming: WorkspaceCatalogEntry[],
): WorkspaceCatalogEntry[] {
  const next = [...current];
  for (const entry of incoming) {
    const index = next.findIndex((item) => item.entryId === entry.entryId);
    if (index === -1) next.push(entry);
    else next[index] = entry;
  }
  return next;
}

export function removeWorkspaceCatalogEntry(
  current: WorkspaceCatalogEntry[],
  entryId: string,
): WorkspaceCatalogEntry[] {
  return current.filter((entry) => entry.entryId !== entryId);
}

export function groupWorkspaceCatalog(
  entries: WorkspaceCatalogEntry[],
): StrategyWorkspaceGroup[] {
  const groups = new Map<string, WorkspaceCatalogEntry[]>();
  for (const entry of entries) {
    const strategyId = entry.workspace.adapter.strategy_id;
    const existing = groups.get(strategyId);
    if (existing) existing.push(entry);
    else groups.set(strategyId, [entry]);
  }
  return [...groups].map(([strategyId, groupedEntries]) => ({
    strategyId,
    entries: groupedEntries,
  }));
}
