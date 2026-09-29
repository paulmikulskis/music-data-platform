import { contract } from "@mdp/contracts";
import { isRecord } from "./unknown";

export const consolePaths = [
  "/ops",
  "/functions",
  "/runs",
  "/traces",
  "/targets",
  "/tenants",
  "/reference",
  "/screen",
  "/audit",
  "/runbooks",
  "/workbench",
  "/sandbox",
  "/sandboxes",
  "/explorer",
  "/queries",
  "/actions",
  "/api",
  "/rpc",
  "/status",
  "/style.css",
  "/ui.js",
  "/favicon.ico",
];
export function consolePath(path: string) {
  if (/^\/(?:rpc|api)\/showcase(?:\/|$)/.test(path)) return false;
  if (/%|\\|\/\//.test(path)) return false;
  return consolePaths.some(
    (root) =>
      path === root || (!root.includes(".") && path.startsWith(root + "/")),
  );
}
export function consoleMutationAllowed(request: Request, origin: string) {
  return (
    request.method === "GET" ||
    (request.headers.get("origin") === origin &&
      request.headers.get("sec-fetch-site") === "same-origin")
  );
}
export function consoleWeight(path: string, method: string): "heavy" | "light" {
  if (path.startsWith("/rpc/")) return rpcOperation(path).weight;
  // Unknown reads fail closed. These families can start warehouse work even on a GET.
  if (
    /^\/(?:explorer|sandbox|sandboxes|workbench|queries)(?:\/|$)/.test(path) ||
    /^\/(?:api|rpc)\/(?:explorer|sandbox|sandboxes|workbench|queries)(?:\/|$)/.test(
      path,
    )
  )
    return "heavy";
  if (!["GET", "HEAD"].includes(method)) return "light";
  return /^\/(?:ops|status|functions|style\.css|ui\.js|favicon\.ico)$/.test(
    path,
  ) || /^\/(?:runs|traces|audit|targets|tenants|runbooks)(?:\/|$)/.test(path)
    ? "light"
    : "heavy";
}
export function upstreamHeaders(
  request: Request,
  key: string,
  workbenchCookie?: string,
) {
  const headers = new Headers();
  for (const name of [
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "sec-fetch-site",
    "range",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("x-api-key", key);
  if (workbenchCookie) headers.set("cookie", workbenchCookie);
  return headers;
}
export function locationFor(
  location: string,
  upstream: string,
  publicOrigin: string,
) {
  const rawPath =
    location.replace(/^https?:\/\/[^/]+/i, "").split(/[?#]/, 1)[0] ?? "";
  if (
    /%|\\/.test(rawPath) ||
    rawPath.split("/").some((part) => part === "." || part === "..")
  )
    return null;
  const resolved = new URL(location, upstream);
  if (
    resolved.origin !== new URL(upstream).origin ||
    !consolePath(resolved.pathname)
  )
    return null;
  return `${publicOrigin}${resolved.pathname}${resolved.search}${resolved.hash}`;
}
function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => {
    if (character === "&") return "&amp;";
    if (character === "<") return "&lt;";
    if (character === ">") return "&gt;";
    if (character === '"') return "&quot;";
    return "&#39;";
  });
}
export function injectBackBar(
  html: string,
  back: string,
  proof = "",
  token?: string,
) {
  const bar = `<nav data-showcase-navigation aria-label="Showcase" style="position:sticky;top:0;z-index:2147483647;display:flex;flex-wrap:wrap;gap:12px;padding:14px 20px;background:#142026;color:#edf4f6;font:14px/1.4 sans-serif"><a style="color:inherit;text-decoration:underline" href="${escapeHtml(back)}">← Back to showcase</a><span>${escapeHtml(proof)}</span></nav>`;
  let page = html.replace(/<base\b[^>]*>/gi, "");
  // Keep the signed reading context through links and the console's polling URL.
  if (token) {
    page = page.replace(/<form\b[^>]*>/gi, (form) => {
      // Keep the originating sheet through explicit session creation too.
      const withAction = form.replace(
        /action="(\/[^"?]*)([^"]*)"/,
        (attribute, path: string, query: string) => {
          if (!consolePath(path)) return attribute;
          const url = new URL(
            path + query.replace(/&amp;/g, "&"),
            "http://console.invalid",
          );
          url.searchParams.set("showcase_proof", token);
          return `action="${escapeHtml(url.pathname + url.search)}"`;
        },
      );
      return /method=["']post["']/i.test(form)
        ? withAction
        : withAction +
            `<input type="hidden" name="showcase_proof" value="${escapeHtml(token)}">`;
    });
    page = page.replace(/href="(\/[^"]*)"/g, (attribute, href: string) => {
      const url = new URL(
        href.replace(/&amp;/g, "&"),
        "http://console.invalid",
      );
      if (!consolePath(url.pathname) || url.pathname.includes("."))
        return attribute;
      url.searchParams.set("showcase_proof", token);
      return `href="${escapeHtml(url.pathname + url.search + url.hash)}"`;
    });
  }
  const protectedBar = `<showcase-navigation style="display:block!important;position:sticky!important;top:0!important;z-index:2147483647!important;opacity:1!important"><template shadowrootmode="open">${bar}</template></showcase-navigation>`;
  return page
    .replace(
      /<head([^>]*)>/i,
      '<head$1><meta name="referrer" content="same-origin">',
    )
    .replace(/<body([^>]*)>/i, `<body$1>${protectedBar}`);
}

type Operation = { weight: "heavy" | "light"; read: boolean };
const workbenchReads = new Set([
  "sandboxStatus",
  "queries",
  "history",
  "explain",
  "lineage",
  "status",
  "result",
  "artifactContent",
  "artifact",
  "models",
]);
function rpcOperation(path: string): Operation {
  const parts = path.slice("/rpc/".length).split("/");
  let procedure: unknown = contract;
  for (const part of parts) {
    if (!isRecord(procedure) || !Object.hasOwn(procedure, part))
      return { weight: "heavy", read: false };
    procedure = procedure[part];
  }
  const internals = isRecord(procedure) ? procedure["~orpc"] : undefined;
  const route = isRecord(internals) ? internals.route : undefined;
  if (!route) return { weight: "heavy", read: false };
  const method = isRecord(route) ? route.method : undefined;
  const heavy = parts[0] === "workbench" || path === "/rpc/functions/page";
  return {
    weight: heavy ? "heavy" : "light",
    read:
      method === "GET" ||
      (parts[0] === "workbench" && workbenchReads.has(parts[1]!)),
  };
}
export function consoleOperation(path: string, method: string): Operation {
  if (path.startsWith("/rpc/")) return rpcOperation(path);
  return { weight: consoleWeight(path, method), read: method === "GET" };
}
