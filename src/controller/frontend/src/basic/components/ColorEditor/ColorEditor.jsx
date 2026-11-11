import { useEffect, useState } from "react";
import "./ColorEditor.css";
import { clamp, hexToRgb, hslToRgb, rgbToHex, rgbToHsl } from "/src/basic/utils/color";

// A colour editor with a visible HSL / RGB / Hex switch. The browser's own
// <input type="color"> hands off to the OS picker, whose modes vary by
// platform (Windows shows its own dialog), so this one is drawn here. The
// value in and out is always "#rrggbb"; the mode is a per-browser convenience.

const MODE_KEY = "saviour.colorEditorMode";
const MODES = [
  { key: "hsl", label: "HSL" },
  { key: "rgb", label: "RGB" },
  { key: "hex", label: "Hex" },
];

function loadMode() {
  try {
    const m = window.localStorage.getItem(MODE_KEY);
    return MODES.some((x) => x.key === m) ? m : "hsl";
  } catch {
    return "hsl";
  }
}

function storeMode(m) {
  try { window.localStorage.setItem(MODE_KEY, m); } catch { /* storage blocked */ }
}

// One channel: a slider whose track shows the colours it will produce, and
// a number box for exact values.
function Channel({ label, value, max, unit, track, onChange }) {
  return (
    <label className="color-editor__channel">
      <span className="color-editor__channel-label">{label}</span>
      <input
        type="range"
        min={0}
        max={max}
        step={1}
        value={Math.round(value)}
        style={{ background: track }}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="color-editor__num">
        <input
          type="number"
          min={0}
          max={max}
          step={1}
          value={Math.round(value)}
          aria-label={label}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) onChange(clamp(v, 0, max));
          }}
        />
        {unit && <span>{unit}</span>}
      </span>
    </label>
  );
}

/**
 * Edit `value` ("#rrggbb"); `onChange` gets a lower-case "#rrggbb".
 * HSL is the default mode; the last mode used is remembered per browser.
 */
export default function ColorEditor({ value, onChange }) {
  const [mode, setMode] = useState(loadMode);
  // HSL is held separately so hue and saturation survive a trip through
  // grey/black/white (where RGB can't say what they were).
  const [hsl, setHsl] = useState(() => rgbToHsl(hexToRgb(value) || [0, 0, 0]));
  const [hexText, setHexText] = useState(value);

  useEffect(() => {
    setHexText(value);
    const rgb = hexToRgb(value);
    if (rgb && rgbToHex(hslToRgb(hsl)) !== value.toLowerCase()) setHsl(rgbToHsl(rgb));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const rgb = hexToRgb(value) || [0, 0, 0];
  const emit = (hex) => onChange(hex.toLowerCase());
  const setFromHsl = (next) => { setHsl(next); emit(rgbToHex(hslToRgb(next))); };
  const pickMode = (m) => { setMode(m); storeMode(m); };

  const [h, s, l] = hsl;
  const hueTrack = `linear-gradient(to right, ${[0, 60, 120, 180, 240, 300, 360]
    .map((d) => `hsl(${d} ${s}% ${l}%)`).join(", ")})`;
  const satTrack = `linear-gradient(to right, hsl(${h} 0% ${l}%), hsl(${h} 100% ${l}%))`;
  const lightTrack = `linear-gradient(to right, hsl(${h} ${s}% 0%), hsl(${h} ${s}% 50%), hsl(${h} ${s}% 100%))`;
  const rgbTrack = (i) => {
    const lo = [...rgb]; lo[i] = 0;
    const hi = [...rgb]; hi[i] = 255;
    return `linear-gradient(to right, ${rgbToHex(lo)}, ${rgbToHex(hi)})`;
  };
  const setRgb = (i, v) => { const next = [...rgb]; next[i] = v; emit(rgbToHex(next)); };

  return (
    <div className="color-editor">
      <div className="color-editor__head">
        <span className="color-editor__preview" style={{ background: value }} />
        <div className="color-editor__modes" role="radiogroup" aria-label="Colour format">
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              role="radio"
              aria-checked={mode === m.key}
              className={`color-editor__mode${mode === m.key ? " selected" : ""}`}
              onClick={() => pickMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {mode === "hsl" && (
        <>
          <Channel label="Hue" value={h} max={360} unit="°" track={hueTrack}
            onChange={(v) => setFromHsl([v, s, l])} />
          <Channel label="Saturation" value={s} max={100} unit="%" track={satTrack}
            onChange={(v) => setFromHsl([h, v, l])} />
          <Channel label="Lightness" value={l} max={100} unit="%" track={lightTrack}
            onChange={(v) => setFromHsl([h, s, v])} />
        </>
      )}
      {mode === "rgb" && ["Red", "Green", "Blue"].map((name, i) => (
        <Channel key={name} label={name} value={rgb[i]} max={255} track={rgbTrack(i)}
          onChange={(v) => setRgb(i, v)} />
      ))}
      {mode === "hex" && (
        <label className="color-editor__hex">
          <span className="color-editor__channel-label">Hex</span>
          <input
            type="text"
            spellCheck={false}
            maxLength={7}
            value={hexText}
            onChange={(e) => {
              const t = e.target.value.trim();
              setHexText(t);
              const ok = hexToRgb(t);
              if (ok) emit(rgbToHex(ok));
            }}
            onBlur={() => setHexText(value)}
          />
        </label>
      )}
    </div>
  );
}
