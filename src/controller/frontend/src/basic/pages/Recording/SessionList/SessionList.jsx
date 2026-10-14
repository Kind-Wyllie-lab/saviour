import React, { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router";
import socket from "/src/socket";
import usePersistedState from "/src/hooks/usePersistedState";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import {
  formatFaultTime, formatScheduledDays, formatTarget, formatRecordingMode,
  formatBytes, triggerDownload, groupSessionsByDate, dateGroupLabel, localDateKey,
  UPCOMING_GROUP,
} from "../sessionFormat";
import { SYNC_LABEL, SYNC_TITLE, worstSummary } from "../syncFormat";
import { Countdown, Elapsed } from "../sessionFormatComponents";
import "./SessionList.css";

// Ask before starting a single zip bigger than this.
const CONFIRM_DOWNLOAD_BYTES = 5 * 1024 ** 3;
const SIZE_DEBOUNCE_MS = 300;

// "Ended" = stopped, or errored-and-not-scheduled-to-retry -- explicitly
// not pending/active/scheduled. A PENDING session hasn't ended, it just
// hasn't started yet; selecting must never sweep those up.
const isEnded = (s) =>
  s.state !== "pending" && s.state !== "active" && s.state !== "error" && s.state !== "scheduled";

// Why a session can't be ticked, or null if it can: only finished ones.
function notSelectableReason(s) {
  return isEnded(s) ? null : "Only finished sessions can be selected";
}

// Why a ticked session is left out of a download, or null if it isn't: a
// zip of a session still exporting would silently miss files.
function notDownloadableReason(s) {
  if ((s.pending_exports ?? 0) > 0) return "Still exporting -- wait for its files to reach the share";
  return null;
}

// A narrow, always-visible selector rail (RecordingLayout renders it next
// to the routed detail pane) -- rows are compact on purpose, since this
// column stays this width regardless of viewport size. All per-session
// actions (Stop/Start Now/Retry Now/Delete/Retry Export/Add Module) live on
// SessionDetailPage, reached by clicking a row. Bulk actions live behind
// Select (top of the list, so a long list needs no scrolling): it shows
// checkboxes per session, per day and for everything, and a bar to
// download the ticked sessions as one zip or delete them.
function SessionList({ sessionList, modules = [], onNewSession, selectedSessionName }) {
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const [selectMode, setSelectMode] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [clearAllWarning, setClearAllWarning] = useState(null); // { message, skippedSessions } | null
  // Per-day open/closed overrides; a day with no entry uses the default
  // (today and Upcoming open, older days collapsed).
  const [openOverrides, setOpenOverrides] = usePersistedState("session_list_open_days", {});
  const [selected, setSelected] = useState(() => new Set());
  const [sizes, setSizes] = useState({}); // session_name -> bytes
  const sizeTimer = useRef(null);

  useEffect(() => {
    const handler = (data) => {
      if (!data.export_warning || !data.skipped_sessions) return;
      // Bulk delete was partly refused — offer a force follow-up.
      // (A single-session delete refusal is handled on SessionDetailPage now.)
      setClearAllWarning({ message: data.error, skippedSessions: data.skipped_sessions });
    };
    socket.on("session_error", handler);
    return () => socket.off("session_error", handler);
  }, []);

  useEffect(() => {
    const onSizes = (d) => setSizes((prev) => ({ ...prev, ...(d?.sizes || {}) }));
    socket.on("sessions_size_response", onSizes);
    return () => socket.off("sessions_size_response", onSizes);
  }, []);

  const handleForceClearAllConfirm = () => {
    socket.emit("delete_sessions", {
      names: clearAllWarning.skippedSessions, delete_files: true, force: true });
    setClearAllWarning(null);
  };

  // sessionList is keyed by session_name in backend insertion order (new
  // sessions are only ever appended, per RecordingLayout's own auto-select
  // effect) -- reverse so the rail reads newest-first, matching how an
  // operator actually wants to scan it (most likely to click a session
  // just created, not one from hours/days ago).
  const sessions = Object.values(sessionList).slice().reverse();
  const endedSessions = sessions.filter(isEnded);
  const nAllTicked = endedSessions.filter((s) => selected.has(s.session_name)).length;
  const allTicked = endedSessions.length > 0 && nAllTicked === endedSessions.length;
  const groups = groupSessionsByDate(sessions);
  const today = localDateKey();

  const byName = useMemo(
    () => Object.fromEntries(Object.values(sessionList).map((s) => [s.session_name, s])),
    [sessionList],
  );

  // Drop ticks for sessions that were deleted or are no longer selectable
  // (e.g. a scheduled session that started again).
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set([...prev].filter((n) => byName[n] && !notSelectableReason(byName[n])));
      return next.size === prev.size ? prev : next;
    });
  }, [byName]);

  // Fetch sizes for newly ticked sessions (debounced) for the selection bar.
  const selectedKey = [...selected].sort().join(",");
  useEffect(() => {
    const missing = [...selected].filter((n) => sizes[n] === undefined);
    if (!missing.length) return undefined;
    clearTimeout(sizeTimer.current);
    sizeTimer.current = setTimeout(
      () => socket.emit("get_sessions_size", { names: missing }), SIZE_DEBOUNCE_MS);
    return () => clearTimeout(sizeTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);

  const selectedGroupKey = selectedSessionName && byName[selectedSessionName]
    ? groups.find((g) => g.sessions.some((s) => s.session_name === selectedSessionName))?.key
    : null;
  const isOpen = (key) =>
    key === selectedGroupKey
    || (openOverrides[key] ?? (key === today || key === UPCOMING_GROUP));
  const toggleOpen = (key) => setOpenOverrides((o) => ({ ...o, [key]: !isOpen(key) }));

  const toggleSession = (name) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });

  const setDaySelected = (daySessions, on) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const s of daySessions) {
        if (notSelectableReason(s)) continue;
        if (on) next.add(s.session_name); else next.delete(s.session_name);
      }
      return next;
    });

  const selectedNames = [...selected];
  const sizeKnown = selectedNames.every((n) => sizes[n] !== undefined);
  const totalBytes = selectedNames.reduce((sum, n) => sum + (sizes[n] || 0), 0);
  const downloadable = selectedNames.filter((n) => byName[n] && !notDownloadableReason(byName[n]));

  const setAllSelected = (on) =>
    setSelected(on ? new Set(endedSessions.map((x) => x.session_name)) : new Set());

  const exitSelect = () => {
    setSelectMode(false);
    setConfirmDelete(false);
    setSelected(new Set());
  };

  const deleteSelected = () => {
    socket.emit("delete_sessions", { names: selectedNames, delete_files: true });
    setConfirmDelete(false);
    setSelected(new Set());
  };

  const downloadSelected = () => {
    if (!downloadable.length) return;
    if (sizeKnown && totalBytes > CONFIRM_DOWNLOAD_BYTES
        && !window.confirm(
          `Download ${downloadable.length} sessions (${formatBytes(totalBytes)}) as one zip?`)) {
      return;
    }
    const names = groups
      .flatMap((g) => g.sessions.map((s) => s.session_name))
      .filter((n) => downloadable.includes(n));
    triggerDownload(
      `/api/sessions/zip?names=${encodeURIComponent(names.join(","))}`,
      `saviour-${names.length}-sessions.zip`,
    );
  };

  const goToSession = (sessionName) => {
    navigate(`/recording/sessions/${encodeURIComponent(sessionName)}`);
  };

  return (
    <div className="session-list card">
      <div className="session-list__header">
        <h2>Sessions</h2>
        {sessions.length > 0 && (
          <span className="session-list__count">{sessions.length}</span>
        )}
        <span className="session-list__header-actions">
          {endedSessions.length > 0 && (
            <button
              type="button"
              className={`session-list__select-btn${selectMode ? " is-active" : ""}`}
              onClick={() => (selectMode ? exitSelect() : setSelectMode(true))}
              title={selectMode ? "Leave select mode" : "Select sessions to download or delete"}
            >
              {selectMode ? "Done" : "Select"}
            </button>
          )}
          {onNewSession && (
            <button
              type="button"
              className="session-list__new-session-btn"
              onClick={onNewSession}
            >
              + New Session
            </button>
          )}
        </span>
      </div>

      {selectMode && (
        <div className="session-list__selection-bar">
          <label className="session-list__select-all">
            <input
              type="checkbox"
              className="session-day__check"
              checked={allTicked}
              ref={(el) => { if (el) el.indeterminate = nAllTicked > 0 && !allTicked; }}
              onChange={() => setAllSelected(!allTicked)}
              aria-label="Select all finished sessions"
            />
            All
          </label>
          <span className="session-list__selection-summary">
            {selectedNames.length
              ? <>{selectedNames.length} selected · {sizeKnown ? formatBytes(totalBytes) : "sizing…"}</>
              : `tick sessions (${endedSessions.length} finished)`}
          </span>
          {selectedNames.length > 0 && !confirmDelete && (
            <span className="session-list__selection-actions">
              <button
                type="button"
                className="session-list__selection-btn session-list__selection-btn--primary"
                onClick={downloadSelected}
                disabled={!downloadable.length}
                title={downloadable.length < selectedNames.length
                  ? `${selectedNames.length - downloadable.length} still exporting; left out of the zip`
                  : "Download as one zip"}
              >
                Download{downloadable.length < selectedNames.length ? ` (${downloadable.length})` : ""}
              </button>
              <button
                type="button"
                className="session-list__selection-btn session-list__selection-btn--danger"
                onClick={() => setConfirmDelete(true)}
                disabled={!loggedIn}
                title={loggedIn ? "Delete the selected sessions and their files"
                  : "Login required for this action"}
              >
                Delete
              </button>
            </span>
          )}
          {confirmDelete && (
            <span className="session-list__delete-confirm">
              Delete {selectedNames.length} session{selectedNames.length !== 1 ? "s" : ""} and
              their files from the share?
              <button type="button" className="session-btn session-btn--delete-confirm" onClick={deleteSelected}>
                Delete
              </button>
              <button type="button" className="session-btn session-btn--cancel" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </span>
          )}
        </div>
      )}

      {clearAllWarning && (
        <div className="session-list__export-warning">
          <span>
            ⚠ {clearAllWarning.message}
          </span>
          <button
            type="button"
            className="session-btn session-btn--delete-confirm"
            onClick={handleForceClearAllConfirm}
          >
            Force clear {clearAllWarning.skippedSessions.length} anyway
          </button>
          <button
            type="button"
            className="session-btn session-btn--cancel"
            onClick={() => setClearAllWarning(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {sessions.length === 0 ? (
        <p className="session-list__empty">No sessions yet - create one to begin recording.</p>
      ) : (
        groups.map(({ key, sessions: daySessions }) => {
          const open = isOpen(key);
          const selectable = daySessions.filter((s) => !notSelectableReason(s));
          const nTicked = selectable.filter((s) => selected.has(s.session_name)).length;
          const allTicked = selectable.length > 0 && nTicked === selectable.length;
          return (
            <div key={key} className="session-day">
              <div className="session-day__header">
                {/* Same column as the rows' ticks (left), so a day's tick sits
                    directly above the sessions it ticks. */}
                {!selectMode ? null : selectable.length > 0 ? (
                  <input
                    type="checkbox"
                    className="session-day__check"
                    checked={allTicked}
                    ref={(el) => { if (el) el.indeterminate = nTicked > 0 && !allTicked; }}
                    onChange={() => setDaySelected(daySessions, !allTicked)}
                    title={allTicked ? "Untick this day" : `Tick all ${selectable.length} finished sessions this day`}
                    aria-label={`Select all sessions on ${dateGroupLabel(key, today)}`}
                  />
                ) : (
                  <span className="session-day__check session-day__check--none" aria-hidden="true" />
                )}
                <button
                  type="button"
                  className="session-day__toggle"
                  onClick={() => toggleOpen(key)}
                  aria-expanded={open}
                  title={open ? "Collapse" : "Expand"}
                >
                  <span className={`session-day__chevron${open ? " session-day__chevron--open" : ""}`}>▸</span>
                  <span className="session-day__label">{dateGroupLabel(key, today)}</span>
                  <span className="session-day__count">{daySessions.length}</span>
                </button>
              </div>
              {open && daySessions.map((session) => (
                <SessionRow
                  key={session.session_name}
                  session={session}
                  modules={modules}
                  isSelected={session.session_name === selectedSessionName}
                  showTick={selectMode}
                  ticked={selected.has(session.session_name)}
                  notSelectable={notSelectableReason(session)}
                  onToggleTick={() => toggleSession(session.session_name)}
                  onOpen={() => goToSession(session.session_name)}
                />
              ))}
            </div>
          );
        })
      )}

    </div>
  );
}

function SessionRow({
  session, modules, isSelected, showTick, ticked, notSelectable, onToggleTick, onOpen,
}) {
  const state = session.state;
  const isPending   = state === "pending";
  const isActive    = state === "active";
  const isStopped   = state === "stopped";
  const isError     = state === "error";
  const isScheduled = state === "scheduled";

  // A session is "starting" when the controller has created it (active)
  // but no modules have confirmed recording yet.
  const isStarting = isActive && session.modules.length > 0 &&
    !session.modules.some(id => modules.find(m => m.id === id)?.status === "RECORDING");

  const totalComplete = session.total_exports_complete ?? 0;
  const totalFailed   = session.total_exports_failed ?? 0;

  let sessionClass = "session";
  if (isStarting)       sessionClass += " starting";
  else if (isActive)    sessionClass += " active";
  if (isPending)        sessionClass += " pending";
  if (isStopped)        sessionClass += " stopped";
  if (isError)          sessionClass += " error";
  if (isSelected)       sessionClass += " session--selected";

  return (
    <div
      className={sessionClass}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      aria-current={isSelected ? "true" : undefined}
      onKeyDown={(e) => { if (e.key === "Enter") onOpen(); }}
    >
      <div className="session-row">
        {showTick && (
          <span className="session-row__check" onClick={(e) => e.stopPropagation()}>
            <input
              type="checkbox"
              checked={ticked}
              disabled={!!notSelectable}
              onChange={onToggleTick}
              title={notSelectable || "Tick to select"}
              aria-label={`Select ${session.session_name}`}
            />
          </span>
        )}
        <span className="status-dot--wrap">
          {isPending && (
            <span className="status-dot status-dot--pending" title="Pending - not started yet" />
          )}
          {isStarting && (
            <span className="status-dot status-dot--starting" title="Starting - waiting for modules" />
          )}
          {isActive && !isStarting && (
            <span className="status-dot status-dot--recording" title="Recording" />
          )}
          {isError && (
            <span className="status-dot status-dot--error" title={session.error_message} />
          )}
          {isScheduled && (
            <span className="status-dot status-dot--scheduled" title="Scheduled" />
          )}
          {isStopped && (
            <span className="status-dot status-dot--stopped" title="Stopped" />
          )}
        </span>

        <div className="session-row__main">
          <div className="session-header__name">
            <span className="session-name">{session.session_name}</span>
            {isPending   && <span className="session-state-label session-state-label--pending">Pending</span>}
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
            {session.framesync_verdict?.status && (
              <span
                className={`session-state-label session-sync-badge session-sync-badge--${session.framesync_verdict.status}`}
                title={
                  (session.framesync_verdict.reasons || []).slice(0, 3).join(" · ")
                  || worstSummary(session.framesync_verdict)
                  || SYNC_TITLE[session.framesync_verdict.status]
                }
              >
                {SYNC_LABEL[session.framesync_verdict.status]}
              </span>
            )}
          </div>

          <div className="session-row__type">
            {formatTarget(session.target)} · {formatRecordingMode(session)}
          </div>

          <div className="session-row__summary">
            {isPending && <span>Not started yet</span>}
            {isActive && !isStarting && (
              session.timed_stop_at
                ? <span><Countdown timedStopAt={session.timed_stop_at} /> left</span>
                : <Elapsed startTime={session.start_time} />
            )}
            {isError && session.error_message && (
              <span className="session-row__error-text" title={session.error_message}>{session.error_message}</span>
            )}
            {isScheduled && session.scheduled_start_time && (
              <span>{session.scheduled_start_time} – {session.scheduled_end_time}, {formatScheduledDays(session.scheduled_days)}</span>
            )}
            {isStopped && (totalComplete > 0 || totalFailed > 0) && (
              <span>
                {totalComplete} exported
                {totalFailed > 0 && <span className="session-export-failed">, {totalFailed} failed</span>}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default SessionList;
