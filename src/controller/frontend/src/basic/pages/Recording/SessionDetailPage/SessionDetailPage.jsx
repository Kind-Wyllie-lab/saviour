import React, { useState, useEffect, useRef } from "react";
import { useParams, useNavigate, useOutletContext, Link } from "react-router";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import {
  formatFaultTime, formatEpochTime, formatBytes, formatDuration,
  formatScheduledDays, levelClass, DOWNLOAD_ALL_MAX_BYTES,
  DOWNLOAD_CONFIRM_BYTES, triggerDownload,
} from "../sessionFormat";
import { Countdown, CopyButton } from "../sessionFormatComponents";
import FileTree from "./FileTree";
import HabitatSessionPanel from "./HabitatSessionPanel";
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

function DownloadConfirmModal({ name, sizeBytes, onConfirm, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <p>Download <strong>{name}</strong>?</p>
        <p className="modal-subtext">
          This is {formatBytes(sizeBytes)} — it may take a while depending on your connection.
        </p>
        <div className="modal-buttons">
          <button className="save-button" type="button" onClick={onConfirm}>
            Download
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
  const { openCopyDrawer } = useOutletContext();

  const [pendingDelete, setPendingDelete] = useState(false);
  const [addModuleTarget, setAddModuleTarget] = useState(false);
  const [shareInfo, setShareInfo] = useState(null);
  const [pendingForceDelete, setPendingForceDelete] = useState(false);
  const [deleteWarning, setDeleteWarning] = useState(null);
  // Both default to open (see the auto-fetch effects below) -- there's
  // room for this content on the detail page, unlike the old
  // inline-expanding card, so there's no reason to hide it by default.
  const [sessionLog, setSessionLog] = useState("loading"); // undefined | "loading" | { lines, total, truncated }
  const [fileInfo, setFileInfo] = useState(null); // null | "loading" | { files, total_bytes }
  const [fileListOpen, setFileListOpen] = useState(true);
  const [shareNoticeOpen, setShareNoticeOpen] = useState(false);
  const [moduleRecordingStates, setModuleRecordingStates] = useState({}); // module_id → { summary, last_reported }
  const [forceStartError, setForceStartError] = useState(null);
  const [editingPending, setEditingPending] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDurationHours, setEditDurationHours] = useState("0");
  const [editDurationMinutes, setEditDurationMinutes] = useState("0");
  const [editDurationSeconds, setEditDurationSeconds] = useState("0");
  const [editError, setEditError] = useState(null);
  const [pendingDownload, setPendingDownload] = useState(null); // null | { url, name, sizeBytes }
  const [diagState, setDiagState] = useState(null); // null | "collecting"

  const requestDownload = (url, name, sizeBytes) => {
    if (sizeBytes >= DOWNLOAD_CONFIRM_BYTES) {
      setPendingDownload({ url, name, sizeBytes });
    } else {
      triggerDownload(url, name);
    }
  };
  const confirmPendingDownload = () => {
    if (pendingDownload) triggerDownload(pendingDownload.url, pendingDownload.name);
    setPendingDownload(null);
  };

  const session = sessions[sessionName];

  useEffect(() => {
    // get_export_destination, not get_controller_samba_info -- the latter
    // always reports the controller's own address (a Settings-page preset)
    // regardless of whether an external NAS override is configured, which
    // is the common case for a habitat deployment. This is where files
    // actually go.
    socket.emit("get_export_destination");
    const handler = (data) => setShareInfo(data);
    socket.on("export_destination_response", handler);
    return () => socket.off("export_destination_response", handler);
  }, []);

  useEffect(() => {
    const onReady = ({ token, filename, error }) => {
      setDiagState(null);
      if (error) { window.alert(`Diagnostics failed: ${error}`); return; }
      const a = document.createElement("a");
      a.href = `/api/bug_report/${token}`;
      a.download = filename;
      a.click();
    };
    socket.on("session_diagnostics_ready", onReady);
    return () => socket.off("session_diagnostics_ready", onReady);
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

  // update_pending_session shares the generic session_error event with
  // several other actions on this page -- only treat it as an edit-panel
  // error while the edit panel is actually open, so an unrelated error
  // (e.g. the delete export-warning above) can't get misattributed to it.
  useEffect(() => {
    const handler = (data) => {
      if (data.export_warning || !editingPending) return;
      setEditError(data.error);
    };
    socket.on("session_error", handler);
    return () => socket.off("session_error", handler);
  }, [editingPending]);

  useEffect(() => {
    const handler = (data) => {
      if (!data.success) return;
      setEditingPending(false);
      setEditError(null);
      if (data.session_name !== sessionName) {
        navigate(`/recording/sessions/${encodeURIComponent(data.session_name)}`, { replace: true });
      }
    };
    socket.on("update_pending_session_result", handler);
    return () => socket.off("update_pending_session_result", handler);
  }, [sessionName, navigate]);

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

  // Files and events both default to visible on the detail page -- there's
  // room for them here, unlike the old inline-expanding list card, so
  // there's no reason to hide them behind an extra click by default. The
  // toggles below still let an operator collapse either one if they want.
  // Re-fetches on every state transition (pending -> active on Start,
  // active -> stopped on Stop, -> error on a fault, etc.), not just once on
  // mount -- otherwise an operator sitting on this page watching it happen
  // never sees the new log line for an action they just took (e.g. "Session
  // started" after pressing Start Recording) without manually toggling the
  // section closed and open again.
  const sessionState = session?.state;

  useEffect(() => {
    setSessionLog("loading");
    socket.emit("get_session_log", { session_name: sessionName });
  }, [sessionName, sessionState]);

  // Populate the per-module recording-pipeline table right away on mount /
  // session change / state transition, instead of waiting up to 5 min for
  // the next server-side poll (the reason it showed "-" / "never" for every
  // module until the operator clicked Refresh). Same request that button sends.
  useEffect(() => {
    setModuleRecordingStates({});
    if (sessionState && sessionState !== "stopped" && sessionState !== "pending") {
      socket.emit("request_recording_state_refresh", { session_name: sessionName });
    }
  }, [sessionName, sessionState]);

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
  const isPending    = state === "pending";
  const isActive     = state === "active";
  const isPaused     = state === "paused";
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
  const startEditingPending = () => {
    setEditName(session.session_name);
    // duration_minutes may carry a fractional/sub-minute part (e.g. 12.25
    // == 12m15s) -- convert through whole seconds so it round-trips exactly.
    const totalSeconds = Math.round((session.duration_minutes || 0) * 60);
    setEditDurationHours(String(Math.floor(totalSeconds / 3600)));
    setEditDurationMinutes(String(Math.floor((totalSeconds % 3600) / 60)));
    setEditDurationSeconds(String(totalSeconds % 60));
    setEditError(null);
    setEditingPending(true);
  };
  const handleCancelEdit = () => {
    setEditingPending(false);
    setEditError(null);
  };
  const handleSaveEdit = () => {
    const totalMinutes = parseInt(editDurationHours || 0, 10) * 60
      + parseInt(editDurationMinutes || 0, 10)
      + parseInt(editDurationSeconds || 0, 10) / 60;
    setEditError(null);
    socket.emit("update_pending_session", {
      session_name: sessionName,
      new_session_name: editName,
      duration_minutes: totalMinutes > 0 ? totalMinutes : null,
    });
  };
  const handleAddModuleConfirm = (name, moduleId) => socket.emit("add_module_to_session", { session_name: name, module_id: moduleId });
  const handleRetryExport = () => socket.emit("retry_failed_exports", { session_name: sessionName });
  const handleExportDiagnostics = () => {
    setDiagState("collecting");
    socket.emit("get_session_diagnostics", { session_name: sessionName });
  };
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
          {isPending   && <span className="session-state-label session-state-label--pending">Pending</span>}
          {isPending && !editingPending && (
            <button
              type="button"
              className="session-edit-toggle"
              onClick={startEditingPending}
              title="Edit name or duration"
            >
              ✎ Edit
            </button>
          )}
          {isStarting  && <span className="session-state-label session-state-label--starting">Starting…</span>}
          {isActive && !isStarting && <span className="session-state-label session-state-label--recording">Recording</span>}
          {isActive && !isStarting && session.error_time && (
            <span className="session-state-label session-state-label--past-fault">
              fault {formatFaultTime(session.error_time)}
            </span>
          )}
          {isPaused    && <span className="session-state-label session-state-label--scheduled">Paused</span>}
          {isStopped   && <span className="session-state-label session-state-label--stopped">Stopped</span>}
          {isScheduled && <span className="session-state-label session-state-label--scheduled">Scheduled</span>}
          {isError     && <span className="session-state-label session-state-label--error">Error</span>}
        </div>

        <HabitatSessionPanel session={session} />

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

          {isPending && editingPending && (
            <div className="session-edit-panel">
              <div className="session-edit-panel__row">
                <label htmlFor="edit-session-name">Name</label>
                <input
                  id="edit-session-name"
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </div>
              <div className="session-edit-panel__row">
                <label>Duration</label>
                <div className="session-edit-panel__duration">
                  <input
                    type="number"
                    min="0"
                    max="99"
                    value={editDurationHours}
                    onChange={(e) => setEditDurationHours(e.target.value)}
                    className="session-edit-panel__duration-input"
                  />
                  <span>h</span>
                  <input
                    type="number"
                    min="0"
                    max="59"
                    value={editDurationMinutes}
                    onChange={(e) => setEditDurationMinutes(e.target.value)}
                    className="session-edit-panel__duration-input"
                  />
                  <span>m</span>
                  <input
                    type="number"
                    min="0"
                    max="59"
                    value={editDurationSeconds}
                    onChange={(e) => setEditDurationSeconds(e.target.value)}
                    className="session-edit-panel__duration-input"
                  />
                  <span>s</span>
                  <span className="session-edit-panel__hint">(0 = no time limit)</span>
                </div>
              </div>
              {editError && <p className="session-error-message">{editError}</p>}
              <div className="session-edit-panel__actions">
                <button type="button" className="session-btn session-btn--start" onClick={handleSaveEdit}>
                  Save
                </button>
                <button type="button" className="session-btn session-btn--cancel" onClick={handleCancelEdit}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {isPending && !editingPending && (
            <p className="session-info-text">
              Created — modules assigned, nothing recording yet. Press <strong>Start Recording</strong>{" "}
              below when ready, or Discard to cancel.
            </p>
          )}

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
                        onClick={(e) => {
                          e.preventDefault();
                          requestDownload(
                            `/api/sessions/${session.session_name}/download`,
                            `${session.session_name}.zip`,
                            fileInfo.total_bytes,
                          );
                        }}
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
              {totalComplete} export{totalComplete !== 1 ? "s" : ""} completed
              {totalFailed > 0 && <span className="session-export-failed">, {totalFailed} failed</span>}
              {(isActive || isError) && activeSegment && (
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

          {isStopped && shareInfo?.share_ip && (() => {
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
                  <FileTree
                    files={fileInfo.files}
                    sessionName={session.session_name}
                    onRequestDownload={requestDownload}
                  />
                )}
              </div>
            );
          })()}

          {isStopped && fileInfo && fileInfo !== "loading" && fileInfo.files.length > 0 && (
            <Link
              className="session-postprocess-link"
              to={`/post-process?session=${encodeURIComponent(session.session_name)}`}
            >
              Post-process this session - compose video, aligned audio, ephys
            </Link>
          )}

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
                  <span>Recording</span>
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
                  // "Module status" used to only show export-pipeline
                  // progress, nothing about whether the module is actually
                  // recording right now -- this column is that, so the
                  // section title isn't overselling what it shows.
                  const recDotClass = moduleInfo?.online === false
                    ? "status-dot--stopped"
                    : moduleInfo?.status === "RECORDING"
                      ? "status-dot--recording"
                      : "status-dot--error";
                  const recLabel = moduleInfo?.online === false
                    ? "Offline"
                    : moduleInfo?.status === "RECORDING"
                      ? "Recording"
                      : (moduleInfo?.status || "Unknown");
                  return (
                    <div key={moduleId} className="session-recording-state-row">
                      <span title={moduleId}>{label}</span>
                      <span className="session-recording-state-status">
                        <span className={`status-dot ${recDotClass}`} title={recLabel} />
                        {recLabel}
                      </span>
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
            {isPending && !editingPending && (
              <button
                className="session-btn session-btn--start"
                onClick={handleForceStart}
                title="Begin recording now"
              >
                Start Recording
              </button>
            )}
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
            <button
              className="session-btn session-btn--copy"
              onClick={handleExportDiagnostics}
              disabled={diagState === "collecting"}
              title="Zip this session's logs -- controller journal since it started, each of its modules' current logs, session_events.log / metadata / per-module stop journals"
            >
              {diagState === "collecting" ? "Collecting…" : "Export Diagnostics"}
            </button>
            <button
              className="session-btn session-btn--copy"
              onClick={() => openCopyDrawer(session)}
              title="Create a new session with the same target and mode (immediate/timed/scheduled)"
            >
              Copy
            </button>
            {(isStopped || (isPending && !editingPending)) && (
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
                  {/* A pending session never recorded anything -- no files
                      to warn about, so the wording (and the action's name)
                      differ from a real, already-recorded session's delete. */}
                  <span>{isPending ? "Discard this session?" : "Delete session and all files?"}</span>
                  <button className="session-btn session-btn--delete-confirm" onClick={handleDeleteConfirm}>
                    {isPending ? "Yes, discard" : "Yes, delete"}
                  </button>
                  <button className="session-btn session-btn--cancel" onClick={() => setPendingDelete(false)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button className="session-btn session-btn--delete" onClick={() => setPendingDelete(true)}>
                  {isPending ? "Discard" : "Delete"}
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

      {pendingDownload && (
        <DownloadConfirmModal
          name={pendingDownload.name}
          sizeBytes={pendingDownload.sizeBytes}
          onConfirm={confirmPendingDownload}
          onClose={() => setPendingDownload(null)}
        />
      )}
    </div>
  );
}
