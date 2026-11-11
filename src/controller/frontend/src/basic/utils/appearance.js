// Per-browser appearance: which theme, light/dark and fonts *this* browser
// shows. The themes and fonts themselves, and the rig's default choice, stay
// in the controller config (config.frontend), so a wiped browser loses only
// its choice and falls back to the rig default.
//
// Only the keys an operator has actually changed here are stored; the rest
// keep following the rig default, so e.g. picking a font here doesn't freeze
// the theme. A stored theme or font that has since been deleted on the
// controller is ignored (rig default again) rather than falling to System.

import { BUILTIN_FONTS } from "./fonts";
import { listThemes } from "./themes";

const STORAGE_KEY = "saviour.appearance";
const EVENT = "saviour-appearance";

export const APPEARANCE_KEYS = ["theme_id", "dark_mode", "font_body", "font_heading"];

function clean(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  for (const k of APPEARANCE_KEYS) {
    const v = raw[k];
    if (k === "dark_mode" ? typeof v === "boolean" : typeof v === "string") out[k] = v;
  }
  return out;
}

/** This browser's stored choices ({} when none, or storage is unavailable). */
export function loadAppearance() {
  try {
    return clean(JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}"));
  } catch {
    return {};
  }
}

/** Replace this browser's choices; an empty object clears them. */
export function saveAppearance(next) {
  const value = clean(next);
  try {
    if (Object.keys(value).length) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Private window or blocked storage: the choice lasts until reload.
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: value }));
}

/** Call `fn(choices)` whenever this browser's choices change, in this tab
 *  or another one. Returns an unsubscribe function. */
export function onAppearanceChange(fn) {
  const local = (e) => fn(e.detail);
  const other = (e) => { if (e.key === STORAGE_KEY) fn(loadAppearance()); };
  window.addEventListener(EVENT, local);
  window.addEventListener("storage", other);
  return () => {
    window.removeEventListener(EVENT, local);
    window.removeEventListener("storage", other);
  };
}

/** The choices that still point at something the rig has. */
export function validAppearance(fe, choices) {
  const out = { ...choices };
  if (out.theme_id && !listThemes(fe?.custom_themes).some((t) => t.id === out.theme_id)) {
    delete out.theme_id;
  }
  const fontIds = new Set([
    ...BUILTIN_FONTS.map((f) => f.id), ...(fe?.custom_fonts || []).map((f) => f.id),
  ]);
  if (out.font_body && !fontIds.has(out.font_body)) delete out.font_body;
  // "" is a real choice for headings: same as body text.
  if (out.font_heading && !fontIds.has(out.font_heading)) delete out.font_heading;
  return out;
}

/** config.frontend as this browser shows it: the rig default with this
 *  browser's (still valid) choices on top. */
export function effectiveFrontend(fe, choices) {
  if (!fe) return fe;
  return { ...fe, ...validAppearance(fe, choices) };
}
