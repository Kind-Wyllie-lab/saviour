// Shared formatting for the post-hoc sync-quality verdict
// (backend: src/controller/framesync_check.py). Every session object carries
// `framesync_verdict` (slim dict) and, for a Habitat Session, `day_verdicts`
// ({ "YYYYMMDD": slim dict }).

export const SYNC_LABEL = {
  green: "✓ sync",
  amber: "sync ⚠",
  red: "sync ✗",
  skipped: "sync –",
  error: "sync ?",
};

// Maps to the config-sync-badge / session-warning-text visual vocabulary.
export const SYNC_CLASS = {
  green: "green",
  amber: "amber",
  red: "red",
  skipped: "muted",
  error: "muted",
};

export const SYNC_TITLE = {
  green: "Sync quality validated",
  amber: "Sync quality validated with warnings",
  red: "Sync quality: problems found",
  skipped: "Sync quality not evaluated for this session",
  error: "Sync check did not complete",
};

// One-line "why" from the verdict's `worst` block, e.g.
// "PTP p95 21 µs · inter-cam p95 19 µs · drift 0.03 µs/s · 0.0% dropped".
export function worstSummary(v) {
  if (!v) return "";
  const w = v.worst || {};
  const parts = [];

  const ptpNs = Math.max(w.ptp4l_p95_ns ?? 0, w.phc2sys_p95_ns ?? 0);
  if (ptpNs) parts.push(`PTP p95 ${Math.round(ptpNs / 1000)} µs`);

  if (v.phase_lock_evaluated && w.detrended_p95_us != null) {
    parts.push(`inter-cam p95 ${Math.round(w.detrended_p95_us)} µs`);
  }
  if (w.drift_us_per_sec != null && Math.abs(w.drift_us_per_sec) >= 0.5) {
    parts.push(`drift ${w.drift_us_per_sec.toFixed(2)} µs/s`);
  }
  if (w.max_dropped_frac != null) {
    const pct = w.max_dropped_frac * 100;
    parts.push(`${pct.toFixed(pct >= 1 ? 1 : 2)}% dropped`);
  }

  if (parts.length) return parts.join(" · ");
  return (v.reasons && v.reasons[0]) || "";
}

// `report_rel` is "<session>/<YYYYMMDD>/framesync_report.json" (relative to the
// share); the per-file download route wants the path relative to the session
// dir, so drop the leading session segment -- same as ComposeVideoPanel.
export function reportDownloadUrl(sessionName, reportRel) {
  if (!reportRel) return null;
  const rel = reportRel.split("/").slice(1).map(encodeURIComponent).join("/");
  return `/api/sessions/${encodeURIComponent(sessionName)}/download/${rel}`;
}
