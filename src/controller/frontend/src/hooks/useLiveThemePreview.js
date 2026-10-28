import { useEffect, useRef } from "react";
import { THEME_TOKENS } from "/src/basic/utils/themes";

const PREVIEW_STYLE_ID = "theme-live-preview";

// Unsaved themes painted over the whole page: the Frontend settings tab
// (the theme tile / dark mode toggle being edited) and, above it,
// ThemeImportModal (the theme being built). Kept as a stack so the newest
// active preview wins and closing it falls back to the one below, then to
// the controller's configured theme.
//
// One <style> tag holds the winning preview. Its selectors carry an extra
// `html` so they outrank useControllerTheme's `:root, body.dark-mode` tag
// (see that hook's docstring for why a <style> tag at all). body.dark-mode
// follows the winning preview, else `baseDark` -- the configured dark_mode,
// which useControllerTheme reports through setConfiguredDarkMode.
const layers = [];
let baseDark = null;

function apply() {
  const top = [...layers].reverse().find((l) => l.map);
  const dark = top ? top.dark : baseDark;
  if (dark != null) document.body.classList.toggle("dark-mode", dark);

  let tag = document.getElementById(PREVIEW_STYLE_ID);
  if (!top) {
    tag?.remove();
    return;
  }
  if (!tag) {
    tag = document.createElement("style");
    tag.id = PREVIEW_STYLE_ID;
    document.head.appendChild(tag);
  }
  const decls = THEME_TOKENS
    .filter((key) => top.map[key])
    .map((key) => `${key}: ${top.map[key]};`)
    .join(" ");
  tag.textContent = `html:root, html body.dark-mode { ${decls} }`;
}

/** Called by useControllerTheme whenever the configured dark_mode arrives. */
export function setConfiguredDarkMode(dark) {
  baseDark = dark;
  apply();
}

/**
 * Preview `map` (a THEME_TOKENS map) in `dark` mode on the whole page while
 * the calling component is mounted. A null `map` means "nothing to
 * preview" and lets the layer below show through.
 */
export default function useLiveThemePreview(map, dark) {
  const layerRef = useRef(null);

  useEffect(() => {
    const layer = { map: null, dark: false };
    layerRef.current = layer;
    layers.push(layer);
    return () => {
      layers.splice(layers.indexOf(layer), 1);
      apply();
    };
  }, []);

  useEffect(() => {
    Object.assign(layerRef.current, { map, dark });
    apply();
  }, [map, dark]);
}
