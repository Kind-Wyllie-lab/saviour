import { useEffect } from "react";
import socket from "../socket";
import { frontendTheme, THEME_TOKENS } from "/src/basic/utils/themes";
import { fontFaceCss, frontendFonts } from "/src/basic/utils/fonts";
import { effectiveFrontend, loadAppearance, onAppearanceChange } from "/src/basic/utils/appearance";
import { setConfiguredDarkMode } from "./useLiveThemePreview";

const STYLE_TAG_ID = "controller-theme-override";
const FONT_TAG_ID = "controller-font-override";

function styleTag(id) {
  let tag = document.getElementById(id);
  if (!tag) {
    tag = document.createElement("style");
    tag.id = id;
    document.head.appendChild(tag);
  }
  return tag;
}

/**
 * Applies the colour theme, dark/light mode and fonts.
 * Mounted once from main.jsx (via ThemedApp) so it runs regardless of which
 * frontend variant is active. The controller config holds the rig default;
 * this browser's own choices (basic/utils/appearance.js, localStorage) go on
 * top, key by key, and are re-applied the moment they change.
 *
 * config.frontend carries `theme_id` (see basic/utils/themes.js for the
 * catalogue) and `dark_mode`, both edited on the Frontend tab in Controller
 * Settings. `dark_mode` picks which half (theme.light / theme.dark) of the
 * selected theme is applied. Both default the same way base_config.json does:
 * theme_id -> "default", dark_mode -> true when the field is absent (e.g. an
 * active_config.json saved before these fields existed).
 *
 * Back-compat: a pre-theme config carries a bare `accent_color` and (once
 * base_config's defaults merge in on the next controller start) theme_id
 * "default". While the Default theme is selected, a set `accent_color` still
 * overrides its accent, so a lab that customised only the accent keeps that
 * exact colour. Picking any named theme drops the override.
 *
 * Injects a <style> tag rather than setting the properties on
 * document.documentElement: index.css declares these tokens on *both* :root
 * and body.dark-mode, and a custom property's computed value comes from the
 * nearest ancestor with an explicit declaration. body.dark-mode's own
 * declaration always wins over anything set on <html> for every element under
 * <body> whenever dark mode is on, making a documentElement-only override
 * silently invisible in dark mode. A <style> rule targeting both selectors
 * overrides both index.css blocks directly instead.
 *
 * Unsaved themes (the Frontend settings tab, ThemeImportModal) are previewed
 * over this by useLiveThemePreview.
 *
 * Fonts ride along in a second tag: @font-face rules for every added font
 * (config.frontend.custom_fonts, served from /fonts/) and the --font-body /
 * --font-heading variables index.css reads (basic/utils/fonts.js).
 *
 * Uses a named handler so cleanup removes exactly this listener via
 * socket.off(event, handler) -- ControllerConfigCard.jsx listens on the same
 * "controller_config_response" event, and the bare socket.off(event) form
 * removes *every* listener for it.
 */
export default function useControllerTheme() {
  useEffect(() => {
    socket.emit("get_controller_config");

    let rigFe;
    let choices = loadAppearance();

    const paint = () => {
      if (!rigFe) return;
      const fe = effectiveFrontend(rigFe, choices);
      const { tokens, darkMode } = frontendTheme(fe);
      // Sets body.dark-mode, unless an unsaved theme preview is showing
      // (useLiveThemePreview), which keeps its own mode until it closes.
      setConfiguredDarkMode(darkMode);

      const decls = THEME_TOKENS
        .filter((key) => tokens[key])
        .map((key) => `${key}: ${tokens[key]};`)
        .join(" ");

      styleTag(STYLE_TAG_ID).textContent = `:root, body.dark-mode { ${decls} }`;

      const fonts = frontendFonts(fe);
      styleTag(FONT_TAG_ID).textContent = `${fontFaceCss(fe?.custom_fonts)}
`
        + `:root { --font-body: ${fonts.body}; --font-heading: ${fonts.heading}; }`;
    };

    const handleConfig = (data) => {
      rigFe = data?.config?.frontend || {};
      paint();
    };
    const stopWatching = onAppearanceChange((next) => {
      choices = next;
      paint();
    });

    socket.on("controller_config_response", handleConfig);
    return () => {
      socket.off("controller_config_response", handleConfig);
      stopWatching();
    };
  }, []);
}
