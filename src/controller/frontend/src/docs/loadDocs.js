import GithubSlugger from "github-slugger";

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

const HEADING_RE = /^(#{1,3})\s+(.+)$/;

function stripInlineMarkdown(text) {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
}

// Builds an "on this page" outline for the given docIds, in the order
// they're rendered. Slugging must exactly match what rehype-slug produces
// in the actual DOM (react-markdown's own pass), or the jump-links below
// point at nothing:
//   - each docId gets its own fresh GithubSlugger instance, since each
//     DocPage is a separate <ReactMarkdown> render with its own fresh
//     rehype-slug scope (duplicate heading text within ONE doc gets -1/-2
//     suffixes; across docs it doesn't).
//   - slug() is called for every heading line regardless of level, since
//     rehype-slug slugs every heading (H1-H6) it sees — skipping a level
//     here without still calling slug() would desync the counter from what
//     actually happened in the render.
// H1s are only included when multiple docIds are passed (the "About" tab
// combines 3 separate documents, so each one's own title is a meaningful,
// distinct anchor); for a single doc the H1 just repeats the tab label.
export function extractOutline(docIds) {
  const entries = [];
  const minLevel = docIds.length > 1 ? 1 : 2;
  for (const docId of docIds) {
    const content = docs[docId];
    if (content == null) continue;
    const slugger = new GithubSlugger();
    let inFence = false;
    for (const rawLine of content.split("\n")) {
      if (/^```/.test(rawLine.trim())) {
        inFence = !inFence;
        continue;
      }
      if (inFence) continue;
      const m = HEADING_RE.exec(rawLine);
      if (!m) continue;
      const level = m[1].length;
      const text = stripInlineMarkdown(m[2]);
      const id = slugger.slug(text);
      if (level < minLevel) continue;
      entries.push({ level, text, id, docId });
    }
  }
  return entries;
}
