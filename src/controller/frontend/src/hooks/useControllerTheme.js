import { useEffect } from "react";
import socket from "../socket";

/**
 * Applies controller-configured theme values (currently just accent color)
 * as CSS custom-property overrides on the document root. Mounted once from
 * main.jsx, so it runs regardless of which frontend variant is active.
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
      if (accentColor) {
        document.documentElement.style.setProperty("--accent-color", accentColor);
      }
    };

    socket.on("controller_config_response", handleConfig);
    return () => socket.off("controller_config_response", handleConfig);
  }, []);
}
