import { useEffect } from "react";
import { frontendFonts } from "/src/basic/utils/fonts";

const PREVIEW_STYLE_ID = "font-live-preview";

/**
 * Preview the body/heading fonts in `fe` (a config.frontend object being
 * edited) on the whole page while the caller is mounted; null shows the
 * configured fonts. The extra `html` in the selector outranks
 * useControllerTheme's `:root` rule. The @font-face rules for added fonts
 * are already declared by useControllerTheme, so only the variables change.
 */
export default function useLiveFontPreview(fe) {
  const { body, heading } = frontendFonts(fe);
  const active = fe != null;

  useEffect(() => {
    if (!active) return undefined;
    let tag = document.getElementById(PREVIEW_STYLE_ID);
    if (!tag) {
      tag = document.createElement("style");
      tag.id = PREVIEW_STYLE_ID;
      document.head.appendChild(tag);
    }
    tag.textContent = `html:root { --font-body: ${body}; --font-heading: ${heading}; }`;
    return () => tag.remove();
  }, [active, body, heading]);
}
