import { useEffect } from "react";
import socket from "../socket";

const STYLE_TAG_ID = "controller-theme-override";

/**
 * Applies controller-configured theme values (currently just accent color)
 * as CSS custom-property overrides. Mounted once from main.jsx, so it runs
 * regardless of which frontend variant is active.
 *
 * Injects a <style> tag rather than setting the property directly on
 * document.documentElement — index.css declares --accent-color on *both*
 * :root and body.dark-mode, and a custom property's computed value for any
 * element comes from the nearest ancestor (including itself) with an
 * explicit declaration, not from further up the tree regardless of inline
 * styles there. body.dark-mode's own declaration always wins over anything
 * set on <html> for every element under <body> — i.e. everything visible —
 * whenever dark mode is on, making a documentElement-only override silently
 * invisible in dark mode specifically. A <style> tag targeting both
 * selectors overrides both rules directly instead of trying to out-cascade
 * them from the wrong element.
 *
 * Uses a named handler so cleanup can remove exactly this listener via
 * socket.off(event, handler) — ControllerConfigCard.jsx (which listens on
 * the same "controller_config_response" event for its own Settings-tab
 * state) previously called the bare socket.off("controller_config_response")
 * form, which removes *every* listener for that event, not just its own.
 * That would silently kill this hook's listener the first time a user
 * navigated away from Settings. Fixed there too, but this hook doesn't rely
 * on it staying fixed.
 */
export default function useControllerTheme() {
  useEffect(() => {
    socket.emit("get_controller_config");

    const handleConfig = (data) => {
      const accentColor = data?.config?.frontend?.accent_color;
      if (!accentColor) return;

      let styleTag = document.getElementById(STYLE_TAG_ID);
      if (!styleTag) {
        styleTag = document.createElement("style");
        styleTag.id = STYLE_TAG_ID;
        document.head.appendChild(styleTag);
      }
      styleTag.textContent = `:root, body.dark-mode { --accent-color: ${accentColor}; }`;
    };

    socket.on("controller_config_response", handleConfig);
    return () => socket.off("controller_config_response", handleConfig);
  }, []);
}
