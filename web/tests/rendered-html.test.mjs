import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { access, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const templateRoot = new URL("../", import.meta.url);
const repositoryRoot = dirname(
  fileURLToPath(new URL("../../pyproject.toml", import.meta.url)),
);
const configuredPython = process.env.QUANTHUS_PYTHON || "python3";
const pythonCommand =
  configuredPython.includes("/") && !isAbsolute(configuredPython)
    ? resolve(process.cwd(), configuredPython)
    : configuredPython;
const execFileAsync = promisify(execFile);
const demoArtifactUrl = new URL(
  "../public/data/attribution-demo.v1.json",
  import.meta.url,
);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

async function loadEnvelopeModule() {
  return import(new URL("../app/artifact-envelope.ts", import.meta.url).href);
}

async function loadDisplayTermsModule() {
  return import(new URL("../app/display-terms.ts", import.meta.url).href);
}

async function loadWorkspaceCatalogModule() {
  return import(new URL("../app/workspace-catalog.ts", import.meta.url).href);
}

async function loadPublishedWorkspaceCatalogModule() {
  return import(
    new URL("../app/published-workspace-catalog.ts", import.meta.url).href
  );
}

async function loadDemoArtifact() {
  const text = await readFile(demoArtifactUrl, "utf8");
  return { text, workspace: JSON.parse(text) };
}

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

function sha256Bytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function publicationFileFetcher(publicationRoot) {
  return async (input) => {
    const url = new URL(input);
    const prefix = "/data/attribution-releases/";
    if (!url.pathname.startsWith(prefix)) {
      return new Response("Not found", { status: 404 });
    }
    const relativePath = url.pathname.slice(prefix.length);
    if (!relativePath || relativePath.split("/").some((part) => part === "..")) {
      return new Response("Not found", { status: 404 });
    }
    try {
      return new Response(await readFile(join(publicationRoot, relativePath)), {
        status: 200,
      });
    } catch {
      return new Response("Not found", { status: 404 });
    }
  };
}

test("server-renders an honest empty state before formal attribution data exists", async () => {
  const [{ workspace }, response] = await Promise.all([
    loadDemoArtifact(),
    render(),
  ]);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Quanthus 策略归因工作台<\/title>/i);
  assert.match(html, /策略归因分析/);
  assert.match(html, /尚未接入真实归因结果/);
  assert.match(html, /正式策略适配器/);
  assert.match(html, /已发布归因产物/);
  assert.match(html, /查看虚构样例/);
  assert.match(html, /人为编造的合同样例/);
  assert.doesNotMatch(html, /Strategy Attribution/);
  assert.doesNotMatch(html, /class="baseline-metric/);
  assert.doesNotMatch(html, /class="synthetic-notice/);
  assert.ok(workspace.claims.length > 0, "the opt-in fixture remains available");
  assert.doesNotMatch(
    html,
    /codex-preview|Your site is taking shape|react-loading-skeleton/i,
  );
});

test("translates standardized artifact terms without changing source identities", async () => {
  const terms = await loadDisplayTermsModule();

  assert.equal(terms.displayComponentLabel("expert.trend"), "趋势专家");
  assert.equal(terms.displayMetricLabel("total_return"), "总收益率");
  assert.equal(terms.displayConditionLabel("state.range_bound"), "震荡状态");
  assert.equal(terms.displayInterventionLabel("abstain_trend_expert"), "趋势专家暂不发表意见");
  assert.equal(terms.displayBoolean(true), "是");
  assert.equal(terms.displayFormattedValue("-27.0 pp"), "-27.0 个百分点");
  assert.equal(
    terms.displayArtifactTerm("All values are fabricated and prove contracts only."),
    "所有数值均为编造，仅用于验证归因合同。",
  );
  assert.equal(terms.displayMetricLabel("custom_metric", "自定义指标"), "自定义指标");
});

test("catalog keeps strategy workspaces isolated while supporting many strategies and runs", async () => {
  const [{ text, workspace }, catalog] = await Promise.all([
    loadDemoArtifact(),
    loadWorkspaceCatalogModule(),
  ]);
  const secondRun = {
    ...workspace,
    workspace_id: "workspace.synthetic-second-run",
    digest: `sha256:${"1".repeat(64)}`,
  };
  const secondStrategy = {
    ...workspace,
    workspace_id: "workspace.synthetic-second-strategy",
    digest: `sha256:${"2".repeat(64)}`,
    adapter: {
      ...workspace.adapter,
      strategy_id: "strategy.synthetic-second",
      strategy_version: "2.0.0",
    },
  };
  const entries = [
    catalog.createWorkspaceCatalogEntry(workspace, text, "synthetic"),
    catalog.createWorkspaceCatalogEntry(secondRun, `${text}\n`, "imported"),
    catalog.createWorkspaceCatalogEntry(secondStrategy, text, "imported"),
  ];

  const stored = catalog.upsertWorkspaceCatalogEntries([], entries);
  const groups = catalog.groupWorkspaceCatalog(stored);
  assert.equal(stored.length, 3);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].entries.length, 2);
  assert.equal(groups[1].entries.length, 1);
  assert.equal(stored[1].artifactText, `${text}\n`);

  const deduplicated = catalog.upsertWorkspaceCatalogEntries(stored, [entries[0]]);
  assert.equal(deduplicated.length, 3);
  const separateBrowserImport = catalog.createWorkspaceCatalogEntry(
    workspace,
    text,
    "imported",
    `imported:99:${workspace.digest}`,
  );
  assert.equal(
    catalog.upsertWorkspaceCatalogEntries(deduplicated, [separateBrowserImport]).length,
    4,
  );
  assert.equal(
    catalog.removeWorkspaceCatalogEntry(deduplicated, entries[0].entryId).length,
    2,
  );
  assert.throws(
    () => catalog.createWorkspaceCatalogEntry(workspace, text, "published"),
    /服务端发布元数据/,
  );
});

test("accepts the complete v1 envelope and requires all three projections", async () => {
  const [{ text }, envelope] = await Promise.all([
    loadDemoArtifact(),
    loadEnvelopeModule(),
  ]);
  const workspace = envelope.parseWorkspaceArtifact(text);

  assert.equal(workspace.schema, envelope.ATTRIBUTION_WORKSPACE_SCHEMA);
  assert.ok(workspace.questions.length > 0);
  assert.ok(workspace.experiments.length > 0);
  assert.ok(workspace.artifacts.length > 0);
  assert.ok(workspace.adapter.legal_joint_interventions.length > 0);
  assert.ok(
    workspace.adapter.legal_joint_interventions.every(
      (item) => item.component_ids.length === item.intervention_ids.length,
    ),
  );
  assert.ok(Array.isArray(workspace.condition_comparisons));
  assert.ok(Array.isArray(workspace.interaction_comparisons));
  for (const claim of workspace.claims) {
    assert.ok(claim.decision_summary);
    assert.ok(claim.reason_analysis);
    assert.ok(claim.method_evidence);
    assert.ok(claim.verification_receipts.length > 0);
    assert.equal(typeof claim.decision_summary.next_action_code, "string");
    assert.deepEqual(
      new Set(claim.decision_summary.primary_effects.map((item) => item.metric_id)),
      new Set(claim.primary_metric_ids),
    );
  }

  const missingProjection = JSON.parse(text);
  delete missingProjection.claims[0].method_evidence;
  assert.throws(
    () => envelope.parseWorkspaceArtifact(JSON.stringify(missingProjection)),
    /归因工作区 v1/,
  );

  const missingAction = JSON.parse(text);
  delete missingAction.claims[0].decision_summary.next_action_code;
  assert.throws(
    () => envelope.parseWorkspaceArtifact(JSON.stringify(missingAction)),
    /归因工作区 v1/,
  );

  const missingJointContract = JSON.parse(text);
  delete missingJointContract.adapter.legal_joint_interventions;
  assert.throws(
    () => envelope.parseWorkspaceArtifact(JSON.stringify(missingJointContract)),
    /归因工作区 v1/,
  );

  const missingArtifactClosure = JSON.parse(text);
  delete missingArtifactClosure.artifacts;
  assert.throws(
    () => envelope.parseWorkspaceArtifact(JSON.stringify(missingArtifactClosure)),
    /归因工作区 v1/,
  );

  const missingReceipt = JSON.parse(text);
  delete missingReceipt.claims[0].verification_receipts;
  assert.throws(
    () => envelope.parseWorkspaceArtifact(JSON.stringify(missingReceipt)),
    /归因工作区 v1/,
  );
});

test("failed imports preserve the current workspace and exact source text", async () => {
  const [{ text }, envelope] = await Promise.all([
    loadDemoArtifact(),
    loadEnvelopeModule(),
  ]);
  const current = {
    workspace: envelope.parseWorkspaceArtifact(text),
    artifactText: text,
  };

  const invalidJson = envelope.importWorkspaceDocument(current, "{not-json");
  assert.equal(invalidJson.ok, false);
  assert.strictEqual(invalidJson.document, current);
  assert.equal(invalidJson.document.artifactText, text);

  const unsupported = envelope.importWorkspaceDocument(
    current,
    JSON.stringify({ schema: "quanthus.strategy-attribution-workspace.v2" }),
  );
  assert.equal(unsupported.ok, false);
  assert.strictEqual(unsupported.document, current);

  const importedText = `\n${text}`;
  const imported = envelope.importWorkspaceDocument(current, importedText);
  assert.equal(imported.ok, true);
  assert.equal(imported.document.artifactText, importedText);
});

test("keeps unavailable components distinct from components without a claim", async () => {
  const [{ text }, envelope] = await Promise.all([
    loadDemoArtifact(),
    loadEnvelopeModule(),
  ]);
  const workspace = envelope.parseWorkspaceArtifact(text);

  assert.equal(
    envelope.componentPublicationState(workspace, "allocator.capital"),
    "unavailable",
  );
  assert.equal(
    envelope.componentPublicationState(workspace, "strategy.root"),
    "unpublished",
  );
  assert.equal(
    envelope.componentPublicationState(workspace, "expert.trend"),
    "claimed",
  );
  assert.equal(envelope.isSyntheticFixture(workspace), true);
  assert.deepEqual(envelope.workspaceSourceTrust(workspace, "imported"), {
    syntheticFixture: true,
    unverifiedImport: true,
    serverVerifiedPublication: false,
  });
  assert.deepEqual(envelope.workspaceSourceTrust(workspace, "synthetic"), {
    syntheticFixture: true,
    unverifiedImport: false,
    serverVerifiedPublication: false,
  });
  assert.deepEqual(envelope.workspaceSourceTrust(workspace, "published"), {
    syntheticFixture: true,
    unverifiedImport: false,
    serverVerifiedPublication: true,
  });
});

test("loads only READY server publications and keeps their trust separate", async () => {
  const [{ text, workspace }, publicationCatalog] = await Promise.all([
    loadDemoArtifact(),
    loadPublishedWorkspaceCatalogModule(),
  ]);
  const releaseId = "synthetic-release-v1";
  const catalog = {
    schema_version: publicationCatalog.ATTRIBUTION_PUBLICATION_CATALOG_SCHEMA,
    releases: [
      {
        release_id: releaseId,
        release_path: `./${releaseId}/release.json`,
        workspace_path: `./${releaseId}/workspace.json`,
        checksum_path: `./${releaseId}/CHECKSUMS.sha256`,
        ready_path: `./${releaseId}/READY`,
      },
    ],
  };
  const release = {
    schema_version: publicationCatalog.ATTRIBUTION_PUBLICATION_SCHEMA,
    release_id: releaseId,
    run_id: "synthetic-run-v1",
    source_kind: workspace.source_kind,
    synthetic_fixture: true,
    formal_strategy_evidence: false,
    workspace_digest: workspace.digest,
    digest: `sha256:${"3".repeat(64)}`,
    note: "\uFFFD",
  };
  const releaseText = JSON.stringify(release);
  const checksumText = [
    `${sha256(releaseText)}  release.json\n`,
    `${sha256(text)}  workspace.json\n`,
  ].join("");
  const responses = new Map([
    ["/data/attribution-releases/catalog.json", JSON.stringify(catalog)],
    [`/data/attribution-releases/${releaseId}/READY`, `${sha256(checksumText)}\n`],
    [
      `/data/attribution-releases/${releaseId}/CHECKSUMS.sha256`,
      checksumText,
    ],
    [
      `/data/attribution-releases/${releaseId}/release.json`,
      releaseText,
    ],
    [`/data/attribution-releases/${releaseId}/workspace.json`, text],
  ]);
  const requested = [];
  const fetcher = async (input) => {
    const url = new URL(input);
    requested.push(url.pathname);
    const body = responses.get(url.pathname);
    return body === undefined
      ? new Response("Not found", { status: 404 })
      : new Response(body, { status: 200 });
  };

  const loaded = await publicationCatalog.loadPublishedWorkspaceCatalog(
    fetcher,
    "http://localhost/data/attribution-releases/catalog.json",
  );
  assert.deepEqual(loaded.errors, []);
  assert.equal(loaded.entries.length, 1);
  assert.equal(loaded.entries[0].origin, "published");
  assert.equal(loaded.entries[0].artifactText, text);
  assert.deepEqual(loaded.entries[0].publication, {
    releaseId,
    runId: release.run_id,
    sourceKind: release.source_kind,
    syntheticFixture: true,
    formalStrategyEvidence: false,
    workspaceDigest: workspace.digest,
    releaseDigest: release.digest,
  });
  assert.ok(
    requested.indexOf(`/data/attribution-releases/${releaseId}/READY`) <
      requested.indexOf(`/data/attribution-releases/${releaseId}/workspace.json`),
  );

  responses.delete(`/data/attribution-releases/${releaseId}/READY`);
  requested.length = 0;
  const incomplete = await publicationCatalog.loadPublishedWorkspaceCatalog(
    fetcher,
    "http://localhost/data/attribution-releases/catalog.json",
  );
  assert.equal(incomplete.entries.length, 0);
  assert.equal(incomplete.errors.length, 1);
  assert.ok(!requested.includes(`/data/attribution-releases/${releaseId}/workspace.json`));

  responses.set(
    `/data/attribution-releases/${releaseId}/READY`,
    `${sha256(checksumText)}\n`,
  );
  responses.set(
    `/data/attribution-releases/${releaseId}/workspace.json`,
    `${text} `,
  );
  const tampered = await publicationCatalog.loadPublishedWorkspaceCatalog(
    fetcher,
    "http://localhost/data/attribution-releases/catalog.json",
  );
  assert.equal(tampered.entries.length, 0);
  assert.match(tampered.errors[0], /校验账本不一致/);

  responses.set(
    `/data/attribution-releases/${releaseId}/workspace.json`,
    text,
  );
  const releaseBytes = Buffer.from(releaseText, "utf8");
  const replacementBytes = Buffer.from([0xef, 0xbf, 0xbd]);
  const replacementIndex = releaseBytes.indexOf(replacementBytes);
  assert.notEqual(replacementIndex, -1);
  const collisionBytes = Buffer.concat([
    releaseBytes.subarray(0, replacementIndex),
    Buffer.from([0xff]),
    releaseBytes.subarray(replacementIndex + replacementBytes.length),
  ]);
  responses.set(
    `/data/attribution-releases/${releaseId}/release.json`,
    collisionBytes,
  );
  const byteCollision = await publicationCatalog.loadPublishedWorkspaceCatalog(
    fetcher,
    "http://localhost/data/attribution-releases/catalog.json",
  );
  assert.equal(byteCollision.entries.length, 0);
  assert.match(byteCollision.errors[0], /release\.json 与发布校验账本不一致/);
  responses.set(
    `/data/attribution-releases/${releaseId}/release.json`,
    releaseText,
  );

  const invalidWorkspaceBytes = Buffer.concat([
    Buffer.from(text.slice(0, -1), "utf8"),
    Buffer.from([0xff]),
    Buffer.from(text.slice(-1), "utf8"),
  ]);
  const invalidChecksumText = [
    `${sha256(releaseText)}  release.json\n`,
    `${sha256Bytes(invalidWorkspaceBytes)}  workspace.json\n`,
  ].join("");
  responses.set(
    `/data/attribution-releases/${releaseId}/READY`,
    `${sha256(invalidChecksumText)}\n`,
  );
  responses.set(
    `/data/attribution-releases/${releaseId}/CHECKSUMS.sha256`,
    invalidChecksumText,
  );
  responses.set(
    `/data/attribution-releases/${releaseId}/workspace.json`,
    invalidWorkspaceBytes,
  );
  const invalidUtf8 = await publicationCatalog.loadPublishedWorkspaceCatalog(
    fetcher,
    "http://localhost/data/attribution-releases/catalog.json",
  );
  assert.equal(invalidUtf8.entries.length, 0);
  assert.match(invalidUtf8.errors[0], /不是有效的 UTF-8 文本/);
});

test(
  "loads a release generated by the actual synchronous attribution pipeline",
  { timeout: 120_000 },
  async () => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), "quanthus-attribution-web-"));
    const runRoot = join(temporaryRoot, "runs");
    const publicationRoot = join(temporaryRoot, "publications");
    try {
      const { stdout } = await execFileAsync(
        pythonCommand,
        [
          "-m",
          "quanthus",
          "attribution-run",
          "--fixture",
          "synthetic-v1",
          "--run-root",
          runRoot,
          "--publication-root",
          publicationRoot,
        ],
        {
          cwd: repositoryRoot,
          env: {
            ...process.env,
            PYTHONPATH: join(repositoryRoot, "src"),
          },
          timeout: 120_000,
        },
      );
      const cliResult = JSON.parse(stdout);
      const publicationCatalog = await loadPublishedWorkspaceCatalogModule();
      const loaded = await publicationCatalog.loadPublishedWorkspaceCatalog(
        publicationFileFetcher(publicationRoot),
        "http://localhost/data/attribution-releases/catalog.json",
      );

      assert.deepEqual(loaded.errors, []);
      assert.equal(loaded.entries.length, 1);
      assert.equal(loaded.entries[0].origin, "published");
      assert.equal(loaded.entries[0].publication.releaseId, cliResult.release_id);
      assert.equal(
        loaded.entries[0].publication.workspaceDigest,
        cliResult.workspace_digest,
      );
      assert.equal(loaded.entries[0].publication.syntheticFixture, true);
      assert.equal(loaded.entries[0].publication.formalStrategyEvidence, false);
    } finally {
      await rm(temporaryRoot, { recursive: true, force: true });
    }
  },
);

test("ships an empty publication catalog instead of a default demo release", async () => {
  const publicationCatalog = await loadPublishedWorkspaceCatalogModule();
  const text = await readFile(
    new URL(
      "../public/data/attribution-releases/catalog.json",
      import.meta.url,
    ),
    "utf8",
  );
  const catalog = publicationCatalog.parsePublicationCatalog(text);
  assert.deepEqual(catalog.releases, []);
});

test("publication catalog rejects duplicate identities and non-canonical paths", async () => {
  const publicationCatalog = await loadPublishedWorkspaceCatalogModule();
  const release = {
    release_id: "release-v1",
    release_path: "./release-v1/release.json",
    workspace_path: "./release-v1/workspace.json",
    checksum_path: "./release-v1/CHECKSUMS.sha256",
    ready_path: "./release-v1/READY",
  };
  assert.throws(
    () =>
      publicationCatalog.parsePublicationCatalog(
        JSON.stringify({
          schema_version: publicationCatalog.ATTRIBUTION_PUBLICATION_CATALOG_SCHEMA,
          releases: [release, release],
        }),
      ),
    /重复发布标识/,
  );
  assert.throws(
    () =>
      publicationCatalog.parsePublicationCatalog(
        JSON.stringify({
          schema_version: publicationCatalog.ATTRIBUTION_PUBLICATION_CATALOG_SCHEMA,
          releases: [{ ...release, workspace_path: "../workspace.json" }],
        }),
      ),
    /格式不受支持/,
  );
});

test("keeps attribution math and baseline authority out of the browser", async () => {
  const [{ workspace }, source, css] = await Promise.all([
    loadDemoArtifact(),
    readFile(new URL("../app/attribution-workbench.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  const claimWithDifferentBaseline = workspace.claims.find(
    (claim) =>
      claim.method_evidence.baseline_experiment_digest !== workspace.baseline.digest,
  );
  assert.ok(claimWithDifferentBaseline);
  assert.match(source, /claim\.decision_summary\.primary_effects/);
  assert.match(source, /claim\.decision_summary\.next_action_code/);
  assert.doesNotMatch(source, /下一动作未声明/);
  assert.match(source, /claim\.reason_analysis/);
  assert.match(source, /claim\.method_evidence/);
  assert.match(source, /workspace\.experiments\.find/);
  assert.match(source, /workspace\.adapter\.legal_joint_interventions/);
  assert.match(source, /createWorkspaceCatalogEntry/);
  assert.match(source, /result\.document\.artifactText/);
  assert.match(source, /groupWorkspaceCatalog/);
  assert.match(source, /策略与回测批次/);
  assert.match(source, /分析对象/);
  assert.match(source, /sourceTrust\.unverifiedImport/);
  assert.match(source, /未验证的导入产物/);
  assert.match(source, /loadPublishedWorkspaceCatalog/);
  assert.match(source, /sourceTrust\.serverVerifiedPublication/);
  assert.match(source, /服务端已验证的合成夹具，非真实策略证据/);
  assert.match(source, /new Blob\(\[artifactText\]/);
  assert.doesNotMatch(source, /workspace\.baseline/);
  assert.doesNotMatch(source, /"total_return"|"max_drawdown"|"win_rate"/);
  assert.doesNotMatch(source, /parseFloat|parseInt|Number\s*\(/);
  assert.doesNotMatch(
    source,
    /recalculate|computeAttribution|calculateContribution/i,
  );
  assert.match(
    css,
    /\.supporting-evidence-block\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/s,
  );
  assert.match(
    css,
    /\.baseline-metrics\s*\{[^}]*repeat\(auto-fit,\s*minmax\(190px,\s*1fr\)\)/s,
  );
  for (const selector of [
    "question-manifest",
    "question-grid",
    "manifest-details-grid",
    "manifest-details",
    "manifest-content",
    "context-bindings",
    "digest-list",
  ]) {
    assert.ok(css.includes(`.${selector}`));
  }

  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
  await assert.rejects(access(new URL("public/favicon.svg", templateRoot)));
});
