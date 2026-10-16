import React from "react";
import {
  LIMITS, SPEC_COLORS, autoWindowSeconds, cameraAspect, clampRect,
} from "./composeLayout";

// Committed swatch PNGs, one per colour map (tools/gen_spectrogram_swatches.py).
const SWATCHES = Object.fromEntries(
  Object.entries(
    import.meta.glob("/src/assets/spectrogram-swatches/*.png", {
      eager: true,
      query: "?url",
      import: "default",
    }),
  ).map(([path, url]) => [path.split("/").pop().replace(".png", ""), url]),
);

function Num({ label, value, onChange, min, max, step = 1, title, suffix }) {
  return (
    <label className="tile-inspector__field" title={title}>
      <span>{label}</span>
      <span className="tile-inspector__input">
        <input
          type="number" value={value ?? ""} min={min} max={max} step={step}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
        {suffix && <em>{suffix}</em>}
      </span>
    </label>
  );
}

/**
 * Options for the selected tile: which source it shows, where it sits
 * (exact pixels), its stacking order, and the per-kind options (camera
 * fit / crop / rotation; spectrogram look, window and playhead).
 */
export default function TileInspector({
  tile, doc, cameras, micDevices, durationS, info, onChange, onDelete, onDuplicate,
  onZ, isTrack, onToggleTrack,
}) {
  if (!tile) {
    return (
      <div className="tile-inspector tile-inspector--empty">
        Select a tile on the canvas to edit it, or add a source from the list.
      </div>
    );
  }
  const o = tile.options;
  const setOpt = (k, v) => onChange({ options: { ...o, [k]: v } });
  const setRect = (i, v) => {
    if (v == null) return;
    const r = [...tile.rect];
    r[i] = v;
    onChange({ rect: clampRect(r, doc.canvas) });
  };
  const cam = cameras.find((c) => c.name === tile.source);

  const fitToSource = () => {
    const a = cameraAspect(cam, o);
    const [x, y, w] = tile.rect;
    onChange({ rect: clampRect([x, y, w, w / a], doc.canvas) });
  };

  const crop = o.crop || [0, 0, 1, 1];
  const setCrop = (i, pct) => {
    if (pct == null) return;
    const c = [...crop];
    c[i] = Math.min(1, Math.max(0, pct / 100));
    c[2] = Math.max(0.01, Math.min(c[2], 1 - c[0]));
    c[3] = Math.max(0.01, Math.min(c[3], 1 - c[1]));
    const full = c[0] === 0 && c[1] === 0 && c[2] === 1 && c[3] === 1;
    setOpt("crop", full ? null : c.map((v) => Math.round(v * 10000) / 10000));
  };

  const autoWin = autoWindowSeconds(tile.rect[2], durationS, info?.playhead_pps,
    info?.max_strip_px);

  return (
    <div className="tile-inspector">
      <div className="tile-inspector__head">
        <strong>{tile.kind === "camera" ? "Camera" : "Spectrogram"}</strong>
        <span className="tile-inspector__actions">
          <button className="btn btn-small" onClick={() => onZ("front")} title="Bring to front">Front</button>
          <button className="btn btn-small" onClick={() => onZ("back")} title="Send to back">Back</button>
          <button className="btn btn-small" onClick={onDuplicate} title="Duplicate this tile">Duplicate</button>
          <button className="btn btn-small tile-inspector__delete" onClick={onDelete}>Delete</button>
        </span>
      </div>

      <label className="tile-inspector__field tile-inspector__field--wide">
        <span>Source</span>
        <select value={tile.source} onChange={(e) => onChange({ source: e.target.value })}>
          {tile.kind === "camera"
            ? cameras.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)
            : micDevices.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          {!(tile.kind === "camera" ? cameras.some((c) => c.name === tile.source)
            : micDevices.some((d) => d.id === tile.source)) && (
            <option value={tile.source}>{tile.source} (not in this day)</option>
          )}
        </select>
      </label>

      <div className="tile-inspector__grid">
        <Num label="X" value={tile.rect[0]} onChange={(v) => setRect(0, v)} min={0} suffix="px" />
        <Num label="Y" value={tile.rect[1]} onChange={(v) => setRect(1, v)} min={0} suffix="px" />
        <Num label="Width" value={tile.rect[2]} onChange={(v) => setRect(2, v)} min={LIMITS.minTile} suffix="px" />
        <Num label="Height" value={tile.rect[3]} onChange={(v) => setRect(3, v)} min={LIMITS.minTile} suffix="px" />
      </div>

      <label className="tile-inspector__check">
        <input type="checkbox" checked={!!o.label} onChange={(e) => setOpt("label", e.target.checked)} />
        Show name label
      </label>

      {tile.kind === "camera" && (
        <>
          <div className="tile-inspector__grid">
            <label className="tile-inspector__field">
              <span>Fit</span>
              <select value={o.fit} onChange={(e) => setOpt("fit", e.target.value)}>
                <option value="contain">Whole frame (letterbox)</option>
                <option value="cover">Fill (crop edges)</option>
                <option value="stretch">Stretch</option>
              </select>
            </label>
            <label className="tile-inspector__field">
              <span>Rotate</span>
              <select value={o.rotate} onChange={(e) => setOpt("rotate", Number(e.target.value))}>
                {[0, 90, 180, 270].map((r) => <option key={r} value={r}>{r}°</option>)}
              </select>
            </label>
          </div>
          <div className="tile-inspector__sub">
            Crop (% of the frame)
            {o.crop && (
              <button className="btn btn-small" onClick={() => setOpt("crop", null)}>Clear crop</button>
            )}
          </div>
          <div className="tile-inspector__grid">
            <Num label="Left" value={Math.round(crop[0] * 1000) / 10} onChange={(v) => setCrop(0, v)} min={0} max={99} step={0.5} suffix="%" />
            <Num label="Top" value={Math.round(crop[1] * 1000) / 10} onChange={(v) => setCrop(1, v)} min={0} max={99} step={0.5} suffix="%" />
            <Num label="Width" value={Math.round(crop[2] * 1000) / 10} onChange={(v) => setCrop(2, v)} min={1} max={100} step={0.5} suffix="%" />
            <Num label="Height" value={Math.round(crop[3] * 1000) / 10} onChange={(v) => setCrop(3, v)} min={1} max={100} step={0.5} suffix="%" />
          </div>
          <button className="btn btn-small" onClick={fitToSource} disabled={!cam}
            title="Set the tile's height so the (cropped, rotated) camera fills it with no letterbox">
            Match tile to camera shape
          </button>
        </>
      )}

      {tile.kind === "spectrogram" && (
        <>
          <label className="tile-inspector__check">
            <input type="checkbox" checked={isTrack} onChange={(e) => onToggleTrack(e.target.checked)} />
            Also include this microphone as an audio track
          </label>
          <div className="tile-inspector__sub">Colour: <strong>{o.color}</strong></div>
          <div className="compose-panel__swatches">
            {SPEC_COLORS.map((c) => (
              <button
                type="button" key={c} title={c} aria-label={c}
                className={`compose-panel__swatch${o.color === c ? " compose-panel__swatch--active" : ""}`}
                onClick={() => setOpt("color", c)}
              >
                {SWATCHES[c] ? <img src={SWATCHES[c]} alt="" />
                  : <span className="compose-panel__swatch-fallback">{c}</span>}
              </button>
            ))}
          </div>
          <div className="tile-inspector__grid">
            <Num label="Freq min" value={o.fmin_hz} onChange={(v) => setOpt("fmin_hz", v ?? 0)} min={0} step={1000} suffix="Hz" />
            <Num label="Freq max" value={o.fmax_hz} onChange={(v) => setOpt("fmax_hz", v)} min={1000} step={1000} suffix="Hz" />
            <label className="tile-inspector__field">
              <span>Freq scale</span>
              <select value={o.fscale} onChange={(e) => setOpt("fscale", e.target.value)}>
                <option value="lin">Linear</option>
                <option value="log">Log</option>
              </select>
            </label>
            <Num label="Gain" value={o.gain} onChange={(v) => setOpt("gain", v ?? 1)} min={0.1} max={20} step={0.5} />
          </div>
          <div className="tile-inspector__grid">
            <label className="tile-inspector__field" title="Seconds of audio across the tile">
              <span>Window</span>
              <span className="tile-inspector__input">
                <input
                  type="number" min={LIMITS.windowMin} max={LIMITS.windowMax} step={1}
                  value={o.window_s ?? ""}
                  placeholder={autoWin ? `auto (${autoWin.toFixed(1)})` : "auto"}
                  onChange={(e) => setOpt("window_s", e.target.value === "" ? null : Number(e.target.value))}
                />
                <em>s</em>
              </span>
            </label>
            <Num
              label="Playhead" value={Math.round((o.playhead ?? 0.5) * 100)}
              onChange={(v) => setOpt("playhead", Math.min(100, Math.max(0, v ?? 50)) / 100)}
              min={0} max={100} step={5} suffix="%"
              title="Where 'now' sits across the tile: 0% = left edge (only upcoming audio), 50% = centre"
            />
          </div>
          <p className="tile-inspector__hint">
            The preview shows the aligned audio around the previewed moment; the
            render scrolls it past the playhead.
          </p>
        </>
      )}
    </div>
  );
}
