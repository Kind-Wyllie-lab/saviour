import { useEffect, useMemo, useState } from "react";
import socket from "/src/socket";
import { parsePalette, themeFromPalette } from "/src/basic/utils/themes";
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

export default function ThemeImportModal({ onClose }) {
  const [name, setName] = useState("");
  const [input, setInput] = useState("");
  const [status, setStatus] = useState(null); // null | "saving" | "error"
  const [error, setError] = useState("");

  const hexes = useMemo(() => parsePalette(input), [input]);
  const theme = useMemo(
    () => (hexes ? themeFromPalette(name || "Preview", hexes) : null),
    [hexes, name],
  );

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
      name: name.trim(),
      light: theme.light,
      dark: theme.dark,
    });
  };

  return (
    <div className="modal-overlay" onClick={() => onClose(false)}>
      <div className="modal theme-import-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Import theme from Coolors</h2>
        <p className="modal-subtext">
          Paste a{" "}
          <a href="https://coolors.co" target="_blank" rel="noreferrer">coolors.co</a>{" "}
          palette URL (or a list of hex colours). Both light and dark variants
          are derived from the palette — fine-tune later by editing the theme
          file on the controller.
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
            <div className="theme-import-modal__swatches">
              {hexes.map((h) => (
                <span key={h} className="theme-import-modal__src" style={{ background: h }} title={h} />
              ))}
            </div>
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
            {status === "saving" ? "Saving…" : "Save theme"}
          </button>
        </div>
      </div>
    </div>
  );
}
