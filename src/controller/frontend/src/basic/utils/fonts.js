// Frontend fonts: a body font and a heading font, chosen on the Frontend
// settings tab (config.frontend.font_body / font_heading) and applied on
// every client by useControllerTheme via --font-body / --font-heading.
//
// Built-in fonts are system font stacks: no files, they use whatever the
// viewing device has installed (stacks after modernfontstacks.com). Added
// fonts are uploaded by an operator's browser and served by the controller
// from /fonts/<id>/<file> (src/controller/fonts.py, FontStore), so they work
// in browsers with no internet access. The browser does the fetching: from a
// Google Fonts link (Google serves the CSS and the files with CORS), or from
// font files dropped onto the page.

import socket from "/src/socket";
import { getUploadToken } from "./uploadToken";

export const DEFAULT_FONT_ID = "system";

// Mirror of RESERVED_IDS in src/controller/fonts.py -- keep in sync.
export const BUILTIN_FONTS = [
  {
    id: "system", name: "System",
    stack: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', "
      + "'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif",
  },
  {
    id: "humanist", name: "Humanist",
    stack: "Seravek, 'Gill Sans Nova', Ubuntu, Calibri, 'DejaVu Sans', source-sans-pro, sans-serif",
  },
  {
    id: "geometric", name: "Geometric",
    stack: "Avenir, Montserrat, Corbel, 'URW Gothic', source-sans-pro, sans-serif",
  },
  {
    id: "rounded", name: "Rounded",
    stack: "ui-rounded, 'Hiragino Maru Gothic ProN', Quicksand, Comfortaa, Manjari, "
      + "'Arial Rounded MT', 'Arial Rounded MT Bold', Calibri, source-sans-pro, sans-serif",
  },
  {
    id: "industrial", name: "Industrial",
    stack: "Bahnschrift, 'DIN Alternate', 'Franklin Gothic Medium', 'Nimbus Sans Narrow', "
      + "sans-serif-condensed, sans-serif",
  },
  {
    id: "transitional", name: "Serif",
    stack: "Charter, 'Bitstream Charter', 'Sitka Text', Cambria, serif",
  },
  {
    id: "old-style", name: "Old-style serif",
    stack: "'Iowan Old Style', 'Palatino Linotype', 'URW Palladio L', P052, serif",
  },
  {
    id: "mono", name: "Monospace",
    stack: "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, "
      + "'DejaVu Sans Mono', monospace",
  },
];

const SYSTEM_STACK = BUILTIN_FONTS[0].stack;

/** Built-ins then added fonts, each as {id, name, builtin, font?}. */
export function listFonts(customFonts) {
  return [
    ...BUILTIN_FONTS.map((f) => ({ id: f.id, name: f.name, builtin: true })),
    ...(customFonts || []).map((f) => ({ id: f.id, name: f.name, builtin: false, font: f })),
  ];
}

/** The CSS font-family value for a font id; unknown ids fall back to System. */
export function fontStack(id, customFonts) {
  const builtin = BUILTIN_FONTS.find((f) => f.id === id);
  if (builtin) return builtin.stack;
  const custom = (customFonts || []).find((f) => f.id === id);
  return custom ? `"${custom.name}", ${SYSTEM_STACK}` : SYSTEM_STACK;
}

/** {body, heading} font-family values from config.frontend. An empty
 *  font_heading means "same as body". */
export function frontendFonts(fe) {
  const custom = fe?.custom_fonts;
  const body = fontStack(fe?.font_body || DEFAULT_FONT_ID, custom);
  const heading = fe?.font_heading ? fontStack(fe.font_heading, custom) : body;
  return { body, heading };
}

/** @font-face rules for every added font. Browsers only download a face
 *  when text uses it, so declaring them all is cheap. Names and descriptors
 *  are validated by FontStore before they're stored. */
export function fontFaceCss(customFonts) {
  return (customFonts || []).flatMap((font) => font.faces.map((face) => {
    const range = face.unicode_range ? ` unicode-range: ${face.unicode_range};` : "";
    return `@font-face { font-family: "${font.name}"; `
      + `src: url("${face.url || `/fonts/${font.id}/${face.file}`}") format("${face.format}"); `
      + `font-weight: ${face.weight}; font-style: ${face.style}; font-display: swap;${range} }`;
  })).join("\n");
}

// ---------------------------------------------------------------------------
// Getting font files into the browser
// ---------------------------------------------------------------------------

const GOOGLE_CSS_RE = /https:\/\/fonts\.googleapis\.com\/css2?\?[^\s"'<>)]+/;
const DEFAULT_SUBSETS = ["latin", "latin-ext"];

const GOOGLE_SPECIMEN_RE = /https:\/\/fonts\.google\.com\/specimen\/([^/?#\s"'<>]+)/;

// Every weight, upright and italic. A specimen link names only the family,
// so the request asks for all of them on Google's older `css` API, which (unlike
// css2) leaves out the ones a family doesn't have instead of refusing.
const ALL_STYLES = [100, 200, 300, 400, 500, 600, 700, 800, 900]
  .flatMap((w) => [`${w}`, `${w}i`]).join(",");

/** The Google Fonts CSS link for whatever was pasted: a family's page
 *  (fonts.google.com/specimen/Name), which fetches every weight and italic it
 *  has and nothing else; or a CSS link, its <link> embed snippet or the
 *  @import one, taken as is. Throws if there's neither. */
export function googleFontsUrl(text) {
  const t = (text || "").replace(/&amp;/g, "&");
  const css = t.match(GOOGLE_CSS_RE);
  if (css) return css[0];
  const spec = t.match(GOOGLE_SPECIMEN_RE);
  if (spec) {
    let family;
    try { family = decodeURIComponent(spec[1].replace(/\+/g, " ")); } catch { family = ""; }
    family = family.trim().replace(/\s+/g, "+");
    if (/^[A-Za-z0-9+]+$/.test(family)) {
      return `https://fonts.googleapis.com/css?family=${family}:${ALL_STYLES}&display=swap`;
    }
  }
  throw new Error(
    "Paste a Google Fonts page link (fonts.google.com/specimen/…), "
    + "or the <link> embed code from Google Fonts.");
}

function cssProp(block, name) {
  const m = block.match(new RegExp(`${name}\\s*:\\s*([^;]+);`));
  return m ? m[1].trim() : "";
}

/**
 * Fetch a Google Fonts link in this browser and return one entry per family:
 * [{family, faces: [{data: ArrayBuffer, weight, style, unicodeRange, bytes}]}].
 * Keeps the latin and latin-ext subsets (most of the size of a family is
 * other scripts), unless a family has none, e.g. one for another script.
 */
// A variable family comes back as one @font-face per requested weight, all
// pointing at the same file; fold those into one face with a weight range,
// so the file is downloaded once and keeps every weight it covers.
function mergeWeights(blocks) {
  const groups = new Map();
  for (const b of blocks) {
    const k = `${b.url}|${b.style}|${b.unicodeRange || ""}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(b);
  }
  return [...groups.values()].map((g) => {
    if (g.length === 1) return g[0];
    const ws = g.flatMap((b) => b.weight.split(/\s+/).map(Number)).filter(Number.isFinite);
    const lo = Math.min(...ws);
    const hi = Math.max(...ws);
    return { ...g[0], weight: lo === hi ? `${lo}` : `${lo} ${hi}` };
  });
}

export async function fetchGoogleFonts(url, { subsets = DEFAULT_SUBSETS } = {}) {
  let css;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = new Error(`Google Fonts answered ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    css = await resp.text();
  } catch (e) {
    if (e.status === 400) throw new Error("Google Fonts doesn't know that family. Check the link.");
    throw new Error(
      `Couldn't reach Google Fonts from this browser (${e.message}). `
      + "Check the link, or download the font files and drop them in instead.");
  }

  const blocks = [];
  const re = /(?:\/\*\s*([^*]+?)\s*\*\/\s*)?@font-face\s*\{([^}]*)\}/g;
  let m;
  while ((m = re.exec(css))) {
    const body = m[2];
    const src = body.match(/url\((["']?)(https:\/\/fonts\.gstatic\.com\/[^)"']+)\1\)/);
    if (!src) continue;
    blocks.push({
      subset: m[1] || null,
      family: cssProp(body, "font-family").replace(/^["']|["']$/g, ""),
      weight: cssProp(body, "font-weight") || "400",
      style: cssProp(body, "font-style") || "normal",
      unicodeRange: cssProp(body, "unicode-range") || null,
      url: src[2],
    });
  }
  if (!blocks.length) throw new Error("That link didn't return any fonts.");

  const byFamily = new Map();
  for (const b of blocks) {
    if (!byFamily.has(b.family)) byFamily.set(b.family, []);
    byFamily.get(b.family).push(b);
  }

  const families = [];
  for (const [family, all] of byFamily) {
    const wanted = all.filter((b) => !b.subset || subsets.includes(b.subset));
    const chosen = mergeWeights(wanted.length ? wanted : all);
    const faces = await Promise.all(chosen.map(async (b) => {
      const resp = await fetch(b.url);
      if (!resp.ok) throw new Error(`Couldn't download ${family} (${resp.status})`);
      const data = await resp.arrayBuffer();
      return { data, weight: b.weight, style: b.style, unicodeRange: b.unicodeRange,
        bytes: data.byteLength, format: formatFromName(b.url) };
    }));
    families.push({ family, faces });
  }
  return families;
}

const FONT_EXT_RE = /\.(woff2|woff|ttf|otf)$/i;
const FORMAT_BY_EXT = { woff2: "woff2", woff: "woff", ttf: "truetype", otf: "opentype" };

/** CSS format() name from a file name or URL; woff2 when there's no extension. */
export function formatFromName(name) {
  const m = (name || "").split("?")[0].match(FONT_EXT_RE);
  return m ? FORMAT_BY_EXT[m[1].toLowerCase()] : "woff2";
}
const WEIGHT_WORDS = [
  [/(extra|ultra)[-_ ]?light/i, 200], [/(semi|demi)[-_ ]?bold/i, 600],
  [/(extra|ultra)[-_ ]?bold/i, 800], [/thin|hairline/i, 100], [/light/i, 300],
  [/medium/i, 500], [/bold/i, 700], [/black|heavy/i, 900],
];

/** Weight and style from a font file name, e.g. "OpenSans-SemiBoldItalic.ttf"
 *  or "Inter-VariableFont_opsz,wght.ttf" (a variable font: 100-900). */
export function faceFromFileName(name) {
  const stem = name.replace(FONT_EXT_RE, "");
  const style = /italic|oblique/i.test(stem) ? "italic" : "normal";
  if (/variable|\[.*wght.*\]/i.test(stem)) return { weight: "100 900", style };
  const hit = WEIGHT_WORDS.find(([re]) => re.test(stem.split(/[-_ ]/).slice(1).join(" ") || stem));
  return { weight: String(hit ? hit[1] : 400), style };
}

/** A family name from a font file name: "OpenSans-Bold.ttf" -> "Open Sans". */
export function familyFromFileName(name) {
  const stem = name.replace(FONT_EXT_RE, "").split(/[-_[]/)[0];
  return stem.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/[^A-Za-z0-9 _-]/g, "").trim();
}

/** Dropped/picked files -> {family, faces} with weights guessed from names.
 *  Non-font files are skipped. */
export async function facesFromFiles(fileList) {
  const files = [...fileList].filter((f) => FONT_EXT_RE.test(f.name));
  if (!files.length) throw new Error("No font files there (.woff2, .woff, .ttf or .otf).");
  const faces = await Promise.all(files.map(async (f) => ({
    ...faceFromFileName(f.name),
    unicodeRange: null,
    data: await f.arrayBuffer(),
    bytes: f.size,
    format: formatFromName(f.name),
    fileName: f.name,
  })));
  return { family: familyFromFileName(files[0].name), faces };
}

/** Make a family name acceptable to FontStore (letters, digits, spaces, - _). */
export function cleanFontName(name) {
  return (name || "").replace(/[^A-Za-z0-9 _-]/g, "").replace(/\s+/g, " ").trim().slice(0, 60);
}

/**
 * Upload one font to the controller. `source` is {kind: "google", url} or
 * {kind: "files"}. Resolves to the stored font's metadata; the controller
 * also broadcasts the new font list to every client.
 */
export async function uploadFont({ name, source, faces }) {
  if (import.meta.env.VITE_MOCK === "1") return mockUploadFont({ name, source, faces });
  const token = await getUploadToken();
  const form = new FormData();
  form.append("meta", JSON.stringify({
    name,
    source,
    faces: faces.map((f, i) => ({
      field: `f${i}`, weight: f.weight, style: f.style, unicode_range: f.unicodeRange,
    })),
  }));
  faces.forEach((f, i) => form.append(`f${i}`, new Blob([f.data]), `f${i}`));
  const resp = await fetch(`/api/fonts?token=${encodeURIComponent(token)}`, {
    method: "POST", body: form,
  });
  let body = {};
  try { body = await resp.json(); } catch { /* non-JSON error page */ }
  if (!resp.ok) throw new Error(body.error || `Upload failed (${resp.status})`);
  return body.font;
}

// npm run dev:mock has no controller to POST to: hand the mock socket the
// metadata with blob: URLs so the font still renders.
function mockUploadFont({ name, source, faces }) {
  return new Promise((resolve) => {
    const done = (d) => { socket.off("mock_font_uploaded", done); resolve(d.font); };
    socket.on("mock_font_uploaded", done);
    socket.emit("mock_upload_font", {
      name, source,
      faces: faces.map((f) => ({
        weight: f.weight, style: f.style, unicodeRange: f.unicodeRange, bytes: f.bytes,
        format: f.format, url: URL.createObjectURL(new Blob([f.data])),
      })),
    });
  });
}
