import React, { useEffect } from "react";
import { createPortal } from "react-dom";
import socket from "/src/socket";
import { formatUtc } from "/src/basic/utils/time";
import ComposeEditor from "./ComposeEditor";
import EphysAlignPanel from "./EphysAlignPanel";
import NwbExportPanel from "./NwbExportPanel";

const TOOL_TITLES = {
  compose: "Compose video",
  nwb: "NWB export",
  ephys: "Ephys alignment",
};

/**
 * Post-Process "New job": pick what to make (a few friendly tiles, with
 * whether each applies to this session), then that tool's form in the same
 * modal. Starting a render or an export closes it, so the new job shows in
 * the page's list; ephys is several steps (upload, align, accelerometer),
 * so it stays open until closed.
 *
 * `modal`: { tool: null | "compose" | "nwb" | "ephys", loadJob?, nwbSettings? }.
 */
export default function NewJobModal({
  modal, onTool, onClose, sessionName, session, files, hasVideo, jobs, ephysSession,
  onSelectSession,
}) {
  const tool = modal?.tool || null;

  // Starting a job closes the modal (the job appears in the list).
  useEffect(() => {
    if (!modal) return undefined;
    const onCompose = () => { if (tool === "compose") onClose(); };
    const onNwb = (j) => {
      if (tool === "nwb" && j.kind === "export" && j.session_name === sessionName
        && j.state === "running") onClose();
    };
    socket.on("compose_job_accepted", onCompose);
    socket.on("nwb_job", onNwb);
    return () => {
      socket.off("compose_job_accepted", onCompose);
      socket.off("nwb_job", onNwb);
    };
  }, [modal, tool, sessionName, onClose]);

  // Escape closes the chooser (not a tool form, where it may be mid-edit).
  useEffect(() => {
    if (!modal || tool) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modal, tool, onClose]);

  if (!modal) return null;

  const model = jobs.ephys?.model;
  const tiles = {
    compose: {
      blurb: "Cameras side by side, with the sound or spectrograms of any microphone.",
      meta: hasVideo ? "" : "No camera video in this session",
      disabled: !hasVideo,
    },
    nwb: {
      blurb: "The whole session as one standard file for analysis tools and DANDI.",
      meta: !jobs.nwb ? "" : !jobs.nwb.installed ? "Installs the NWB package first"
        : jobs.existing ? `Replaces the file written ${formatUtc(jobs.existing.mtime)}` : "",
    },
    ephys: {
      blurb: "Line up an Open Ephys recording with this session, then its headstage accelerometer.",
      meta: model ? `Aligned · worst residual ${model.residual_max_ms} ms`
        : jobs.ephysUploaded ? "Uploaded, not aligned yet"
          : ephysSession ? "Recorded with ephys sync: upload the Open Ephys folder"
            : "Not recorded with ephys sync",
      badge: ephysSession && !model ? "Do this first: the video can then show the headstage" : "",
      muted: !ephysSession && !jobs.ephysUploaded,
    },
  };
  const order = ephysSession && !model ? ["ephys", "compose", "nwb"] : ["compose", "nwb", "ephys"];

  return createPortal(
    <div className="modal-overlay pp-modal-overlay">
      <div className={`pp-modal${tool === "compose" ? " pp-modal--wide" : ""}`}
        role="dialog" aria-modal="true"
        aria-label={tool ? TOOL_TITLES[tool] : "New job"}>
        <div className="pp-modal__head">
          {tool && (
            <button type="button" className="btn btn-small" onClick={() => onTool(null)}>
              ← Job types
            </button>
          )}
          <h3>{tool ? TOOL_TITLES[tool] : "New job"}</h3>
          <span className="pp-modal__session">{sessionName}</span>
          <button type="button" className="pp-modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="pp-modal__body">
          {!tool && (
            <div className="pp-tiles">
              {order.map((id) => {
                const t = tiles[id];
                return (
                  <button key={id} type="button" disabled={t.disabled}
                    className={`pp-tile${t.muted ? " pp-tile--muted" : ""}`}
                    onClick={() => onTool(id)}>
                    <span className="pp-tile__title">{TOOL_TITLES[id]}</span>
                    <span className="pp-tile__blurb">{t.blurb}</span>
                    {t.badge && <span className="pp-tile__badge">{t.badge}</span>}
                    {t.meta && <span className="pp-tile__meta">{t.meta}</span>}
                  </button>
                );
              })}
            </div>
          )}

          {tool === "compose" && (
            <ComposeEditor
              sessionName={sessionName}
              session={session}
              files={files}
              active
              ephysSession={ephysSession}
              ephysState={model ? "aligned" : jobs.ephysUploaded ? "uploaded" : jobs.ephys ? "none" : undefined}
              onOpenEphys={() => onTool("ephys")}
              loadJob={modal.loadJob}
              onSelectSession={onSelectSession}
            />
          )}
          {tool === "nwb" && (
            <>
              {(ephysSession || jobs.ephysUploaded) && (
                <div className="post-process__note">
                  NWB export doesn&apos;t include the ephys recording or the headstage
                  accelerometer yet: the file holds SAVIOUR&apos;s own streams only.
                </div>
              )}
              <NwbExportPanel sessionName={sessionName} loadSettings={modal.nwbSettings} />
            </>
          )}
          {tool === "ephys" && <EphysAlignPanel sessionName={sessionName} />}
        </div>
      </div>
    </div>,
    document.body,
  );
}
