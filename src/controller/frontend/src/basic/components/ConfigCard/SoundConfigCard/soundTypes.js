// Generator sound types (src/shared/sound_generator.py TYPES), for the
// New sound window and the library's descriptions.
export const TYPES = [
  { key: "tone", label: "Tone", help: "A pure tone, e.g. a conditioning cue." },
  { key: "noise", label: "Noise", help: "Coloured, band-limited noise: startle pulses, prepulses, background." },
  { key: "gap", label: "Gap in noise", help: "Noise with a silent gap, for gap-prepulse inhibition." },
  { key: "click_train", label: "Click train", help: "Short rectangular clicks at a fixed rate." },
  { key: "am_tone", label: "AM tone", help: "A tone whose amplitude is modulated." },
  { key: "fm_sweep", label: "Sweep", help: "A tone gliding from one frequency to another." },
];
