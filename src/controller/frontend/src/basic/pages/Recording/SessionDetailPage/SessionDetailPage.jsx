import React, { useState, useEffect, useRef } from "react";
import { useParams, useNavigate, Link } from "react-router";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import {
  formatFaultTime, formatEpochTime, formatBytes, formatDuration,
  formatScheduledDays, levelClass, DOWNLOAD_ALL_MAX_BYTES,
} from "../sessionFormat";
import { Countdown, CopyButton } from "../sessionFormatComponents";
import "../SessionList/SessionList.css";
import "./SessionDetailPage.css";

function AddModuleModal({ sessionName, candidates, onConfirm, onClose }) {
  const [selectedId, setSelectedId] = useState("");
  const selectRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    selectRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleConfirm = () => {
    if (!selectedId) return;
    onConfirm(sessionName, selectedId);
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <p>Add a module to <strong>{sessionName}</strong></p>
        <p className="modal-subtext">
          The selected module will begin recording immediately and join the existing session.
        </p>
        <div className="form-field" style={{ marginBottom: "1rem" }}>
          <label>Module:</label>
          <select
            ref={selectRef}
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
          >
            <option value="">- select a module -</option>
            {candidates.map(m => (
              <option key={m.id} value={m.id}>{m.name || m.id}</option>
            ))}
          </select>
        </div>
        <div className="modal-buttons">
          <button
            className="save-button"
            type="button"
            disabled={!selectedId}
            onClick={handleConfirm}
          >
            Add to Session
          </button>
          <button className="save-button" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SessionDetailPage() {
  const { sessionName } = useParams();
  const navigate = useNavigate();
  const { sessions } = useSessions();
  const { moduleList: modules } = useModules();

  const [pendingDelete, setPendingDelete] = useState(false);
  const [addModuleTarget, setAddModuleTarget] = useState(false);
  const [shareInfo, setShareInfo] = useState(null);
  const [pendingForceDelete, setPendingForceDelete] = useState(false);
  const [deleteWarning, setDeleteWarning] = useState(null);
  const [sessionLog, setSessionLog] = useState(undefined); // undefined | "loading" | { lines, total, truncated }
  const [fileInfo, setFileInfo] = useState(null); // null | "loading" | { files, total_bytes }
  const [fileListOpen, setFileListOpen] = useState(false);
  const [shareNoticeOpen, setShareNoticeOpen] = useState(false);
  const [moduleRecordingStates, setModuleRecordingStates] = useState({}); // module_id → { summary, last_reported }
  const [forceStartError, setForceStartError] = useState(null);

  const session = sessions[sessionName];

  useEffect(() => {
    socket.emit("get_controller_samba_info");
    const handler = (data) => setShareInfo(data);
    socket.on("controller_samba_info_response", handler);
    return () => socket.off("controller_samba_info_response", handler);
  }, []);

  useEffect(() => {
    const handler = ({ session_name, success, error }) => {
      if (session_name !== sessionName || success || !error) return;
      setForceStartError(error);
      const t = setTimeout(() => setForceStartError(null), 8000);
      return () => clearTimeout(t);
    };
    socket.on("force_start_result", handler);
    return () => socket.off("force_start_result", handler);
  }, [sessionName]);

  useEffect(() => {
    const handler = (data) => {
      if (!data.export_warning || data.session_name !== sessionName) return;
      setDeleteWarning(data.error);
      setPendingForceDelete(true);
    };
    socket.on("session_error", handler);
    return () => socket.off("session_error", handler);
  }, [sessionName]);

  useEffect(() => {
    const handler = (data) => {
      if (data.session_name !== sessionName) return;
      setSessionLog({ lines: data.lines ?? [], total: data.total ?? 0, truncated: !!data.truncated });
    };
    socket.on("session_log_response", handler);
    return () => socket.off("session_log_response", handler);
  }, [sessionName]);

  useEffect(() => {
    const handler = (data) => {
      if (data.session_name !== sessionName) return;
      setFileInfo(data);
    };
    socket.on("session_file_info_response", handler);
    return () => socket.off("session_file_info_response", handler);
  }, [sessionName]);

  useEffect(() => {
    const handler = ({ module_id, summary, last_reported }) => {
      setModuleRecordingStates(prev => ({ ...prev, [module_id]: { summary, last_reported } }));
    };
    socket.on("module_recording_state_update", handler);
    return () => socket.off("module_recording_state_update", handler);
  }, []);

  // Fetch share-side file info once, on mount for this session.
  useEffect(() => {
    setFileInfo("loading");
    socket.emit("get_session_file_info", { session_name: sessionName });
  }, [sessionName]);

  if (!session) {
    return (
      <div className="session-detail-page">
        <Link to="/recording" className="session-detail-page__back">‹ Back to Sessions</Link>
        <p className="session-list__empty">
          Session "{sessionName}" not found — it may have been deleted.
        </p>
      </div>
    );
  }

  const state = session.state;
  const isActive    = state === "active";
  const isStopped    = state === "stopped";
  const isError      = state === "error";
  const isScheduled  = state === "scheduled";

  const isStarting = isActive && session.modules.length > 0 &&
    !session.modules.some(id => modules.find(m => m.id === id)?.status === "RECORDING");

  const exportStates    = session.module_export_states || {};
  const exportEntries   = Object.entries(exportStates);
  const pendingExports   = exportEntries.filter(([, s]) => s === "pending").length;
  const completeExports  = exportEntries.filter(([, s]) => s === "complete").length;
  const totalComplete    = session.total_exports_complete ?? 0;
  const totalFailed      = session.total_exports_failed ?? 0;
  const activeSegment    = pendingExports > 0 || completeExports > 0;

  const stopStates    = session.module_stop_states || {};
  const stillStopping = Object.values(stopStates).filter(s => s === "stopping").length;

  const allBusyModuleIds = new Set(
    Object.values(sessions)
      .filter(s => s.state === "active" || s.state === "error")
      .flatMap(s => s.modules)
  );
  const candidates = modules.filter(m => !allBusyModuleIds.has(m.id));

  const handleStop = () => socket.emit("stop_session", { session_name: sessionName });
  const handleForceStart = () => socket.emit("force_start_session", { session_name: sessionName });
  const handleAddModuleConfirm = (name, moduleId) => socket.emit("add_module_to_session", { session_name: name, module_id: moduleId });
  const handleRetryExport = () => socket.emit("retry_failed_exports", { session_name: sessionName });
  const handleRefreshRecordingState = () => socket.emit("request_recording_state_refresh", { session_name: sessionName });
  const toggleSessionLog = () => {
    if (sessionLog !== undefined) {
      setSessionLog(undefined);
    } else {
      setSessionLog("loading");
      socket.emit("get_session_log", { session_name: sessionName });
    }
  };
  const handleDeleteConfirm = () => {
    socket.emit("delete_session", { session_name: sessionName, delete_files: true });
    setPendingDelete(false);
    navigate("/recording");
  };
  const handleForceDeleteConfirm = () => {
    socket.emit("delete_session", { session_name: sessionName, delete_files: true, force: true });
    setPendingForceDelete(false);
    setDeleteWarning(null);
    navigate("/recording");
  };

  return (
    <div className="session-detail-page">
      <Link to="/recording" className="session-detail-page__back">‹ Back to Sessions</Link>

      <div className="session-detail-page__body card">
        <div className="session-header__name session-detail-page__title">
          <span className="session-name">{session.session_name}</span>
          {isStarting  && <span className="session-state-label session-state-label--starting">Starting…</span>}
          {isActive && !isStarting && <span className="session-state-label session-state-label--recording">Recording</span>}
          {isActive && !isStarting && session.error_time && (
            <span className="session-state-label session-state-label--past-fault">
              fault {formatFaultTime(session.error_time)}
            </span>
          )}
          {isStopped   && <span className="session-state-label session-state-label--stopped">Stopped</span>}
          {isScheduled && <span className="session-state-label session-state-label--scheduled">Scheduled</span>}
          {isError     && <span className="session-state-label session-state-label--error">Error</span>}
        </div>

        <div className="session-details">
          <div className="session-meta-grid">
            <span className="session-meta-label">Target</span>
            <span>{session.target}</span>

            <span className="session-meta-label">Modules</span>
            <span>{session.modules.join(", ")}</span>

            <span className="session-meta-label">Start</span>
            <span>{session.start_time || "-"}</span>

            {session.end_time && (
              <>
                <span className="session-meta-label">End</span>
                <span>{session.end_time}</span>
              </>
            )}

            {session.timed_stop_at && session.duration_minutes && (
              <>
                <span className="session-meta-label">Duration</span>
                <span>{formatDuration(session.duration_minutes)}</span>
                {(isActive || isError) && (
                  <>
                    <span className="session-meta-label">Remaining</span>
                    <span><Countdown timedStopAt={session.timed_stop_at} /></span>
                  </>
                )}
              </>
            )}

            {isScheduled && session.scheduled_start_time && (
              <>
                <span className="session-meta-label">Schedule</span>
                <span>
                  {session.scheduled_start_time} – {session.scheduled_end_time}
                  {", "}
                  {formatScheduledDays(session.scheduled_days)}
                </span>
              </>
            )}
          </div>

          {isError && (
            <p className="session-error-message">{session.error_message}</p>
          )}

          {isActive && session.error_time && (
            <p className="session-past-fault-message">
              Fault recorded at {session.error_time.replace(
                /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/,
                "$4:$5"
              )}{session.error_message ? ` - ${session.error_message}` : ""}
            </p>
          )}

          {(() => {
            const loading = fileInfo === "loading";
            const ready = fileInfo && fileInfo !== "loading";
            const sizeLabel = loading
              ? "loading…"
              : ready
                ? `${fileInfo.files.length} file${fileInfo.files.length !== 1 ? "s" : ""}, ${formatBytes(fileInfo.total_bytes)}`
                : null;
            return sizeLabel ? (
              <div className="session-meta-grid">
                <span className="session-meta-label">Files</span>
                <span className="session-files-inline">
                  <span>{sizeLabel}</span>
                  {ready && fileInfo.files.length > 0 && (
                    fileInfo.total_bytes > DOWNLOAD_ALL_MAX_BYTES ? (
                      <span
                        className="session-file-dl session-file-dl--all session-file-dl--disabled"
                        title={`Too large to download via browser (${formatBytes(fileInfo.total_bytes)}) - use the NAS share below`}
                      >
                        Download all
                      </span>
                    ) : (
                      <a
                        className="session-file-dl session-file-dl--all"
                        href={`/api/sessions/${session.session_name}/download`}
                        download={`${session.session_name}.zip`}
                      >
                        Download all
                      </a>
                    )
                  )}
                </span>
              </div>
            ) : null;
          })()}

          {isActive && session.ptp_warning && (
            <p className="session-warning-text">{session.ptp_warning}</p>
          )}

          {isActive && session.recording_health_warning && (
            <p className="session-warning-text">{session.recording_health_warning}</p>
          )}

          {stillStopping > 0 && (
            <p className="session-info-text">
              Waiting for {stillStopping} module{stillStopping !== 1 ? "s" : ""} to stop…
            </p>
          )}

          {(exportEntries.length > 0 || totalComplete > 0) && (
            <p className="session-info-text">
              <strong>Exports:</strong>{" "}
              {totalComplete} file{totalComplete !== 1 ? "s" : ""} exported
              {totalFailed > 0 && <span className="session-export-failed">, {totalFailed} failed</span>}
              {activeSegment && (
                <span className="session-export-progress">
                  {" "}· {completeExports}/{exportEntries.length} this segment
                  {pendingExports > 0 && `, ${pendingExports} pending`}
                </span>
              )}
            </p>
          )}

          {isStopped && (session.pending_exports ?? 0) > 0 && (
            <p className="session-warning-text">
              ⏳ {session.pending_exports} export{session.pending_exports !== 1 ? "s" : ""} not yet confirmed on the share
            </p>
          )}

          {isStopped && (session.pending_exports ?? 0) === 0 && totalComplete > 0 && (
            <p className="session-info-text">
              ✓ All exports confirmed on the share
            </p>
          )}

          {isStopped && shareInfo && (() => {
            if (!fileInfo || fileInfo === "loading" || fileInfo.files.length === 0) return null;
            const tooLargeToDownload = fileInfo.total_bytes > DOWNLOAD_ALL_MAX_BYTES;
            const isOpen = tooLargeToDownload || shareNoticeOpen;
            return (
              <div className="session-share-notice-wrapper">
                {!tooLargeToDownload && (
                  <button
                    type="button"
                    className="session-log-toggle"
                    onClick={() => setShareNoticeOpen(prev => !prev)}
                  >
                    {isOpen ? "▲ Hide network share details" : "▼ Use the network share instead"}
                  </button>
                )}
                {isOpen && (
                  <div className="session-share-notice">
                    <p className="session-share-notice__instruction">
                      {tooLargeToDownload
                        ? "Too large to download via browser -- use the network share directly:"
                        : "Direct file explorer access, e.g. for scripted/bulk workflows:"}
                    </p>
                    <div className="session-share-notice__path-row">
                      <p className="session-share-notice__path">
                        \\{shareInfo.share_ip}\{shareInfo.share_path}
                      </p>
                      <CopyButton text={`\\\\${shareInfo.share_ip}\\${shareInfo.share_path}`} />
                    </div>
                    <p className="session-share-notice__creds">
                      Need the share login? On the controller, run <code>sudo saviour-config</code>{" "}
                      → "Reset Samba share password" to view or set it.
                    </p>
                    <p className="session-share-notice__warning">
                      ⚠ Always check files are present after a recording, and export them to a safe long-term storage location!
                    </p>
                  </div>
                )}
              </div>
            );
          })()}

          {(() => {
            if (!fileInfo || fileInfo === "loading" || fileInfo.files.length === 0) return null;
            return (
              <div className="session-files-section">
                <button
                  type="button"
                  className="session-log-toggle"
                  onClick={() => setFileListOpen(prev => !prev)}
                >
                  {fileListOpen ? "▲ Hide files" : `▼ Browse files (${fileInfo.files.length})`}
                </button>
                {fileListOpen && (
                  <div className="session-file-list">
                    {fileInfo.files.map((file) => {
                      const encodedPath = file.path.split("/").map(encodeURIComponent).join("/");
                      const url = `/api/sessions/${session.session_name}/download/${encodedPath}`;
                      return (
                        <div key={file.path} className="session-file-row">
                          <span className="session-file-name" title={file.path}>{file.path}</span>
                          <span className="session-file-size">{formatBytes(file.size_bytes)}</span>
                          <a className="session-file-dl" href={url} download={file.name}>Download</a>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })()}

          {(isActive || isError) && session.modules.length > 0 && (
            <div className="session-recording-state-section">
              <div className="session-recording-state-header">
                <span className="session-meta-label">Module status</span>
                <button
                  type="button"
                  className="session-log-toggle"
                  onClick={handleRefreshRecordingState}
                  title="Ask every module in this session to report its local recording state now, instead of waiting for the next automatic poll (every ~5 min)"
                >
                  ↻ Refresh
                </button>
              </div>
              <div className="session-recording-state-table">
                <div className="session-recording-state-row session-recording-state-row--head">
                  <span>Module</span>
                  <span>Pending</span>
                  <span>Staged</span>
                  <span>Exported</span>
                  <span>Last reported</span>
                </div>
                {session.modules.map((moduleId) => {
                  const moduleInfo = modules.find(m => m.id === moduleId);
                  const label = moduleInfo?.name || moduleId;
                  const moduleState = moduleRecordingStates[moduleId];
                  const summary = moduleState?.summary;
                  const cell = (stage) => {
                    const s = summary?.[stage];
                    if (!s || !s.count) return "-";
                    return `${s.count}${s.total_bytes ? ` (${formatBytes(s.total_bytes)})` : ""}`;
                  };
                  return (
                    <div key={moduleId} className="session-recording-state-row">
                      <span title={moduleId}>{label}</span>
                      <span>{cell("pending")}</span>
                      <span>{cell("to_export")}</span>
                      <span>{cell("exported")}</span>
                      <span>{moduleState?.last_reported ? formatEpochTime(moduleState.last_reported) : "never"}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {!isScheduled && (
            <div className="session-log-section">
              <button
                type="button"
                className="session-log-toggle"
                onClick={toggleSessionLog}
              >
                {sessionLog !== undefined ? "▲ Hide events" : "▼ Events"}
              </button>
              {sessionLog !== undefined && (
                <div className="session-log">
                  {sessionLog === "loading" ? (
                    <span className="fault-alert-log-empty">Loading…</span>
                  ) : sessionLog.lines.length === 0 ? (
                    <span className="fault-alert-log-empty">No events recorded</span>
                  ) : (
                    <>
                      {sessionLog.truncated && (
                        <div className="session-log-truncation">
                          {sessionLog.total} events total - showing last 200
                        </div>
                      )}
                      {sessionLog.lines.map((line, i) => (
                        <div key={i} className={`session-log-line ${levelClass(line)}`}>{line}</div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {forceStartError && (
            <p className="session-error-message">{forceStartError}</p>
          )}

          <div className="session-actions">
            {(isActive || isStarting || isError) && (
              <button className="session-btn session-btn--stop" onClick={handleStop}>
                End Session
              </button>
            )}
            {isScheduled && (
              <>
                <button
                  className="session-btn session-btn--start"
                  onClick={handleForceStart}
                  title="Start recording now, bypassing the scheduled time window"
                >
                  Start Now
                </button>
                <button className="session-btn session-btn--stop" onClick={handleStop}>
                  Cancel Schedule
                </button>
              </>
            )}
            {isError && session.scheduled && (
              <button
                className="session-btn session-btn--start"
                onClick={handleForceStart}
                title="Retry this scheduled session now"
              >
                Retry Now
              </button>
            )}
            {(isActive || isStarting || isError) && candidates.length > 0 && (
              <button
                className="session-btn session-btn--join"
                onClick={() => setAddModuleTarget(true)}
              >
                Add Module to Session
              </button>
            )}
            {isStopped && (session.pending_exports ?? 0) > 0 && (
              <button
                className="session-btn session-btn--start"
                onClick={handleRetryExport}
                title="Re-attempt export for this session -- useful if exports failed and gave up before whatever caused it (e.g. bad credentials) was fixed"
              >
                Retry Export
              </button>
            )}
            {isStopped && (
              pendingForceDelete ? (
                <div className="delete-confirm delete-confirm--export-warning">
                  <span>⚠ {deleteWarning}</span>
                  <button className="session-btn session-btn--delete-confirm" onClick={handleForceDeleteConfirm}>
                    Delete anyway
                  </button>
                  <button
                    className="session-btn session-btn--cancel"
                    onClick={() => { setPendingForceDelete(false); setDeleteWarning(null); }}
                  >
                    Cancel
                  </button>
                </div>
              ) : pendingDelete ? (
                <div className="delete-confirm">
                  <span>Delete session and all files?</span>
                  <button className="session-btn session-btn--delete-confirm" onClick={handleDeleteConfirm}>
                    Yes, delete
                  </button>
                  <button className="session-btn session-btn--cancel" onClick={() => setPendingDelete(false)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button className="session-btn session-btn--delete" onClick={() => setPendingDelete(true)}>
                  Delete
                </button>
              )
            )}
          </div>
        </div>
      </div>

      {addModuleTarget && (
        <AddModuleModal
          sessionName={session.session_name}
          candidates={candidates}
          onConfirm={handleAddModuleConfirm}
          onClose={() => setAddModuleTarget(false)}
        />
      )}
    </div>
  );
}
