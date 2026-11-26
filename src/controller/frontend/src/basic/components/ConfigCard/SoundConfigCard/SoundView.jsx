import { useEffect, useRef } from "react";
import { lutRgb, paletteByName } from "/src/basic/utils/spectrogramPalettes";

// One sound file's analysis (src/shared/sound_analysis.py): the figures,
// the plain-words warnings, the waveform (min/max envelope per channel) and
// a coarse spectrogram with the output's usable band marked.

const fmtHz = (hz) => (hz == null ? "–" : hz >= 1000 ? `${(hz / 1000).toFixed(hz >= 10000 ? 1 : 2)} kHz` : `${Math.round(hz)} Hz`);
const fmtDb = (db) => (db == null ? "–" : `${db.toFixed(1)} dBFS`);
const fmtS = (s) => (s == null ? "–" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(s < 10 ? 2 : 1)} s`);
const FORMAT_LABEL = { pcm8: "8-bit PCM", pcm16: "16-bit PCM", pcm24: "24-bit PCM", pcm32: "32-bit PCM",
  float32: "32-bit float", float64: "64-bit float" };

function Waveform({ envelope, duration }) {
  if (!envelope?.channels?.length) return null;
  const W = 800, H = 46;
  const chans = envelope.channels;
  return (
    <div className="sound-view__plot">
      {chans.map((c, ci) => {
        const n = c.max.length;
        const x = (i) => (i / Math.max(1, n - 1)) * W;
        const y = (v) => H / 2 - v * (H / 2 - 1);
        const top = c.max.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        const bottom = c.min.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse();
        return (
          <div key={ci} className="sound-view__wave-row">
            <span className="sound-view__axis-label">{chans.length > 1 ? (ci === 0 ? "L" : "R") : ""}</span>
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="sound-view__wave"
              role="img" aria-label={`Waveform, channel ${ci + 1}`}>
              <line x1="0" x2={W} y1={H / 2} y2={H / 2} className="sound-view__zero" />
              <line x1="0" x2={W} y1={y(1)} y2={y(1)} className="sound-view__full" />
              <line x1="0" x2={W} y1={y(-1)} y2={y(-1)} className="sound-view__full" />
              <polygon points={[...top, ...bottom].join(" ")} className="sound-view__wave-fill" />
            </svg>
          </div>
        );
      })}
      <div className="sound-view__time-axis"><span>0</span><span>{fmtS(duration)}</span></div>
    </div>
  );
}

function Spectrogram({ spec, usableHz, palette = "magma" }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !spec?.db?.length) return;
    const { cols, rows, db, floor_db: floor } = spec;
    canvas.width = cols;
    canvas.height = rows;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(cols, rows);
    const lut = lutRgb(paletteByName(palette).stops);
    // Fixed scale: floor .. 0 dBFS, so files compare at a glance.
    for (let c = 0; c < cols; c++) {
      for (let r = 0; r < rows; r++) {
        const v = Math.max(0, Math.min(255, Math.round(((db[c][r] - floor) / -floor) * 255)));
        const p = ((rows - 1 - r) * cols + c) * 4;
        img.data[p] = lut[v * 3]; img.data[p + 1] = lut[v * 3 + 1]; img.data[p + 2] = lut[v * 3 + 2];
        img.data[p + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [spec, palette]);
  if (!spec?.db?.length) return null;
  const usablePct = usableHz && spec.max_hz > usableHz ? (1 - usableHz / spec.max_hz) * 100 : null;
  return (
    <div className="sound-view__plot">
      <div className="sound-view__spec-row">
        <div className="sound-view__freq-axis">
          <span>{fmtHz(spec.max_hz)}</span><span>{fmtHz(spec.max_hz / 2)}</span><span>0</span>
        </div>
        <div className="sound-view__spec-wrap">
          <canvas ref={ref} className="sound-view__spec" aria-label="Spectrogram" />
          {usablePct != null && (
            <div className="sound-view__usable" style={{ top: `${usablePct}%` }}
              title={`Above ${fmtHz(usableHz)} is removed at playback`}>
              <span>{fmtHz(usableHz)}: output limit</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function SoundView({ entry }) {
  const a = entry?.analysis;
  if (!a) return null;
  if (a.error) return <p className="sound-error">{a.error}</p>;
  const sp = a.spectrum ?? {};
  const rows = [
    ["Format", `${FORMAT_LABEL[a.format] ?? a.format ?? "–"}, ${a.channels?.length === 1 ? "mono" : `${a.channels?.length ?? "?"} channels`}`],
    ["Sample rate", fmtHz(a.sample_rate)],
    ["Length", fmtS(a.duration_s)],
    ["Peak", fmtDb(a.peak_dbfs)],
    ["RMS", fmtDb(a.rms_dbfs)],
    ["Crest factor", a.crest_db == null ? "–" : `${a.crest_db.toFixed(1)} dB`],
    ["Main frequency", fmtHz(sp.dominant_hz)],
    ["Band (−10 dB)", sp.band_hz ? `${fmtHz(sp.band_hz[0])} – ${fmtHz(sp.band_hz[1])}` : "–"],
    ["Above output limit", sp.above_usable_frac == null ? "–" : `${(sp.above_usable_frac * 100).toFixed(sp.above_usable_frac < 0.1 ? 1 : 0)} % of energy`],
    ["Silence before / after", `${fmtS(a.leading_silence_s)} / ${fmtS(a.trailing_silence_s)}`],
    ["Stereo", a.stereo ?? "–"],
  ];
  return (
    <div className="sound-view">
      {a.warnings?.length > 0 ? (
        <ul className="sound-view__warnings">
          {a.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      ) : (
        <p className="sound-ok">No problems found.</p>
      )}
      <Waveform envelope={a.envelope} duration={a.duration_s} />
      <Spectrogram spec={a.spectrogram} usableHz={a.usable_hz} />
      <table className="sound-view__facts">
        <tbody>
          {rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}
        </tbody>
      </table>
      {entry.sha256 && <p className="sound-muted sound-view__hash" title={entry.sha256}>SHA-256 {entry.sha256.slice(0, 12)}…</p>}
    </div>
  );
}
