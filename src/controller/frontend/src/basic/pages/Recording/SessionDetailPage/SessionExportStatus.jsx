import React from "react";
import { formatBytes } from "../sessionFormat";
import "./SessionExportStatus.css";

// A module's progress report older than this is treated as stale (shown as
// "exporting…" without numbers) rather than as current.
const PROGRESS_STALE_S = 15;

const ordinal = (n) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};

/**
 * Where each of a session's modules is with getting its files onto the
 * share: exporting (files / bytes, with a bar), waiting in the queue
 * (modules export one at a time by default, to keep bulk traffic off the
 * PTP path), done, or failed. Normal progress is neutral; only a failure or
 * the controller's stall alert is shown as a problem.
 */
function moduleRow(moduleId, session, queue) {
  const name = session.session_name;
  const active = queue?.active?.find((a) => a.module_id === moduleId && a.session === name);
  if (active) {
    const p = active.progress;
    const fresh = p && Date.now() / 1000 - (p.updated ?? 0) < PROGRESS_STALE_S;
    if (fresh && p.bytes_total) {
      return {
        kind: "exporting",
        text: `Exporting ${p.files_done} / ${p.files_total} file${p.files_total !== 1 ? "s" : ""}`
          + ` · ${formatBytes(p.bytes_done)} / ${formatBytes(p.bytes_total)}`,
        frac: Math.min(1, p.bytes_done / p.bytes_total),
        detail: p.current_file || null,
      };
    }
    return { kind: "exporting", text: "Exporting…", frac: null };
  }
  const queued = queue?.queued?.find((q) => q.module_id === moduleId && q.session === name);
  if (queued) {
    return { kind: "waiting", text: `Waiting to export (${ordinal(queued.position)} in queue)` };
  }
  if (queue?.rerun?.some((r) => r.module_id === moduleId && r.session === name)) {
    return { kind: "waiting", text: "Waiting to export newly finished files" };
  }
  if ((session.export_failed_modules || []).includes(moduleId)) {
    return { kind: "failed", text: "Export failed after retries; files are still on the module" };
  }
  const state = (session.module_export_states || {})[moduleId];
  if (state === "complete") return { kind: "done", text: "On the share" };
  if (state === "failed") return { kind: "waiting", text: "Export failed; retrying" };
  if (state === "pending") return { kind: "waiting", text: "Waiting to export" };
  return null;
}

export default function SessionExportStatus({ session, queue, moduleNames = {} }) {
  const rows = session.modules
    .map((id) => ({ id, row: moduleRow(id, session, queue) }))
    .filter((r) => r.row);
  const pending = session.pending_exports ?? 0;
  const busy = rows.filter((r) => r.row.kind === "exporting" || r.row.kind === "waiting").length;
  const failed = rows.some((r) => r.row.kind === "failed");
  const done = rows.filter((r) => r.row.kind === "done").length;
  const stalled = session.export_stall_alerted && busy === 0 && pending > 0;
  const allDone = pending === 0 && !busy && (session.total_exports_complete ?? 0) > 0;

  if (!rows.length && !pending) return null;

  let headline;
  let tone = "info";
  if (failed) {
    headline = "Some files could not be exported";
    tone = "problem";
  } else if (stalled) {
    headline = `Exports stalled: ${pending} not confirmed on the share. `
      + "Check the modules are online, then use Retry Export.";
    tone = "problem";
  } else if (allDone) {
    headline = "All files are on the share";
    tone = "done";
  } else {
    headline = `Copying files to the share: ${done} of ${session.modules.length} `
      + `module${session.modules.length !== 1 ? "s" : ""} done`;
  }

  return (
    <div className={`session-export session-export--${tone}`}>
      <div className="session-export__headline">
        {tone === "done" ? "✓ " : ""}{headline}
      </div>
      {!allDone && rows.length > 0 && (
        <ul className="session-export__rows">
          {rows.map(({ id, row }) => (
            <li key={id} className={`session-export__row session-export__row--${row.kind}`}>
              <span className="session-export__module">{moduleNames[id] || id}</span>
              <span className="session-export__text">
                {row.kind === "done" ? "✓ " : ""}{row.text}
              </span>
              {row.kind === "exporting" && (
                <span
                  className={`session-export__bar${row.frac == null ? " session-export__bar--busy" : ""}`}
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={row.frac == null ? undefined : Math.round(row.frac * 100)}
                >
                  <span style={{ width: row.frac == null ? undefined : `${row.frac * 100}%` }} />
                </span>
              )}
              {row.detail && <span className="session-export__file" title={row.detail}>{row.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
