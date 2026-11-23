import React from "react";
import ComposeJobRow from "../Recording/SessionDetailPage/ComposeJobRow";
import { formatBytes, triggerDownload } from "../Recording/sessionFormat";
import { formatUtc } from "/src/basic/utils/time";

const KIND_LABEL = {
  compose: "Video",
  "nwb-file": "NWB", "nwb-running": "NWB", "nwb-failed": "NWB",
  "ephys-model": "Ephys", "ephys-accel": "Ephys", "ephys-running": "Ephys",
};

/** A job row for the non-compose kinds, styled like ComposeJobRow. */
function SimpleRow({ state, title, meta, progress, error, actions }) {
  const pct = progress != null ? Math.round(progress * 100) : null;
  return (
    <div className={`compose-job compose-job--${state}`}>
      <div className="compose-job__head">
        <span className={`compose-job__state compose-job__state--${state}`}>{title}</span>
        {state === "running" && pct != null && <span className="compose-job__pct">{pct}%</span>}
        <span className="compose-job__actions">{actions}</span>
      </div>
      {meta && <div className="compose-job__meta"><span>{meta}</span></div>}
      {state === "running" && pct != null && (
        <div className="compose-job__bar">
          <div className="compose-job__bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {error && <div className="compose-job__error">{error}</div>}
    </div>
  );
}

/**
 * Post-Process's list of what's been made from one session (useSessionJobs
 * `items`), running first, then newest first. `onOpen(tool, extra)` opens
 * the New job modal on a tool (to see a job's detail, edit a copy, or
 * export again).
 */
export default function JobsList({ sessionName, items, onOpen }) {
  const downloadNwb = () => triggerDownload(
    `/api/sessions/${encodeURIComponent(sessionName)}/download/${encodeURIComponent(`${sessionName}.nwb`)}`,
    `${sessionName}.nwb`);
  const open = (tool, extra) => (
    <button className="btn btn-small" onClick={() => onOpen(tool, extra)}>Open</button>
  );

  return (
    <div className="pp-jobs">
      {items.map((it) => {
        let row;
        switch (it.kind) {
          case "compose":
            row = (
              <ComposeJobRow job={it.job}
                onEditCopy={(job) => onOpen("compose", { loadJob: { job, nonce: Date.now() } })} />
            );
            break;
          case "nwb-file":
            row = (
              <SimpleRow state="done" title={`${sessionName}.nwb`}
                meta={`${formatBytes(it.file.size_bytes)} · written ${formatUtc(it.file.mtime)}`}
                actions={<>
                  <button className="btn btn-small" onClick={downloadNwb}>Download</button>
                  <button className="btn btn-small"
                    title={it.file.settings ? "Open the export form with the settings this file was written with"
                      : "Open the export form"}
                    onClick={() => onOpen("nwb", it.file.settings
                      ? { nwbSettings: { settings: it.file.settings, nonce: Date.now() } } : {})}>
                    Export again
                  </button>
                </>} />
            );
            break;
          case "nwb-running":
            row = (
              <SimpleRow state="running"
                title={it.job.kind === "install" ? "Installing the NWB package" : "Exporting NWB"}
                meta={it.job.stage} progress={it.job.progress || 0}
                actions={open("nwb")} />
            );
            break;
          case "nwb-failed":
            row = (
              <SimpleRow state="error" title="NWB export failed"
                meta={formatUtc(it.job.created_at)} error={it.job.error} actions={open("nwb")} />
            );
            break;
          case "ephys-running":
            row = (
              <SimpleRow state="running" title={it.label}
                meta={it.job.stage || it.job.state} actions={open("ephys")} />
            );
            break;
          case "ephys-model":
            row = (
              <SimpleRow state="done" title="Ephys alignment"
                meta={`worst residual ${it.model.residual_max_ms} ms over ${it.model.edges_used} edges`
                  + (it.t ? ` · ${formatUtc(it.t)}` : "")}
                actions={open("ephys")} />
            );
            break;
          case "ephys-accel":
            row = (
              <SimpleRow state="done" title={`Headstage ${it.port} accelerometer`}
                meta={[
                  it.result.aligned ? "on session time" : "ephys clock only: run alignment",
                  it.result.duration_s ? `${Math.round(it.result.duration_s)} s` : null,
                  it.t ? formatUtc(it.t) : null,
                ].filter(Boolean).join(" · ")}
                actions={<>
                  {it.result.aligned && (
                    <button className="btn btn-small" onClick={() => onOpen("compose")}
                      title="Open Compose video, where the headstage can be added">
                      Add to a video
                    </button>
                  )}
                  {open("ephys")}
                </>} />
            );
            break;
          default:
            row = null;
        }
        return (
          <div key={it.key} className="pp-job">
            <span className={`pp-job__kind pp-job__kind--${KIND_LABEL[it.kind]?.toLowerCase()}`}>
              {KIND_LABEL[it.kind]}
            </span>
            <div className="pp-job__row">{row}</div>
          </div>
        );
      })}
    </div>
  );
}
