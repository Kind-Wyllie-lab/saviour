// docs/readthedocs/ at the repo root is the single source of truth for user
// docs (also published in full at https://saviour.readthedocs.io via
// mkdocs) — this pulls the raw markdown and images in at build time so the
// in-app Guide page renders that exact content rather than a duplicate copy
// hand-maintained in JSX.
//
// The relative path below is deep because this file lives inside the Vite
// package (src/controller/frontend/) while docs/readthedocs/ lives at the
// repo root — five levels up from here.
const rawDocs = import.meta.glob("../../../../../docs/readthedocs/**/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

const rawImages = import.meta.glob("../../../../../docs/readthedocs/**/images/*", {
  query: "?url",
  import: "default",
  eager: true,
});

const GLOB_PREFIX = "../../../../../docs/readthedocs/";

function stripPrefix(globKey) {
  return globKey.slice(GLOB_PREFIX.length);
}

// docId -> raw markdown source, e.g. "getting_started", "about/contributing".
export const docs = Object.fromEntries(
  Object.entries(rawDocs).map(([key, content]) => [
    stripPrefix(key).replace(/\.md$/, ""),
    content,
  ])
);

// "images/foo.png" -> resolved built asset URL.
export const docImages = Object.fromEntries(
  Object.entries(rawImages).map(([key, url]) => [stripPrefix(key), url])
);

// The About sub-pages are combined onto one in-app tab (see Guide.jsx), so
// any link/id under about/ should resolve to that single tab.
export function tabIdFor(docId) {
  return docId.startsWith("about/") ? "about" : docId;
}

// Resolves a markdown link href (which may be relative to the *linking*
// page's own directory, per normal web/mkdocs link semantics) against the
// page it appears on. Returns one of:
//   { type: "external", href }        - http(s)/mailto/etc, open normally
//   { type: "anchor", href }          - "#foo" in-page anchor
//   { type: "internal", tabId, hash } - another doc page, switch tabs to it
export function resolveDocLink(href, currentDocId) {
  if (!href) return { type: "anchor", href: "#" };
  if (href.startsWith("#")) return { type: "anchor", href };
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { type: "external", href };

  const currentDir = currentDocId.includes("/")
    ? currentDocId.slice(0, currentDocId.lastIndexOf("/") + 1)
    : "";
  // URL's relative-resolution algorithm (handles "../", "./", etc.) is
  // reused here rather than hand-rolling path-segment math — the base is a
  // throwaway placeholder origin, only .pathname/.hash below are used.
  const url = new URL(href, `https://docs.local/${currentDir}`);
  const path = url.pathname.replace(/^\//, "").replace(/\.md$/, "");
  return { type: "internal", tabId: tabIdFor(path || "index"), hash: url.hash };
}

// Same relative-resolution logic, for markdown image src values.
export function resolveDocImage(src, currentDocId) {
  const currentDir = currentDocId.includes("/")
    ? currentDocId.slice(0, currentDocId.lastIndexOf("/") + 1)
    : "";
  const url = new URL(src, `https://docs.local/${currentDir}`);
  const path = url.pathname.replace(/^\//, "");
  return docImages[path] ?? src;
}
