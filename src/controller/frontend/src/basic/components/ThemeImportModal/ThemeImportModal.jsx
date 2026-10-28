import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import {
  parsePalette, themeFromPalette, defaultRoleAssignment, PALETTE_ROLES,
} from "/src/basic/utils/themes";
import useLiveThemePreview from "/src/hooks/useLiveThemePreview";
import "./ThemeImportModal.css";

// Starting colours for a new theme (the same Coolors palette the import
// field uses as its placeholder).
const DEFAULT_PALETTE = ["#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51"];

// The palette editor: one tile per role, in fixed PALETTE_ROLES order. Its
// colour is a native colour input (click to change it); dragging a tile onto
// another swaps their colours -- there are always exactly 5 roles, so
// "reorder" here means "which of the 5 roles this hex anchors", not an
// insert/shift.
function RoleSwatches({ roles, onSwap, onPick }) {
  const [dragIndex, setDragIndex] = useState(null);
  return (
    <div className="theme-import-modal__roles">
      {PALETTE_ROLES.map(({ key, label }, i) => (
        <div
          key={key}
          className={`theme-role-swatch${dragIndex === i ? " theme-role-swatch--dragging" : ""}`}
          draggable
          title="Click the colour to change it; drag to swap with another role"
          onDragStart={() => setDragIndex(i)}
          onDragEnd={() => setDragIndex(null)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (dragIndex != null && dragIndex !== i) onSwap(dragIndex, i);
            setDragIndex(null);
          }}
        >
          <input
            type="color"
            className="theme-role-swatch__color"
            aria-label={`${label} colour`}
            value={roles[key]}
            onChange={(e) => onPick(key, e.target.value.toLowerCase())}
          />
          <span className="theme-role-swatch__label">{label}</span>
          <span className="theme-role-swatch__hex">{roles[key]}</span>
        </div>
      ))}
    </div>
  );
}

// Reconstructs the editor's starting state from a saved custom theme.
// `editingTheme.source` (see themes.py's module docstring) carries the
// original palette and role assignment when the theme was saved through
// this modal; older/hand-written theme files won't have it, so fall back to
// treating the theme's own current light-mode colours as the "palette" --
// editing still starts from something, it just can't recover the true
// source. Returns null for "new theme" (editingTheme absent).
function initialStateFor(editingTheme) {
  if (!editingTheme) return null;
  const src = editingTheme.source;
  if (src?.palette?.length) {
    return {
      name: editingTheme.name,
      input: src.palette.join(" "),
      manualRoles: src.roles || null,
      rawColours: !!src.raw,
    };
  }
  const light = editingTheme.light || {};
  const fallbackRoles = {
    bg: light["--bg-color"], card: light["--card-bg-color"],
    text: light["--text-color"], accent: light["--accent-color"],
    accentAlt: light["--accent-color-alt"],
  };
  return {
    name: editingTheme.name,
    input: Object.values(fallbackRoles).join(" "),
    manualRoles: fallbackRoles,
    // These are already-final colours, not raw palette hexes -- don't push
    // them through the lightness/contrast fix-up a second time.
    rawColours: true,
  };
}

export default function ThemeImportModal({ onClose, editingTheme }) {
  const initial = useMemo(() => initialStateFor(editingTheme), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [name, setName] = useState(initial?.name ?? "");
  // A new theme starts on DEFAULT_PALETTE, so the editor and preview show
  // straight away.
  const [input, setInput] = useState(initial?.input ?? DEFAULT_PALETTE.join(" "));
  const [status, setStatus] = useState(null); // null | "saving" | "error"
  const [error, setError] = useState("");
  // null == automatic (defaultRoleAssignment's pick); once the operator
  // drags a swatch this becomes an explicit { bg, card, text, accent,
  // accentAlt } hex assignment shared by both light and dark.
  const [manualRoles, setManualRoles] = useState(initial?.manualRoles ?? null);
  // The palette is used as picked by default, so light and dark come out the
  // same. Ticking "Normalise colours" (rawColours false) gives each mode its
  // lightness/contrast fix-up.
  const [rawColours, setRawColours] = useState(initial?.rawColours ?? true);
  // The Coolors URL / hex-list field is tucked away by default; the role
  // tiles are the main editor.
  const [showImport, setShowImport] = useState(false);

  const hexes = useMemo(() => parsePalette(input), [input]);
  // The drag editor's starting arrangement, and the roles the theme is built
  // from until the operator changes anything.
  const seedRoles = useMemo(() => (hexes ? defaultRoleAssignment(hexes) : null), [hexes]);
  const displayRoles = manualRoles || seedRoles;

  // A freshly pasted/edited palette drops any previous manual order rather
  // than silently carrying it over onto different colours -- but not on the
  // very first render, or reopening an edit with a manual role assignment
  // would immediately lose it.
  const hexesKey = hexes ? hexes.join(",") : "";
  const prevHexesKeyRef = useRef(hexesKey);
  useEffect(() => {
    if (prevHexesKeyRef.current !== hexesKey) {
      setManualRoles(null);
      prevHexesKeyRef.current = hexesKey;
    }
  }, [hexesKey]);

  const theme = useMemo(
    () => (displayRoles ? themeFromPalette(name || "Preview", hexes, displayRoles, rawColours) : null),
    [hexes, name, displayRoles, rawColours],
  );

  // Picking a tile's colour makes the 5 roles the palette. The
  // palette-changed reset above is skipped, since the roles are the source.
  const pickColour = (key, hex) => {
    const roles = { ...displayRoles, [key]: hex };
    const palette = PALETTE_ROLES.map((r) => roles[r.key]);
    setManualRoles(roles);
    prevHexesKeyRef.current = palette.join(",");
    setInput(palette.join(" "));
  };

  // Which half of the theme the live page preview shows; starts on the
  // page's current mode.
  const [previewDark, setPreviewDark] = useState(
    () => document.body.classList.contains("dark-mode"),
  );
  useLiveThemePreview(theme ? (previewDark ? theme.dark : theme.light) : null, previewDark);

  const swapRoles = (i, j) => {
    const keys = PALETTE_ROLES.map((r) => r.key);
    const base = { ...(manualRoles || seedRoles) };
    const a = keys[i], b = keys[j];
    [base[a], base[b]] = [base[b], base[a]];
    setManualRoles(base);
  };

  useEffect(() => {
    const ok = () => onClose(true);
    const fail = (data) => {
      setStatus("error");
      setError(data?.error || "Could not save theme");
    };
    socket.on("custom_theme_saved", ok);
    socket.on("custom_theme_error", fail);
    return () => {
      socket.off("custom_theme_saved", ok);
      socket.off("custom_theme_error", fail);
    };
  }, [onClose]);

  const canSave = name.trim() && theme && status !== "saving";

  const save = () => {
    if (!canSave) return;
    setStatus("saving");
    setError("");
    socket.emit("save_custom_theme", {
      ...(editingTheme ? { id: editingTheme.id } : {}),
      name: name.trim(),
      light: theme.light,
      dark: theme.dark,
      // Recorded so a later edit can reconstruct this exact drag-and-drop
      // state instead of reverse-engineering it from the final colours.
      source: { palette: hexes, roles: manualRoles || null, raw: rawColours },
    });
  };

  return (
    <div className="modal-overlay theme-import-overlay" onClick={() => onClose(false)}>
      <div className="modal theme-import-modal" onClick={(e) => e.stopPropagation()}>
        <h2>{editingTheme ? "Edit theme" : "New theme"}</h2>
        <p className="modal-subtext">
          Click a colour below to change it, and drag one onto another to
          swap which role it plays. Or start from a{" "}
          <a href="https://coolors.co" target="_blank" rel="noreferrer">coolors.co</a>{" "}
          palette. Your colours are used as they are; tick "Normalise
          colours" to derive separate light and dark variants automatically.
          Fine-tune later by editing the theme file on the controller.
        </p>

        <label className="theme-import-modal__label" htmlFor="theme-name">Name</label>
        <input
          id="theme-name"
          className="theme-import-modal__input"
          type="text"
          maxLength={60}
          placeholder="Ocean"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        {showImport ? (
          <>
            <label className="theme-import-modal__label" htmlFor="theme-url">
              Import from Coolors: URL or hex list
            </label>
            <input
              id="theme-url"
              className="theme-import-modal__input"
              type="text"
              autoFocus
              placeholder="https://coolors.co/palette/264653-2a9d8f-e9c46a-f4a261-e76f51"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
            {input.trim() && !hexes && (
              <p className="theme-import-modal__hint">
                Need at least 3 hex colours — paste a Coolors palette link or
                something like <code>264653 2a9d8f e9c46a</code>.
              </p>
            )}
          </>
        ) : (
          <button
            type="button"
            className="theme-import-modal__reset-roles theme-import-modal__import-toggle"
            onClick={() => setShowImport(true)}
          >
            Import from Coolors…
          </button>
        )}

        {theme && (
          <>
            <div className="theme-import-modal__roles-head">
              <label className="theme-import-modal__label theme-import-modal__label--inline">
                Colours: click to change, drag to swap
              </label>
              {manualRoles && (
                <button
                  type="button"
                  className="theme-import-modal__reset-roles"
                  onClick={() => setManualRoles(null)}
                >
                  Reset to automatic
                </button>
              )}
            </div>
            <RoleSwatches roles={displayRoles} onSwap={swapRoles} onPick={pickColour} />
            <label className="theme-import-modal__raw-toggle">
              <input
                type="checkbox"
                checked={!rawColours}
                onChange={(e) => setRawColours(!e.target.checked)}
              />
              Normalise colours (adjust lightness and contrast for readability, with
              separate light and dark variants)
            </label>

            <div className="theme-import-modal__preview-mode">
              <span>Previewing on this page in</span>
              {[false, true].map((dark) => (
                <button
                  key={String(dark)}
                  type="button"
                  className={`theme-import-modal__mode-btn${previewDark === dark ? " is-active" : ""}`}
                  onClick={() => setPreviewDark(dark)}
                >
                  {dark ? "Dark" : "Light"}
                </button>
              ))}
              {rawColours && <span className="theme-import-modal__mode-note">(same in both unless normalised)</span>}
            </div>
          </>
        )}

        {status === "error" && <p className="theme-import-modal__msg val--danger">{error}</p>}

        <div className="modal-buttons">
          <button className="reset-button" onClick={() => onClose(false)}>Cancel</button>
          <button className="save-button" onClick={save} disabled={!canSave}>
            {status === "saving" ? "Saving…" : editingTheme ? "Save changes" : "Save theme"}
          </button>
        </div>
      </div>
    </div>
  );
}
