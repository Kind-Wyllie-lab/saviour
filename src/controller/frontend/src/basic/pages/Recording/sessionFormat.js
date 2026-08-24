// Shared formatting helpers used by both SessionList (compact rows) and
// SessionDetailPage (full detail) -- kept in one place so the two views
// can never format the same data differently. Component exports
// (Countdown/Elapsed/CopyButton) live in sessionFormatComponents.jsx
// instead -- react-refresh/only-export-components requires files to
// export either components or plain values, not both.

export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Sessions/folders larger than this will have "Download zip" disabled — use
// the NAS share instead. The zip itself streams straight from disk with no
// server-side buffering (see web.py's _stream_zip_response), so this is a
// UI guard against surprising an operator with a huge in-browser download,
// not a real backend limit — 25 GB comfortably covers a single day/module
// folder at habitat scale (largest observed in practice: ~22 GB for one
// camera's full day of continuous hourly segments) while still catching an
// accidental zip of an entire multi-week deployment share.
export const DOWNLOAD_ALL_MAX_BYTES = 25 * 1024 ** 3; // 25 GB

// Below this, a download (single file, folder zip, or whole-session zip)
// starts immediately on click. At or above it, the caller should confirm
// with the operator first — big enough to not nag on an ordinary clip, but
// still a heads-up before kicking off a download that could take a while.
export const DOWNLOAD_CONFIRM_BYTES = 1 * 1024 ** 3; // 1 GB

// Actually starts a download without a full page navigation (a plain
// `<a href>` click would also work for this, but every download in the file
// tree/session view is routed through here so the size-confirm flow above
// has exactly one place to intercept before committing to the click).
export function triggerDownload(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function formatFaultTime(error_time) {
  if (!error_time) return null;
  const m = error_time.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  return m ? `${m[4]}:${m[5]}` : null;
}

export function formatEpochTime(epochSeconds) {
  if (!epochSeconds) return null;
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function levelClass(line) {
  const m = line.match(/\[(\w+)\s*\]/);
  if (!m) return "";
  return `fault-alert-log-line--${m[1].toLowerCase()}`;
}

export function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDuration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0 && m > 0) return `${h}h ${m}m`;
  if (h > 0) return `${h}h`;
  return `${m}m`;
}

export function formatScheduledDays(days) {
  if (!days || days.length === 0 || days.length === 7) return "every day";
  return [...days].sort((a, b) => a - b).map(d => DAY_NAMES[d]).join(", ");
}

// session.target is "all", a module group name (e.g. "camera",
// "habitat_camera"), or a single module id -- readable as-is once
// underscores become spaces and it's capitalised, and it's set once at
// creation and never changes, so it stays a stable "what kind of session
// was this" label even long after the session has stopped.
export function formatTarget(target) {
  if (!target || target === "all") return "All modules";
  const spaced = target.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// The three ways a session can be started, distinguished the same way
// create_session()/create_scheduled_session() distinguish them on the
// backend: session.scheduled (a daily time-window session, set once at
// creation and never cleared even while its state cycles
// scheduled<->active over the day) beats duration_minutes (set only for
// a timed auto-stop session) beats neither (a plain manual start/stop
// session) -- also stable for the life of the session, same reasoning as
// formatTarget above.
export function formatRecordingMode(session) {
  if (session.scheduled) return "Scheduled";
  if (session.duration_minutes) return "Timed";
  return "Manual";
}
