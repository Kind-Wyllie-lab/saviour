import { useEffect } from "react";
import socket from "../socket";
import {
  resolveTheme,
  themeTokens,
  THEME_TOKENS,
  DEFAULT_THEME_ID,
} from "/src/basic/utils/themes";

const STYLE_TAG_ID = "controller-theme-override";

/**
 * Applies the controller-configured colour theme and dark/light mode.
 * Mounted once from main.jsx (via ThemedApp) so it runs regardless of which
 * frontend variant is active, and every browser viewing this controller
 * shows the same theme rather than a per-browser preference.
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
 * Uses a named handler so cleanup removes exactly this listener via
 * socket.off(event, handler) -- ControllerConfigCard.jsx listens on the same
 * "controller_config_response" event, and the bare socket.off(event) form
 * removes *every* listener for it.
 */
export default function useControllerTheme() {
  useEffect(() => {
    socket.emit("get_controller_config");

    const handleConfig = (data) => {
      const fe = data?.config?.frontend || {};
      const darkMode = fe.dark_mode ?? true;
      document.body.classList.toggle("dark-mode", darkMode);

      const themeId = fe.theme_id ?? DEFAULT_THEME_ID;
      const theme = resolveTheme(themeId, fe.custom_themes);
      const tokens = themeTokens(theme, darkMode);

      if (themeId === DEFAULT_THEME_ID && fe.accent_color) {
        tokens["--accent-color"] = fe.accent_color;
      }

      const decls = THEME_TOKENS
        .filter((key) => tokens[key])
        .map((key) => `${key}: ${tokens[key]};`)
        .join(" ");

      let styleTag = document.getElementById(STYLE_TAG_ID);
      if (!styleTag) {
        styleTag = document.createElement("style");
        styleTag.id = STYLE_TAG_ID;
        document.head.appendChild(styleTag);
      }
      styleTag.textContent = `:root, body.dark-mode { ${decls} }`;
    };

    socket.on("controller_config_response", handleConfig);
    return () => socket.off("controller_config_response", handleConfig);
  }, []);
}
