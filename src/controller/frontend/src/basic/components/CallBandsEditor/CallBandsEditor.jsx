import {
  BAND_PRESETS, MAX_BANDS, PALETTES, bandProblems, paletteByName,
} from "/src/basic/utils/spectrogramPalettes";
import { BandPreview, PaletteSwatch } from "/src/basic/components/SpectrogramSwatch/SpectrogramSwatch";
import "./CallBandsEditor.css";

const khz = (hz) => +(Number(hz) / 1000).toFixed(2);

// Per-band warnings that don't stop the band working, just explain why part
// of it won't show: above Nyquist, or outside the plotted frequency range.
function bandWarnings(band, nyquistHz, plotLoHz, plotHiHz) {
  const lo = Number(band.lo_hz);
  const hi = Number(band.hi_hz);
  const out = [];
  if (nyquistHz && lo >= nyquistHz) {
    out.push(`Entirely above Nyquist (${khz(nyquistHz)} kHz) at this sample rate: nothing will show`);
  } else if (nyquistHz && hi > nyquistHz) {
    out.push(`Above Nyquist (${khz(nyquistHz)} kHz) from ${khz(nyquistHz)} kHz up`);
  }
  if (plotLoHz != null && plotHiHz != null && (!nyquistHz || lo < nyquistHz)) {
    if (hi <= plotLoHz || lo >= plotHiHz) {
      out.push(`Outside the plotted range (${khz(plotLoHz)}–${khz(plotHiHz)} kHz)`);
    } else if (lo < plotLoHz || hi > plotHiHz) {
      out.push(`Partly outside the plotted range (${khz(plotLoHz)}–${khz(plotHiHz)} kHz)`);
    }
  }
  return out;
}

/**
 * Edit a list of call bands: [{ id?, label, lo_hz, hi_hz, palette, enabled }].
 * Each enabled band is drawn in its own palette; everything outside the
 * bands is grey (src/shared/spectrogram_palette.py).
 */
export default function CallBandsEditor({ bands, onChange, nyquistHz, plotLoHz, plotHiHz }) {
  const list = bands ?? [];
  const set = (i, patch) => onChange(list.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const remove = (i) => onChange(list.filter((_, j) => j !== i));
  const hasPreset = (p) => list.some((b) => b.id === p.id
    || (Number(b.lo_hz) === p.lo_hz && Number(b.hi_hz) === p.hi_hz));
  const addPreset = (p) => onChange([...list, { ...p, enabled: true }]);
  const addCustom = () => onChange([...list, {
    label: "Band", lo_hz: 20000, hi_hz: 30000, palette: "alarm_blue", enabled: true,
  }]);
  const problems = bandProblems(list);
  const anyOn = list.some((b) => b.enabled !== false);

  return (
    <div className="call-bands">
      <div className="call-bands__head">
        <span className="call-bands__title">Call bands</span>
        <div className="call-bands__add">
          {BAND_PRESETS.map((p) => (
            <button type="button" key={p.id} disabled={hasPreset(p) || list.length >= MAX_BANDS}
              onClick={() => addPreset(p)} title={`${khz(p.lo_hz)}–${khz(p.hi_hz)} kHz`}>
              + {p.label}
            </button>
          ))}
          <button type="button" disabled={list.length >= MAX_BANDS} onClick={addCustom}>
            + Custom
          </button>
        </div>
      </div>

      {plotLoHz != null && plotHiHz > plotLoHz && (
        <div className="call-bands__preview">
          <BandPreview bands={list} loHz={plotLoHz} hiHz={Math.min(plotHiHz, nyquistHz || plotHiHz)}
            width={360} height={80} />
          <span className="call-bands__preview-caption">
            Example calls, {khz(plotLoHz)}–{khz(Math.min(plotHiHz, nyquistHz || plotHiHz))} kHz:
            how the monitor will colour them
          </span>
        </div>
      )}

      {list.length === 0 ? (
        <div className="call-bands__hint">
          No bands yet, so the spectrogram is all grey. Add a band to draw a frequency
          range (e.g. rat alarm or appetitive calls) in its own colours.
        </div>
      ) : (
        <>
          {list.map((b, i) => {
            const warnings = b.enabled === false ? []
              : bandWarnings(b, nyquistHz, plotLoHz, plotHiHz);
            return (
              <div key={i} className={`call-bands__row${b.enabled === false ? " call-bands__row--off" : ""}`}>
                <input type="checkbox" checked={b.enabled !== false} title="Show this band"
                  onChange={(e) => set(i, { enabled: e.target.checked })} />
                <input type="text" className="call-bands__label" value={b.label ?? ""}
                  placeholder="Label" maxLength={32}
                  onChange={(e) => set(i, { label: e.target.value })} />
                <input type="number" className="call-bands__khz" min="0" step="0.5"
                  value={khz(b.lo_hz)} title="Low (kHz)"
                  onChange={(e) => set(i, { lo_hz: Math.round(Number(e.target.value) * 1000) })} />
                <span>–</span>
                <input type="number" className="call-bands__khz" min="0" step="0.5"
                  value={khz(b.hi_hz)} title="High (kHz)"
                  onChange={(e) => set(i, { hi_hz: Math.round(Number(e.target.value) * 1000) })} />
                <span>kHz</span>
                <select value={paletteByName(b.palette).name}
                  onChange={(e) => set(i, { palette: e.target.value })}>
                  {PALETTES.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
                </select>
                <PaletteSwatch className="call-bands__swatch"
                  stops={paletteByName(b.palette).stops} width={56} height={18} />
                <button type="button" className="call-bands__remove" title="Remove band"
                  aria-label="Remove band" onClick={() => remove(i)}>×</button>
                {warnings.map((w) => (
                  <div key={w} className="call-bands__warn">{w}</div>
                ))}
              </div>
            );
          })}
          <div className="call-bands__hint">
            {anyOn ? "Outside the bands" : "With every band off, the whole spectrogram"} is
            grey. Colour goes by frequency only: any sound in a band, noise included, gets
            that band&apos;s colour.
          </div>
        </>
      )}
      {problems.map((p) => (
        <span key={p} className="config-sync-badge config-sync-badge--failed call-bands__problem">
          {p}: bands are ignored until this is fixed
        </span>
      ))}
    </div>
  );
}
