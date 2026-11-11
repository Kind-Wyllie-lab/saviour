# Spectrogram palettes and call-band colouring

- **Status:** in progress: phase 1 (shared palettes + monitor) built 2026-11-11 on
  `feat/spectrogram-palettes`, not yet seen on the rig; phases 2-4 not started
- **Created:** 2026-11-11
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

Two requests for the live monitor and the post-processing spectrograms:

- **A. Custom colour palettes**, not only the fixed built-in lists.
- **B. Colour by call band.** The rat ~22 kHz band (aversive / alarm calls)
  in one colour and the ~50 kHz band (appetitive calls) in another, so a
  researcher can see at a glance which kind of call is happening.

## How it works today

There are two separate renderers, each with its **own palette list**:

| Where | Renderer | Palette setting | Palettes |
|---|---|---|---|
| Live monitor (module MJPEG, `:8081`) | OpenCV in `microphone_module.py` (`_render_monitor_cell`, `else: # spectrogram` branch ~L1340): dB → uint8 → `cv2.applyColorMap` | `monitoring.colormap` | `_SPEC_COLORMAPS`: inferno, magma, plasma, viridis, jet, hot, bone, ocean, turbo, grayscale |
| Post-processing | ffmpeg `showspectrum(pic)` built from `audio_align.SpectrogramOpts` | `SpectrogramOpts.color` (compose tile option, validated in `compose_layout._validate_spectrogram_options`) | `SPEC_COLORS`: ffmpeg's enum (intensity, rainbow, moreland, nebulae, fire, …) |

Post-processing render paths that use `SpectrogramOpts`:

- `render_spectrogram_png` (with legend): audio-align report PNG
- `render_source_spectrogram_png` / `render_window_spectrogram_png`:
  compose preview tiles (`compose.py` ~L883 / ~L942)
- `render_strip_png`: the compose render's spectrogram strip, panned
  frame by frame in OpenCV by `video_compose.spectrogram_pane`
- `render_overlay`: audio-align report overlay
- `render_ethogram`: audio-align report; the scrolling panel is ffmpeg
  `showspectrum slide=scroll` inside the filtergraph

ffmpeg can't take a custom palette, and it can't colour different rows
differently, so neither A nor B is possible with ffmpeg alone.

Frontend: `MicrophoneConfigCard.jsx` (swatch picker from committed PNGs,
`tools/gen_microphone_colormap_swatches.py`), and `TileInspector.jsx` +
`composeLayout.js::SPEC_COLORS` for compose tiles.

## Key finding: ffmpeg can render greyscale (spike, 2026-11-11)

`showspectrumpic=…:color=intensity:saturation=0` gives a true greyscale
image (R=G=B at every pixel). It is monotonic in magnitude (a rising tone
went 0 → 108 → 145 → … → 206 along the row) and has about 200 distinct
levels. So post-processing can **keep ffmpeg** for the STFT and its
`scale` / `fscale` / `gain` behaviour, which saved layouts depend on.
ffmpeg makes the greyscale image and we add the colour in numpy. No
numpy STFT and no change to how loudness maps to brightness.

## Decisions (2026-11-11)

- Band labels: **"Alarm (22 kHz)"** and **"Appetitive (50 kHz)"**.
- Band edges: **alarm 18–32 kHz, appetitive 45–96 kHz.** 96 kHz is
  Nyquist at 192 kHz; the AudioMoth's usable response tails off above
  ~70 kHz (`HARDWARE_NOTES.md`), so the top of the band will look faint.
  That's the microphone, not the rendering.
- **Grey outside the bands:** while any band is on, rows outside every
  band are drawn in `grayscale`, so only the bands are in colour. With no
  bands on, the chosen palette covers the whole image.
- **Explicit colour mode** (added after phase 1): `colour_mode` is
  `"palette"` (one palette, whole image; saved bands kept but not drawn) or
  `"bands"` (bands in their palettes, grey elsewhere, all grey with none
  on). Unset = bands if any are on. The UI is a two-button toggle,
  **One palette | Frequency bands**, showing only the controls that apply.
  Resolved by `spectrogram_palette.resolve_colouring`, which compose tiles
  use too (phase 2: `SpectrogramOpts.colour_mode`).
- **Custom palettes can be exported** (and imported) as JSON, so they
  move between rigs.
- **ffmpeg colour schemes go away.** Every post-processing path uses the
  SAVIOUR palettes (greyscale ffmpeg + our LUT). Saved layouts that name
  an ffmpeg colour are mapped to the nearest SAVIOUR palette, so they
  render, but **not pixel-identical** to before.

## Goals / non-goals

- One palette system shared by the monitor and every post-processing path,
  so a palette name looks the same everywhere.
- Palettes defined as colour stops; users can make and save their own.
- Frequency bands drawn with their own palette, configurable, with rat
  presets.
- **Not** call detection or classification. Band colouring goes **by
  frequency only**: noise in 45–96 kHz is coloured like a 50 kHz call, and
  not every 50 kHz call is appetitive (FM / trill calls are; flat 50 kHz
  calls are less clear). Detection is a separate question (the DeepSqueak
  discussion, not planned yet).

## Design

### 1. Shared palette module: `src/shared/spectrogram_palette.py`

Pure numpy, no cv2 needed; modules and the controller both have numpy.

- `Palette`: a name plus a list of stops `[(pos 0–1, "#rrggbb"), …]`, ≥2,
  positions strictly increasing from 0 to 1. `lut(palette) -> (256, 3)
  uint8` (BGR, since both consumers are OpenCV), with linear RGB
  interpolation. Cache by palette.
- Built-ins as stops: `grayscale`, `inferno`, `magma`, `plasma`, `viridis`,
  `cividis`, `turbo` (≈ 9–16 stops each, sampled from matplotlib's
  tables, enough that you can't see the steps), plus `jet` / `hot` /
  `bone` / `ocean` so current monitor configs keep working, plus the two
  band ramps (`band_orange`, `band_blue`; the first names `alarm_blue` /
  `appetitive_orange` stay as aliases for their original colours).
- `LEGACY_FFMPEG_ALIASES`: ffmpeg colour name → nearest SAVIOUR palette.
  Same-name maps (`magma`, `viridis`, `plasma`, `cividis`) are 1:1;
  `intensity` → `inferno` (also the new default); `fire` / `fiery` → `hot`;
  `rainbow` → `turbo`; `cool` → `ocean`; `moreland`, `nebulae`, `fruit`,
  `terrain`, `green`, `channel` → closest by eye, picked when building
  (compare swatches side by side).
- `CallBand`: `name`, `lo_hz`, `hi_hz`, `palette`, `enabled`.
- `colourise(grey: (H, W) uint8, base: Palette, bands: list[CallBand],
  row_hz: (H,) float) -> (H, W, 3) uint8`. With no enabled bands, every
  row uses the base LUT. With bands, each row uses the LUT of the band
  containing its frequency, and every other row uses `grayscale`. Band
  edges are hard (one row) because the frequency is the information here.
- `export_palettes(list) -> str` / `import_palettes(str) -> list`: a
  versioned JSON document (`{"saviour_palettes": 1, "palettes": [{name,
  stops}]}`), validated on import. Name clashes are reported back so the
  UI can rename or overwrite.
- `row_frequencies(height, fmin, fmax, fscale)`: row → Hz for `lin`, and
  for `log` / `rlog` **matching ffmpeg's mapping** (checked by test, see
  below). The monitor's axis is always linear.
- Validation: bands must not overlap, `lo < hi`, and at most ~4 bands.

### 2. Presets

| Preset | Range | Palette | Label |
|---|---|---|---|
| `rat_alarm` | 18–32 kHz | `band_orange` (black → #ff8a00 → #fff0c0) | "Alarm (22 kHz)" |
| `rat_appetitive` | 45–96 kHz | `band_blue` (black → #2b6cff → #cfe0ff) | "Appetitive (50 kHz)" |

Alarm is orange (the usual warning colour, and the brighter of the two, so
it draws the eye); appetitive is blue. Swapped from the first version on
2026-11-11. Blue against orange, not red against green (colour-blind safe). Rows
outside both bands are grey. The labels describe the usual meaning of each
band, but the colouring is still by frequency only (see Goals). Mouse
presets later if anyone asks.

### 3. Where settings live

- **Palette library** (custom palettes, shared by the rig): controller
  config `spectrogram.palettes` (list of `{name, stops}`). Built-ins are
  in code and can't be edited.
- **Monitor**, per microphone module config:
  - `monitoring.colormap`: palette name (existing key, unchanged meaning)
  - `monitoring.colormap_stops`: the **resolved stops**, written by the
    controller when a custom palette is chosen, so the module never has to
    ask the controller for anything. Empty means a built-in.
  - `monitoring.call_bands`: list of `CallBand` dicts, default `[]` (off)
- **Compose tile** (`SpectrogramOpts`): `color` is replaced by `palette`
  (name or `{name, stops}`, resolved and frozen into the layout when it's
  saved, so a layout renders the same even if the library entry changes
  later) and `bands`. Default `palette: "inferno"`.

**Old layouts:** on load, `compose_layout` maps a legacy `color` to
`palette` via `LEGACY_FFMPEG_ALIASES` (and the same for any saved template
or render request with `spectrogram.color`). The stored file is rewritten
only when the user saves it again. Unknown names fall back to `inferno`
with a warning, never an error.

### 4. Monitor rendering (`microphone_module.py`)

Replace the `applyColorMap` block with: resolve the palette (stops in
config, or a built-in name) → LUT; `row_hz = linspace(plot_hi_hz,
plot_lo_hz, PLOT_H)` (rows are flipped); `colourise(spec_norm, …)`. Cost
is one fancy-index per frame on about 600×200 px, negligible next to the
FFT. On the frequency axis, draw a thin coloured tick bar on the left
edge of the plot for each band, plus its label, so the colour key is
always on screen. A band outside the plotted range (`freq_lo_hz` /
`freq_hi_hz`, default 20–70 kHz, so most of `rat_appetitive` above
70 kHz is off-plot unless the range is widened)
draws as much as it can.

### 5. Post-processing rendering (`audio_align.py`, `video_compose.py`)

Every path goes through the same pipeline:

- `_common()` always emits `color=intensity:saturation=0` (greyscale).
  `SPEC_COLORS` and the `color` validation are removed.
- **PNG paths** (`render_source_spectrogram_png`,
  `render_window_spectrogram_png`, `render_strip_png`): ffmpeg writes
  greyscale → load → `colourise` with `row_frequencies(h, fmin, fmax,
  fscale)` → write back. One shared helper, `_recolour_png(path, opts)`.
  The PNG is decoded once and rewritten once, cheap next to the ffmpeg
  STFT.
- **`render_spectrogram_png` with legend:** ffmpeg's legend layout puts
  the plot at an offset that depends on the build. Render the plot area
  without the legend, recolour it, then draw the axes in OpenCV (kHz
  ticks, time ticks) plus a band key. We get the same look on every
  ffmpeg version, and only one place draws axes.
- **`render_ethogram` scrolling panel:** move it off ffmpeg
  `slide=scroll` onto the compose method: one strip PNG for the window
  (recoloured), panned per frame by `video_compose.spectrogram_pane`.
  `render_ethogram` already writes the top panel with OpenCV, so the
  bottom panel goes in the same loop, and the ffmpeg graph shrinks to a
  mux. This also removes the `shortest=1` workaround.
- **`render_overlay`:** same strip approach, or the PNG helper, whichever
  it uses on inspection.

### 6. Frontend

- `PaletteEditor` (in `basic/components/`): list of stops (colour input +
  position), gradient preview drawn as a CSS `linear-gradient` from the
  stops, save to the library / delete from it, **Export** (downloads
  `saviour-palettes.json`) and **Import** (file picker; shows clashes and
  asks to rename or overwrite each one). Every swatch is a generated
  gradient: the committed swatch PNGs (`src/assets/mic-colormap-swatches/`,
  the compose tile's ffmpeg swatches) and
  `tools/gen_microphone_colormap_swatches.py` are deleted, and
  `composeLayout.js::SPEC_COLORS` becomes the SAVIOUR palette list.
- `CallBandsEditor`: preset buttons (`rat_alarm`, `rat_appetitive`), lo/hi
  kHz, palette per band, enable toggle. A note says rows outside the bands
  are drawn grey. It warns
  when a band is above the module's Nyquist (48 kHz → nothing above
  ~5 kHz is usable; `HARDWARE_NOTES.md`) or outside the plotted range.
- Used in `MicrophoneConfigCard.jsx` (monitor) and `TileInspector.jsx`
  (compose tile). Library read/write goes over Socket.IO like other
  controller config. The `npm run dev:mock` fixtures need the new keys.
- `npm run build` for all 5 variants; `basic` is shared, so `apa` /
  `habitat` pick it up.

## Phases

1. **Shared module + monitor.** *Built 2026-11-11.* Palette data is
   `src/shared/spectrogram_palettes.json` (generated by
   `tools/gen_spectrogram_palettes.py` from OpenCV's LUTs, within 2/255),
   read by `src/shared/spectrogram_palette.py` and, through the
   `@saviour-shared` Vite alias, by `basic/utils/spectrogramPalettes.js`.
   Monitor swatches are CSS gradients; the PNG swatches and their generator
   are gone. `CallBandsEditor` is on the Monitor tab. Left for the rig: see
   it live with real calls.
   Original scope: `spectrogram_palette.py` with tests;
   monitor uses the LUT and draws bands; config keys; `MicrophoneConfigCard`
   gets a palette dropdown and the band editor (presets + lo/hi). Can be
   checked live on the desk rig at once.
2. **Post-processing PNGs.** Greyscale ffmpeg + `_recolour_png` for the
   preview tiles and compose strips; `SpectrogramOpts` (`color` →
   `palette`), legacy alias mapping in layout validation, `TileInspector`
   gets SAVIOUR palettes and bands.
3. **Report paths.** Our own axes for `render_spectrogram_png`; ethogram
   and overlay switched to the strip method.
4. **Custom palette library.** `PaletteEditor` with export/import,
   controller `spectrogram.palettes`, controller writes resolved stops to
   modules.

Phases 1–3 work with built-in palettes only. Phase 4 adds editing.

## Tests

- `lut()`: endpoints exact, monotonic for single-hue ramps, rejects bad
  stops.
- `colourise()`: with no bands every row uses the base LUT; with bands,
  in-band rows use that band's LUT and outside rows are grey (R=G=B);
  overlapping bands rejected.
- Export → import round-trips; malformed / wrong-version documents are
  rejected with a message; clashes reported.
- **`row_frequencies` vs ffmpeg:** synthesise tones at 22 / 50 / 70 kHz
  (192 kHz lavfi `sine`), render greyscale with each `fscale`, check the
  brightest row lands within ±1 row of the predicted row. This pins the
  log / rlog mapping to the ffmpeg version installed.
- Greyscale check: `saturation=0` output has R=G=B (guards against an
  ffmpeg change).
- Layout validation: every legacy ffmpeg `color` maps to a valid
  palette; an unknown name falls back to `inferno`; new fields validated.
- Monitor: render a frame from a synthetic `spec_buf` containing a 22 kHz
  and a 50 kHz line, and check the pixel hue in each band.

## Acceptance

- On the desk rig: the monitor shows a 22 kHz test tone in blue and a
  50 kHz tone in orange (an ultrasonic speaker or the AudioMoth's own
  playback, if available; otherwise a recorded rat file replayed through
  the test harness).
- A compose render with band colouring matches the monitor's colours for
  the same palette and bands.
- Every saved layout and template from before the change still loads and
  renders (colours mapped to the nearest SAVIOUR palette).
- ruff + pytest + 5-variant frontend build green.

## Open questions

- Which SAVIOUR palette each of the "closest by eye" ffmpeg names maps to
  (`moreland`, `nebulae`, `fruit`, `terrain`, `green`, `channel`): decide
  while building, from swatches side by side.
