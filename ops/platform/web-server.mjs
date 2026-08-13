import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";

import application from "./dist/server/index.js";

const serverRoot = dirname(fileURLToPath(import.meta.url));
const clientRoot = resolve(serverRoot, "dist/client");
const productRoot = resolve(serverRoot, "product-dist");
const publicationRoot = resolve(
  process.env.QUANTHUS_ATTRIBUTION_PUBLICATION_ROOT ||
    "/data/attribution-releases",
);
const apiOrigin = new URL(
  process.env.QUANTHUS_PLATFORM_API_ORIGIN || "http://127.0.0.1:8080",
);
const port = Number.parseInt(process.env.PORT || "3100", 10);
const proxiedPaths = ["/api/", "/health", "/ready", "/metrics", "/openapi.json"];

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
};

function isProxied(pathname) {
  return proxiedPaths.some((path) =>
    path.endsWith("/") ? pathname.startsWith(path) : pathname === path,
  );
}

function safeFile(root, relativePath) {
  let decoded;
  try {
    decoded = decodeURIComponent(relativePath);
  } catch {
    return null;
  }
  if (!decoded || decoded.split("/").some((part) => !part || part === "..")) {
    return null;
  }
  const candidate = resolve(root, decoded);
  return candidate.startsWith(`${root}${sep}`) ? candidate : null;
}

async function fileResponse(path, method) {
  try {
    const metadata = await stat(path);
    if (!metadata.isFile()) return null;
    const headers = {
      "Cache-Control": path.includes(`${sep}assets${sep}`)
        ? "public, max-age=31536000, immutable"
        : "no-store",
      "Content-Length": String(metadata.size),
      "Content-Type": contentTypes[extname(path)] || "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
    };
    return new Response(method === "HEAD" ? null : Readable.toWeb(createReadStream(path)), {
      status: 200,
      headers,
    });
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "EACCES") return null;
    throw error;
  }
}

async function staticResponse(url, method) {
  const productAssetPrefix = "/product-assets/";
  if (url.pathname.startsWith(productAssetPrefix)) {
    const path = safeFile(productRoot, url.pathname.slice(productAssetPrefix.length));
    return path ? fileResponse(path, method) : null;
  }
  const publicationPrefix = "/data/attribution-releases/";
  if (url.pathname.startsWith(publicationPrefix)) {
    const path = safeFile(publicationRoot, url.pathname.slice(publicationPrefix.length));
    return path ? fileResponse(path, method) : null;
  }
  const path = safeFile(clientRoot, url.pathname.replace(/^\//, ""));
  return path ? fileResponse(path, method) : null;
}

function isProductRoute(pathname) {
  return ["/", "/research", "/candidates", "/attribution"].includes(pathname) || pathname.startsWith("/candidates/");
}

async function proxyRequest(request, url) {
  const target = new URL(`${url.pathname}${url.search}`, apiOrigin);
  const headers = new Headers(request.headers);
  headers.delete("connection");
  headers.delete("host");
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    duplex: "half",
    redirect: "manual",
  });
  const responseHeaders = new Headers(response.headers);
  responseHeaders.set("Cache-Control", "no-store");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

function nodeRequest(request, origin) {
  const url = new URL(request.url || "/", origin);
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (Array.isArray(value)) value.forEach((item) => headers.append(name, item));
    else if (value !== undefined) headers.set(name, value);
  }
  const init = { method: request.method, headers };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = Readable.toWeb(request);
    init.duplex = "half";
  }
  return new Request(url, init);
}

async function handle(request) {
  const url = new URL(request.url);
  if (isProxied(url.pathname)) return proxyRequest(request, url);
  const asset = await staticResponse(url, request.method);
  if (asset) return asset;
  if (isProductRoute(url.pathname)) {
    return fileResponse(resolve(productRoot, "index.html"), request.method);
  }
  return application.fetch(
    request,
    {
      ASSETS: {
        fetch: async (assetRequest) =>
          (await staticResponse(new URL(assetRequest.url), assetRequest.method)) ||
          new Response("Not found", { status: 404 }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

const server = createServer(async (incoming, outgoing) => {
  try {
    const request = nodeRequest(incoming, `http://${incoming.headers.host || "localhost"}`);
    const response = await handle(request);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    if (response.body) Readable.fromWeb(response.body).pipe(outgoing);
    else outgoing.end();
  } catch (error) {
    console.error("platform_web_request_failed", error);
    outgoing.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    outgoing.end("Platform Web unavailable");
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Quanthus Platform Web listening on 0.0.0.0:${port}`);
});
