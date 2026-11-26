// Generator sound types (src/shared/sound_generator.py TYPES), for the
// New sound window and the library's descriptions.
export const TYPES = [
  { key: "tone", label: "Tone", help: "A pure tone, e.g. a conditioning cue." },
  { key: "noise", label: "Noise", help: "Coloured, band-limited noise: startle pulses, prepulses, background." },
  { key: "gap", label: "Gap in noise", help: "Noise with a silent gap, for gap-prepulse inhibition." },
  { key: "click", label: "Click", help: "One short rectangular click; a protocol repeats it." },
  { key: "am_tone", label: "AM tone", help: "A tone whose amplitude is modulated." },
  { key: "fm_sweep", label: "Sweep", help: "A tone gliding from one frequency to another." },
];

// What a generated sound's level means. Generator v2: the RMS over the
// plateau (without the ramps) for everything but a click (peak). v1 files
// (2026-11-26 morning) used the peak except for noise and gaps.
export function levelKind(g) {
  if ((g?.generator_version ?? 2) < 2) return g?.type === "noise" || g?.type === "gap" ? "RMS" : "peak";
  return g?.type === "click" || g?.type === "click_train" ? "peak" : "RMS";
}
