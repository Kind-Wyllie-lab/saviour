import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import usePersistedState from "/src/hooks/usePersistedState";
import {
  formatBytes, groupSessionsByDate, dateGroupLabel,
} from "../Recording/sessionFormat";
import ComposeJobRow from "../Recording/SessionDetailPage/ComposeJobRow";
import { streamsByDate } from "./composeLayout";
import JobsList from "./JobsList";
import NewJobModal from "./NewJobModal";
import useSessionJobs from "./useSessionJobs";
import "./PostProcess.css";

// Everything that turns a *recorded* session into a derived artefact:
// composed video, NWB files, ephys alignment. The page is the session's
// list of jobs; "New job" opens a modal to pick what to make
// (plans/post-process-page-layout.md). Kept off the Recording page, which
// is for running sessions and browsing raw files.
export default function PostProcess() {
  const { sessions } = useSessions();
  const [params, setParams] = useSearchParams();
  const names = useMemo(
    () => Object.keys(sessions || {}).sort(),
    [sessions],
  );

  // Same day grouping as the session list rail, newest day first.
  const groups = useMemo(
    () => groupSessionsByDate(Object.values(sessions || {}).slice().reverse()),
    [sessions],
  );

  // Remembered so leaving the page (sidebar) and coming back reopens the
  // same session rather than a different one.
  const [remembered, setRemembered] = usePersistedState("postprocess_session", "");
  const [selected, setSelectedRaw] = useState(params.get("session") || remembered || "");
  const setSelected = (name) => {
    setSelectedRaw(name);
    setRemembered(name);
  };
  const [fileInfo, setFileInfo] = useState(null);
  const [modal, setModal] = useState(null);   // { tool, loadJob?, nwbSettings? }
  const [showOthers, setShowOthers] = usePersistedState("postprocess_renders_all_sessions", false);
  const seededRef = useRef(false);
  const jobs = useSessionJobs(selected);

  // Default to the session in the URL (or the one last picked here), else
  // the most recent one -- the controller lists sessions oldest first; the
  // alphabetically last name is usually not the newest.
  useEffect(() => {
    if (seededRef.current || !names.length) return;
    const newest = Object.keys(sessions || {}).pop();
    const want = names.includes(selected) ? selected : newest;
    seededRef.current = true;
    if (want !== selected) setSelected(want);
  }, [names]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setModal(null);
    if (!selected) return undefined;
    setParams((p) => {
      p.set("session", selected);
      return p;
    });
    setFileInfo(null);
    const onInfo = (d) => {
      if (d.session_name === selected) setFileInfo(d);
    };
    socket.on("session_file_info_response", onInfo);
    socket.emit("get_session_file_info", { session_name: selected });
    return () => socket.off("session_file_info_response", onInfo);
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  const session = sessions?.[selected];
  const ephysSession = session?.external_sync === "ephys"
    || (!!session?.ephys && !session?.external_sync);
  const files = useMemo(() => fileInfo?.files || [], [fileInfo]);
  const hasFiles = files.length > 0;
  const hasVideo = useMemo(() => Object.keys(streamsByDate(files)).length > 0, [files]);

  const openTool = (tool, extra = {}) => setModal({ tool, ...extra });
  const closeModal = useCallback(() => setModal(null), []);
  const renderingElsewhere = jobs.otherCompose.filter(
    (j) => j.state === "queued" || j.state === "running");
  const newJobButton = (
    <button className="btn" onClick={() => setModal({ tool: null })} disabled={!hasFiles}>
      + New job
    </button>
  );

  return (
    <div className="post-process">
      <label className="post-process__pick">
        Session
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">— choose a session —</option>
          {groups.map(({ key, sessions: day }) => (
            <optgroup key={key} label={dateGroupLabel(key)}>
              {day.map((s) => (
                <option key={s.session_name} value={s.session_name}>
                  {s.session_name}
                  {s.state ? `  (${s.state})` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {fileInfo && fileInfo !== "loading" && (
          <span className="post-process__size">
            {files.length} files · {formatBytes(fileInfo.total_bytes || 0)}
          </span>
        )}
      </label>

      {!selected && (
        <div className="post-process__empty">Pick a session to start.</div>
      )}

      {selected && fileInfo && !hasFiles && (
        <div className="post-process__empty">
          This session has no exported files yet.
        </div>
      )}

      {selected && hasFiles && (
        <section className="card pp-jobs-card">
          <div className="pp-jobs-card__head">
            <h3>Jobs</h3>
            <span className="pp-jobs-card__hint">
              Videos, NWB files and ephys alignment made from this session.
              Jobs run on the controller; you can leave this page.
            </span>
            {jobs.items.length > 0 && newJobButton}
          </div>

          {ephysSession && jobs.ephys && !jobs.ephys.model && (
            <div className="post-process__note post-process__note--warn">
              Recorded with ephys sync: align the ephys recording first, so videos can
              show the headstage.
              <button type="button" className="btn btn-small" onClick={() => openTool("ephys")}>
                Align ephys
              </button>
            </div>
          )}

          {jobs.items.length > 0 ? (
            <JobsList sessionName={selected} items={jobs.items} onOpen={openTool}
              nwb={jobs.nwb} checking={jobs.nwbRunning?.kind === "inspect"}
              checkError={jobs.checkError} onCheck={jobs.clearCheckError} />
          ) : (
            <div className="pp-empty">
              <p>Nothing has been made from this session yet.</p>
              {newJobButton}
            </div>
          )}

          {renderingElsewhere.map((j) => (
            <div key={j.id} className="compose-panel__elsewhere">
              Rendering <strong>{j.spec.session_name}</strong>:{" "}
              {j.state === "queued" ? "queued" : `${Math.round((j.progress || 0) * 100)}%`}
              <button className="btn btn-small" onClick={() => setSelected(j.spec.session_name)}>
                Show
              </button>
            </div>
          ))}

          {jobs.otherCompose.length > 0 && (
            <>
              <button
                type="button"
                className="post-process__renders-toggle"
                onClick={() => setShowOthers(!showOthers)}
                aria-expanded={showOthers}
              >
                {showOthers ? "▾" : "▸"} Videos from other sessions ({jobs.otherCompose.length})
              </button>
              {showOthers && (
                <div className="compose-panel__jobs">
                  {jobs.otherCompose.map((job) => (
                    <ComposeJobRow
                      key={job.id}
                      job={job}
                      showSession
                      onShowSession={(name) => names.includes(name) && setSelected(name)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      )}

      <NewJobModal
        modal={selected && hasFiles ? modal : null}
        onTool={(tool) => setModal((m) => ({ ...(m || {}), tool, loadJob: undefined, nwbSettings: undefined }))}
        onClose={closeModal}
        sessionName={selected}
        session={session}
        files={files}
        hasVideo={hasVideo}
        jobs={jobs}
        ephysSession={ephysSession}
        onSelectSession={(name) => names.includes(name) && setSelected(name)}
      />
    </div>
  );
}
