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

// Live PTP-sync chip for the session detail page -- deliberately distinct
// from the post-hoc camera-frame-sync verdict above (which is inter-camera
// timestamp alignment, not PTP). While a session runs, the monitor sets
// `session.ptp_warning` to a string whenever a member module is over the PTP
// gate and clears it on recovery, so that's the live signal. Once stopped
// there's no live signal, so fall back to the worst per-day PTP p95 the
// framesync check recorded.
export function ptpChip(session) {
  if (!session) return { cls: "muted", label: "PTP –", title: "" };
  const live = session.state === "active" || session.state === "paused";
  if (live) {
    return session.ptp_warning
      ? { cls: "amber", label: "PTP ⚠", title: session.ptp_warning }
      : {
          cls: "green",
          label: "✓ PTP",
          title: "All modules within the PTP sync gate",
        };
  }
  const w = session.framesync_verdict?.worst || {};
  const ptpNs = Math.max(w.ptp4l_p95_ns ?? 0, w.phc2sys_p95_ns ?? 0);
  if (!ptpNs) {
    return {
      cls: "muted",
      label: "PTP –",
      title: "No PTP statistics recorded for this session",
    };
  }
  const us = Math.round(ptpNs / 1000);
  return {
    cls: us > 20 ? "amber" : "green",
    label: `PTP p95 ${us} µs`,
    title: "Worst per-day PTP p95 offset across the recording",
  };
}

// `report_rel` is "<session>/<YYYYMMDD>/framesync_report.json" (relative to the
// share); the per-file download route wants the path relative to the session
// dir, so drop the leading session segment -- same as ComposeVideoPanel.
export function reportDownloadUrl(sessionName, reportRel) {
  if (!reportRel) return null;
  const rel = reportRel.split("/").slice(1).map(encodeURIComponent).join("/");
  return `/api/sessions/${encodeURIComponent(sessionName)}/download/${rel}`;
}
