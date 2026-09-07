import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import {
  parsePalette, themeFromPalette, defaultRoleAssignment, PALETTE_ROLES,
} from "/src/basic/utils/themes";
import "./ThemeImportModal.css";

function Preview({ label, map }) {
  return (
    <div
      className="theme-preview"
      style={{ background: map["--bg-color"], borderColor: map["--border-color"] }}
    >
      <div className="theme-preview__label" style={{ color: map["--secondary-text-color"] }}>
        {label}
      </div>
      <div
        className="theme-preview__card"
        style={{ background: map["--card-bg-color"], borderColor: map["--border-color"] }}
      >
        <div className="theme-preview__title" style={{ color: map["--text-color"] }}>
          Aa Heading
        </div>
        <div className="theme-preview__body" style={{ color: map["--secondary-text-color"] }}>
          Secondary text sample
        </div>
        <div className="theme-preview__row">
          <span className="theme-preview__btn" style={{ background: map["--accent-color"] }}>
            Button
          </span>
          <span
            className="theme-preview__chip"
            style={{ borderColor: map["--accent-color-alt"], color: map["--accent-color-alt"] }}
          >
            Chip
          </span>
        </div>
      </div>
    </div>
  );
}

// One draggable chip per role, in fixed PALETTE_ROLES order. Dragging a chip
// onto another swaps their colours -- there are always exactly 5 roles, so
// "reorder" here means "which of the 5 roles this hex anchors", not an
// insert/shift.
function RoleSwatches({ roles, onSwap }) {
  const [dragIndex, setDragIndex] = useState(null);
  return (
    <div className="theme-import-modal__roles">
      {PALETTE_ROLES.map(({ key, label }, i) => (
        <div
          key={key}
          className={`theme-role-swatch${dragIndex === i ? " theme-role-swatch--dragging" : ""}`}
          draggable
          title="Drag to swap with another role"
          onDragStart={() => setDragIndex(i)}
          onDragEnd={() => setDragIndex(null)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (dragIndex != null && dragIndex !== i) onSwap(dragIndex, i);
            setDragIndex(null);
          }}
        >
          <span className="theme-role-swatch__color" style={{ background: roles[key] }} />
          <span className="theme-role-swatch__label">{label}</span>
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
  const [input, setInput] = useState(initial?.input ?? "");
  const [status, setStatus] = useState(null); // null | "saving" | "error"
  const [error, setError] = useState("");
  // null == automatic (the heuristic pick, one per mode); once the operator
  // drags a swatch this becomes an explicit { bg, card, text, accent,
  // accentAlt } hex assignment shared by both light and dark previews.
  const [manualRoles, setManualRoles] = useState(initial?.manualRoles ?? null);
  const [rawColours, setRawColours] = useState(initial?.rawColours ?? false);

  const hexes = useMemo(() => parsePalette(input), [input]);
  // The drag editor's starting arrangement -- matches what automatic mode
  // would pick, so it looks pre-sorted until the operator changes anything.
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
    () =>
      !hexes
        ? null
        : manualRoles
        ? themeFromPalette(name || "Preview", hexes, manualRoles, rawColours)
        : themeFromPalette(name || "Preview", hexes),
    [hexes, name, manualRoles, rawColours],
  );

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
      source: { palette: hexes, roles: manualRoles || null, raw: manualRoles ? rawColours : false },
    });
  };

  return (
    <div className="modal-overlay" onClick={() => onClose(false)}>
      <div className="modal theme-import-modal" onClick={(e) => e.stopPropagation()}>
        <h2>{editingTheme ? "Edit theme" : "Import theme from Coolors"}</h2>
        <p className="modal-subtext">
          {editingTheme ? (
            "Paste a different palette to replace this theme's colours, or leave it and just drag the swatches below."
          ) : (
            <>
              Paste a{" "}
              <a href="https://coolors.co" target="_blank" rel="noreferrer">coolors.co</a>{" "}
              palette URL (or a list of hex colours).
            </>
          )}{" "}
          Both light and dark variants are derived from the palette
          automatically — drag a swatch below to assign a colour to a role
          yourself instead, or fine-tune later by editing the theme file on
          the controller.
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

        <label className="theme-import-modal__label" htmlFor="theme-url">Coolors URL or hex list</label>
        <input
          id="theme-url"
          className="theme-import-modal__input"
          type="text"
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

        {theme && (
          <>
            <div className="theme-import-modal__roles-head">
              <label className="theme-import-modal__label theme-import-modal__label--inline">
                Drag to assign colours
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
            <RoleSwatches roles={displayRoles} onSwap={swapRoles} />
            <label className="theme-import-modal__raw-toggle">
              <input
                type="checkbox"
                checked={rawColours}
                disabled={!manualRoles}
                onChange={(e) => setRawColours(e.target.checked)}
              />
              Use exact colours (skip automatic contrast/lightness adjustment)
            </label>

            <div className="theme-import-modal__previews">
              <Preview label="Light" map={theme.light} />
              <Preview label="Dark" map={theme.dark} />
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
