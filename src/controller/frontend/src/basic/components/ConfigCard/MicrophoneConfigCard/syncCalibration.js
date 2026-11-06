// Calibration status of one AudioMoth's stored sync offset
// (audiomoth_sync_offsets_ms entry), for the microphone card's Sync tab.

const STALE_DAYS = 30;

function ago(seconds) {
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  const d = Math.round(seconds / 86400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export const khz = (hz) => `${Number(hz) / 1000} kHz`;

/** {level: ok|warn|none, title, notes[]} for one AudioMoth's stored entry. */
export function calibrationStatus(entry, current, nowS = Date.now() / 1000) {
  if (entry === undefined || entry === null) {
    return { level: "none", title: "Not calibrated",
             notes: ["Audio is placed to about ±2 ms (default offset). Run the self-test."] };
  }
  if (typeof entry === "number") {
    return { level: "warn", title: "Calibrated, date unknown",
             notes: [`Offset ${entry.toFixed(2)} ms. Run the self-test to record when.`] };
  }
  const notes = [];
  if (entry.sample_rate && current.sampleRate && entry.sample_rate !== current.sampleRate) {
    notes.push(`Measured at ${khz(entry.sample_rate)}; now ${khz(current.sampleRate)}.`);
  }
  if (entry.block_size && current.blockSize && entry.block_size !== current.blockSize) {
    notes.push(`Measured with ${entry.block_size}-sample blocks; now ${current.blockSize}.`);
  }
  const age = entry.calibrated_at ? nowS - entry.calibrated_at : null;
  if (age !== null && age > STALE_DAYS * 86400) {
    notes.push(`Over ${STALE_DAYS} days old.`);
  }
  const level = notes.length ? "warn" : "ok";
  const title = age === null ? "Calibrated" : `Calibrated ${ago(age)}`;
  const tone = entry.tone_hz ? `, ${khz(entry.tone_hz)} buzz` : "";
  notes.push(`Offset ${Number(entry.offset_ms).toFixed(2)} ms${tone}.`);
  return { level, title, notes };
}
