// Frontend colour themes.
//
// A theme supplies a full CSS-custom-property map for BOTH light and dark
// mode; config.frontend.dark_mode selects which half is applied at runtime
// (useControllerTheme.js injects the chosen half into a <style> tag). This
// replaces the old single accent_color picker on the Frontend settings tab --
// that field is still honoured for backwards-compat when no theme_id is set
// (see useControllerTheme.js).
//
// The tokens below mirror the two blocks in index.css (:root for light,
// body.dark-mode for dark). index.css stays the static fallback for the
// brief window before the config arrives over the socket, and it remains the
// sole definition of --ready-color / --recording-color, which are semantic
// status colours (blue = ready, red = recording) and deliberately NOT
// themeable.
//
// To add a built-in theme: append an entry with a unique `id`, a display
// `name`, and a `light` + `dark` map covering THEME_TOKENS. Rebuild the
// frontend (npm run build).
//
// Operators add their own themes at runtime by pasting a Coolors palette
// (ThemeImportModal -> parsePalette + themeFromPalette below). Those are
// stored one JSON file each on the controller (src/controller/themes.py,
// ThemeStore), delivered back in every controller_config_response as
// config.frontend.custom_themes, and merged in by listThemes() / resolveTheme().

export const THEME_TOKENS = [
  "--bg-color",
  "--card-bg-color",
  "--text-color",
  "--secondary-text-color",
  "--accent-color",
  "--accent-color-alt",
  "--border-color",
  "--button-color",
];

export const DEFAULT_THEME_ID = "default";

export const BUILTIN_THEMES = [
  {
    // Verbatim reproduction of index.css's :root / body.dark-mode blocks, so
    // an install that never picks another theme renders pixel-identically to
    // before this picker existed.
    id: "default",
    name: "Default",
    light: {
      "--bg-color": "#f5f5f5",
      "--card-bg-color": "#ffffff",
      "--text-color": "#111111",
      "--secondary-text-color": "#555555",
      "--accent-color": "#6495ed",
      "--accent-color-alt": "#041e42",
      "--border-color": "#dddddd",
      "--button-color": "#007bff",
    },
    dark: {
      "--bg-color": "#1e1e1e",
      "--card-bg-color": "#2a2a2a",
      "--text-color": "#e0e0e0",
      "--secondary-text-color": "#a0a0a0",
      "--accent-color": "#6495ed",
      "--accent-color-alt": "#041e42",
      "--border-color": "#444444",
      "--button-color": "#007bff",
    },
  },
  {
    // SIDB brand palette (supplied): #865c44 #d67a3e #54a0c6 #436e85 #313e49
    // -- brown, orange helix, letterform blue, slate, charcoal disc. The five
    // are all mid-tone brand colours, so light/dark neutrals are derived from
    // the charcoal/slate end and the blue is the accent in both modes.
    id: "sidb",
    name: "SIDB",
    light: {
      "--bg-color": "#f2f5f7",
      "--card-bg-color": "#ffffff",
      "--text-color": "#313e49",
      "--secondary-text-color": "#436e85",
      "--accent-color": "#54a0c6",
      "--accent-color-alt": "#865c44",
      "--border-color": "#d5dfe5",
      "--button-color": "#54a0c6",
    },
    dark: {
      "--bg-color": "#1e272f",
      "--card-bg-color": "#2a3742",
      "--text-color": "#e7edf1",
      "--secondary-text-color": "#9db0bd",
      "--accent-color": "#54a0c6",
      "--accent-color-alt": "#d67a3e",
      "--border-color": "#3b4a57",
      "--button-color": "#54a0c6",
    },
  },
  {
    // University of Edinburgh brand palette (supplied):
    // #fefefe #627d9c #b2bfce #193b69 #da0244 -- near-white, steel blue,
    // blue-grey, University navy, crest red. Navy is body text / primary in
    // light mode; in dark mode it's lifted to a pale blue (pure navy vanishes
    // on a dark ground) while the crest red stays red.
    id: "uofe",
    name: "UofE",
    light: {
      "--bg-color": "#f1f4f7",
      "--card-bg-color": "#fefefe",
      "--text-color": "#193b69",
      "--secondary-text-color": "#627d9c",
      "--accent-color": "#193b69",
      "--accent-color-alt": "#da0244",
      "--border-color": "#b2bfce",
      "--button-color": "#193b69",
    },
    dark: {
      "--bg-color": "#161a22",
      "--card-bg-color": "#1f2530",
      "--text-color": "#e9edf2",
      "--secondary-text-color": "#9fb0c2",
      "--accent-color": "#7aa0c9",
      "--accent-color-alt": "#da0244",
      "--border-color": "#333c4a",
      "--button-color": "#7aa0c9",
    },
  },
  {
    // Wood -- Coolors palette (supplied):
    // #606c38 #283618 #fefae0 #dda15e #bc6c25 -- olive, dark forest, cream,
    // sand, burnt orange. Cream paper / forest text in light; the dark
    // variant is built down from the forest green.
    id: "wood",
    name: "Wood",
    light: {
      "--bg-color": "#fefae0",
      "--card-bg-color": "#fffef7",
      "--text-color": "#283618",
      "--secondary-text-color": "#606c38",
      "--accent-color": "#606c38",
      "--accent-color-alt": "#bc6c25",
      "--border-color": "#e6ddba",
      "--button-color": "#606c38",
    },
    dark: {
      "--bg-color": "#181d10",
      "--card-bg-color": "#232b16",
      "--text-color": "#f2eecd",
      "--secondary-text-color": "#b3b98f",
      "--accent-color": "#8a9850",
      "--accent-color-alt": "#dda15e",
      "--border-color": "#3a4222",
      "--button-color": "#8a9850",
    },
  },
  {
    // Kind -- Coolors palette (supplied):
    // #335c67 #fff3b0 #e09f3e #9e2a2b #540b0e -- teal, pale yellow, amber,
    // brick, maroon. Teal is the anchor/accent, amber the warm secondary;
    // pale-yellow-tinted paper in light, dark teal-grey in dark.
    id: "kind",
    name: "Kind",
    light: {
      "--bg-color": "#fbf4de",
      "--card-bg-color": "#fffdf7",
      "--text-color": "#2f2321",
      "--secondary-text-color": "#6b5a4a",
      "--accent-color": "#335c67",
      "--accent-color-alt": "#e09f3e",
      "--border-color": "#ece0c0",
      "--button-color": "#335c67",
    },
    dark: {
      "--bg-color": "#1a2124",
      "--card-bg-color": "#232c30",
      "--text-color": "#f3ecd6",
      "--secondary-text-color": "#a9b3b3",
      "--accent-color": "#5b93a1",
      "--accent-color-alt": "#e09f3e",
      "--border-color": "#33403f",
      "--button-color": "#5b93a1",
    },
  },
  {
    // Pagan -- Coolors palette (supplied):
    // #4f000b #720026 #ce4257 #ff7f51 #ff9b54 -- a dark-wine to warm-orange
    // ramp, no neutrals. Wine is body text / dark-mode ground; the rose and
    // coral ends are the accents.
    id: "pagan",
    name: "Pagan",
    light: {
      "--bg-color": "#fff5f0",
      "--card-bg-color": "#fffdfb",
      "--text-color": "#4f000b",
      "--secondary-text-color": "#87343c",
      "--accent-color": "#ce4257",
      "--accent-color-alt": "#ff7f51",
      "--border-color": "#f0d6cc",
      "--button-color": "#ce4257",
    },
    dark: {
      "--bg-color": "#1b0508",
      "--card-bg-color": "#2a0b10",
      "--text-color": "#ffe9de",
      "--secondary-text-color": "#d69f97",
      "--accent-color": "#ff7f51",
      "--accent-color-alt": "#ff9b54",
      "--border-color": "#45161c",
      "--button-color": "#ff7f51",
    },
  },
];

/** Built-in themes followed by any operator-defined custom themes. */
export function listThemes(customThemes) {
  const extra = Array.isArray(customThemes) ? customThemes : [];
  return [...BUILTIN_THEMES, ...extra];
}

/** The theme matching `themeId`, falling back to Default. */
export function resolveTheme(themeId, customThemes) {
  return (
    listThemes(customThemes).find((t) => t.id === themeId) || BUILTIN_THEMES[0]
  );
}

/**
 * The token map for `theme` in the given mode, restricted to known
 * THEME_TOKENS keys with a truthy value — safe to spread straight into a
 * style declaration or an inline style object.
 */
export function themeTokens(theme, darkMode) {
  const map = (darkMode ? theme?.dark : theme?.light) || {};
  return THEME_TOKENS.reduce((acc, key) => {
    if (map[key]) acc[key] = map[key];
    return acc;
  }, {});
}

// ─── Coolors palette import ──────────────────────────────────────────────
// Turn a flat list of palette colours (pasted as a Coolors URL or a bare hex
// list) into a full light+dark theme: the light map lightens the palette's
// neutrals, the dark map darkens them, and accents are pulled from the most
// vivid colours with a contrast fix per mode. The saved .json is
// hand-editable for anything the derivation gets slightly wrong.

const HEX6 = /[0-9a-fA-F]{6}/g;

/** Extract `#rrggbb` colours from a Coolors URL or a space/comma/dash list.
 *  Returns null if fewer than 3 were found. */
export function parsePalette(input) {
  if (typeof input !== "string") return null;
  // Strip a leading scheme+host so "https://" etc. can't contribute hex runs.
  const body = input.replace(/^https?:\/\/[^/]+/i, "");
  const found = body.match(HEX6);
  if (!found || found.length < 3) return null;
  return found.slice(0, 8).map((h) => `#${h.toLowerCase()}`);
}

function rgb(hexStr) {
  let h = hexStr.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}
function toHex({ r, g, b }) {
  const c = (n) =>
    Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}
function mix(a, b, t) {
  const x = rgb(a);
  const y = rgb(b);
  return toHex({
    r: x.r + (y.r - x.r) * t,
    g: x.g + (y.g - x.g) * t,
    b: x.b + (y.b - x.b) * t,
  });
}
function relLum(hexStr) {
  const { r, g, b } = rgb(hexStr);
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a, b) {
  const l1 = relLum(a), l2 = relLum(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
function saturation(hexStr) {
  const { r, g, b } = rgb(hexStr);
  const max = Math.max(r, g, b) / 255, min = Math.min(r, g, b) / 255;
  if (max === min) return 0;
  const l = (max + min) / 2;
  return l > 0.5 ? (max - min) / (2 - max - min) : (max - min) / (max + min);
}
/** Mix `hexStr` toward white or black until its luminance ≈ target. */
function withLum(hexStr, target) {
  const toWhite = relLum(hexStr) < target;
  const pole = toWhite ? "#ffffff" : "#000000";
  let lo = 0;
  let hi = 1;
  let out = hexStr;
  for (let i = 0; i < 14; i++) {
    const t = (lo + hi) / 2;
    out = mix(hexStr, pole, t);
    const tooDark = relLum(out) < target;
    if (toWhite === tooDark) lo = t;
    else hi = t;
  }
  return out;
}
/** Nudge `fg` toward the readable pole until it clears `min` contrast on `bg`. */
function ensureContrast(fg, bg, min) {
  const pole = relLum(bg) < 0.5 ? "#ffffff" : "#000000";
  let out = fg;
  for (let i = 0; i < 16 && contrast(out, bg) < min; i++) {
    out = mix(out, pole, 0.08);
  }
  return out;
}

function pickAccents(hexes) {
  // Prefer vivid mid-tone colours; a very dark or very pale colour makes a
  // poor accent even at high saturation.
  const ranked = [...hexes].sort(
    (a, b) => saturation(b) * (1 - Math.abs(relLum(b) - 0.45))
            - saturation(a) * (1 - Math.abs(relLum(a) - 0.45)),
  );
  return [ranked[0], ranked[1] || ranked[0]];
}

function buildModeMap(hexes, targetMode) {
  const byLum = [...hexes].sort((a, b) => relLum(a) - relLum(b));
  const darkest = byLum[0];
  const lightest = byLum[byLum.length - 1];
  const [a1, a2] = pickAccents(hexes);

  if (targetMode === "light") {
    const bg = withLum(lightest, 0.955);
    const card = withLum(lightest, 0.99);
    const text = withLum(darkest, 0.06);
    return {
      "--bg-color": bg,
      "--card-bg-color": card,
      "--text-color": text,
      "--secondary-text-color": mix(text, bg, 0.42),
      "--accent-color": ensureContrast(a1, bg, 3),
      "--accent-color-alt": ensureContrast(a2, bg, 3),
      "--border-color": mix(text, bg, 0.82),
      "--button-color": ensureContrast(a1, bg, 3),
    };
  }
  const bg = withLum(darkest, 0.025);
  const card = withLum(darkest, 0.06);
  const text = withLum(lightest, 0.92);
  return {
    "--bg-color": bg,
    "--card-bg-color": card,
    "--text-color": text,
    "--secondary-text-color": mix(text, bg, 0.4),
    "--accent-color": ensureContrast(a1, bg, 4),
    "--accent-color-alt": ensureContrast(a2, bg, 4),
    "--border-color": mix(text, bg, 0.8),
    "--button-color": ensureContrast(a1, bg, 4),
  };
}

/**
 * Build a full theme ({ name, light, dark }) from a palette. Both variants
 * are synthesised from the same colours: the light map lightens the
 * palette's neutrals and the dark map darkens them, so the palette's
 * character shows through in whichever mode matches its natural lightness.
 * `id` is left unset — the backend assigns it from the name on save.
 */
export function themeFromPalette(name, hexes) {
  return {
    name: (name || "").trim(),
    light: buildModeMap(hexes, "light"),
    dark: buildModeMap(hexes, "dark"),
  };
}
