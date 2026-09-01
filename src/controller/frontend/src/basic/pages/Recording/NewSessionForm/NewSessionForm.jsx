import { useState, useMemo, useEffect } from "react";
import socket from "/src/socket";
import "./NewSessionForm.css";

import useExperimentTitle from "/src/hooks/useExperimentTitle";
import usePersistedState from "/src/hooks/usePersistedState";
import SessionName from "../SessionName/SessionName";
import TimeSelect from "./TimeSelect/TimeSelect";
import { groupModulesByGroup, resolveTargetModules, isModuleReady } from "../targetModules";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ALL_DAYS = new Set([0, 1, 2, 3, 4, 5, 6]);

const daysSerialize = (days) => JSON.stringify([...days]);
const daysDeserialize = (str) => new Set(JSON.parse(str));

function NewSessionForm({ modules, sessionList = {}, target, setTarget, onSessionCreated, prefill }) {
  const { experimentName, experimenter } = useExperimentTitle();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const sessionNamePreview = useMemo(() => {
    if (!experimentName) return "-";
    const p = (n) => String(n).padStart(2, "0");
    const ts = `${now.getFullYear()}${p(now.getMonth()+1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
    const safe = experimentName.replace(/[^a-zA-Z0-9 \-_]/g, "").trim().replace(/ /g, "_");
    return target !== "all" ? `${safe}-${target}-${ts}` : `${safe}-${ts}`;
  }, [experimentName, target, now]);

  const [recordingMode, setRecordingMode] = usePersistedState("saviour_session_form_mode", "immediate");
  const [unattended, setUnattended] = usePersistedState("saviour_session_form_unattended", false);
  const [ptpSyncStatus, setPtpSyncStatus] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [dataRate, setDataRate] = useState(null);

  useEffect(() => { setPtpSyncStatus(null); }, [target]);
  useEffect(() => {
    socket.on("ptp_sync_status", setPtpSyncStatus);
    return () => socket.off("ptp_sync_status", setPtpSyncStatus);
  }, []);

  // Pre-flight data-rate estimate for the chosen target — how much this
  // session will generate and how long the share holds at that rate.
  useEffect(() => {
    const onEstimate = (d) => setDataRate(d);
    socket.on("data_rate_estimate", onEstimate);
    return () => socket.off("data_rate_estimate", onEstimate);
  }, []);
  useEffect(() => {
    if (target) socket.emit("estimate_data_rate", { target });
  }, [target]);

  useEffect(() => {
    const onError = (data) => {
      setSubmitError(data.error || "Unknown error");
      const t = setTimeout(() => setSubmitError(null), 12000);
      return () => clearTimeout(t);
    };
    socket.on("session_error", onError);
    return () => socket.off("session_error", onError);
  }, []);

  // Sessions are created PENDING now, not auto-started -- this is the
  // direct signal (rather than waiting on sessions_update to eventually
  // reflect it) telling the caller which session to navigate to so the
  // operator lands on the actual "Start Recording" action.
  useEffect(() => {
    if (!onSessionCreated) return;
    const onCreated = (data) => {
      if (data.success) onSessionCreated(data.session_name);
    };
    socket.on("create_session_result", onCreated);
    return () => socket.off("create_session_result", onCreated);
  }, [onSessionCreated]);

  // Request a fresh health snapshot on mount so PTP offset data is available
  // before the user presses Check Ready.
  useEffect(() => {
    socket.emit("send_command", { module_id: "all", type: "get_health", params: {} });
  }, []);

  // Timed mode
  const [durationHours, setDurationHours]     = usePersistedState("saviour_session_form_duration_h", "0");
  const [durationMinutes, setDurationMinutes] = usePersistedState("saviour_session_form_duration_m", "10");
  const [durationSeconds, setDurationSeconds] = usePersistedState("saviour_session_form_duration_s", "0");

  // Scheduled mode
  const [startHour, setStartHour]     = usePersistedState("saviour_session_form_start_h", "19");
  const [startMinute, setStartMinute] = usePersistedState("saviour_session_form_start_m", "00");
  const [endHour, setEndHour]         = usePersistedState("saviour_session_form_end_h", "23");
  const [endMinute, setEndMinute]     = usePersistedState("saviour_session_form_end_m", "00");
  const [scheduledDays, setScheduledDays] = usePersistedState(
    "saviour_session_form_days",
    new Set(ALL_DAYS),
    { serialize: daysSerialize, deserialize: daysDeserialize }
  );

  // One-shot prefill from "Copy Session" (target is applied by the caller
  // via setTarget before opening the drawer, since it's already lifted up
  // to RecordingLayout). `prefill` is a fresh object per Copy click, so
  // this fires exactly once per click rather than on every render -- not a
  // plain initializer because mode/duration/schedule are persisted state,
  // already populated before the operator ever copies a session.
  useEffect(() => {
    if (!prefill) return;
    setRecordingMode(prefill.mode);
    if (prefill.mode === "timed") {
      // prefill.durationMinutes may carry a fractional/sub-minute part
      // (e.g. 12.25 == 12m15s) -- convert through whole seconds so the
      // seconds field round-trips exactly rather than showing "12.25" in
      // the minutes box.
      const totalSeconds = Math.round((prefill.durationMinutes ?? 0) * 60);
      setDurationHours(String(Math.floor(totalSeconds / 3600)));
      setDurationMinutes(String(Math.floor((totalSeconds % 3600) / 60)));
      setDurationSeconds(String(totalSeconds % 60));
    } else if (prefill.mode === "scheduled") {
      const [sh, sm] = (prefill.scheduledStart || "19:00").split(":");
      const [eh, em] = (prefill.scheduledEnd || "23:00").split(":");
      setStartHour(sh);
      setStartMinute(sm);
      setEndHour(eh);
      setEndMinute(em);
      setScheduledDays(new Set(prefill.scheduledDays?.length ? prefill.scheduledDays : ALL_DAYS));
    }
  }, [
    prefill, setRecordingMode, setDurationHours, setDurationMinutes, setDurationSeconds,
    setStartHour, setStartMinute, setEndHour, setEndMinute, setScheduledDays,
  ]);

  // Derive groups from module list
  const groups = useMemo(() => groupModulesByGroup(modules), [modules]);

  const hasGroups = Object.keys(groups).length > 0;

  const targetModules = useMemo(
    () => resolveTargetModules(modules, target, groups),
    [target, modules, groups]
  );

  const allTargetReady     = targetModules.length > 0 && targetModules.every(isModuleReady);
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");

  const totalDurationMins = parseInt(durationHours || 0) * 60
    + parseInt(durationMinutes || 0)
    + parseInt(durationSeconds || 0) / 60;
  const timedDurationValid = recordingMode !== "timed" || totalDurationMins > 0;

  const ptpOk = ptpSyncStatus === null || ptpSyncStatus.ok;
  const canStart = experimentName && allTargetReady && !anyTargetRecording && timedDurationValid && ptpOk;
  const canSchedule = !!experimentName;

  const nameAlreadyUsed = experimentName
    ? Object.values(sessionList).some(s => s.session_name.startsWith(experimentName + "-"))
    : false;

  const targetLabel = target === "all"
    ? `all ${modules.length} module${modules.length !== 1 ? "s" : ""}`
    : target in groups
      ? `group "${target}" (${groups[target].length} module${groups[target].length !== 1 ? "s" : ""})`
      : modules.find((m) => m.id === target)?.name || target;

  const toggleDay = (day) => {
    setScheduledDays((prev) => {
      const next = new Set(prev);
      if (next.has(day)) {
        next.delete(day);
      } else {
        next.add(day);
      }
      return next;
    });
  };

  const daysDescription = scheduledDays.size === 0 || scheduledDays.size === 7
    ? "every day"
    : [...scheduledDays].sort((a, b) => a - b).map(d => DAY_NAMES[d]).join(", ");

  const durationLabel = (() => {
    const h = parseInt(durationHours || 0);
    const m = parseInt(durationMinutes || 0);
    const s = parseInt(durationSeconds || 0);
    const parts = [];
    if (h > 0) parts.push(`${h}h`);
    if (m > 0) parts.push(`${m}m`);
    if (s > 0) parts.push(`${s}s`);
    return parts.length > 0 ? parts.join(" ") : "0m";
  })();

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return;
    setSubmitError(null);

    if (recordingMode === "scheduled") {
      const daysArray = scheduledDays.size === 0 || scheduledDays.size === 7
        ? []
        : [...scheduledDays].sort((a, b) => a - b);
      socket.emit("create_scheduled_session", {
        target,
        session_name: experimentName,
        start_time: `${startHour}:${startMinute}`,
        end_time:   `${endHour}:${endMinute}`,
        days: daysArray,
        researcher: experimenter || null,
      });
    } else {
      socket.emit("create_session", {
        target,
        session_name: experimentName,
        duration_minutes: recordingMode === "timed" ? totalDurationMins : null,
        researcher: experimenter || null,
        unattended,
      });
    }

    setTarget("all");
  };

  const checkReady = (e) => {
    e.preventDefault();
    if (!experimentName) return;
    socket.emit("check_ready", { target });
  };

  return (
    <div className="new-session-form card">
      <SessionName experimentName={experimentName} />

      <form onSubmit={handleSubmit} className="session-form">
        <div className="form-row">
          <label htmlFor="target-select">Target</label>
          <select
            id="target-select"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            <option value="all">All Modules</option>

            {hasGroups && (
              <optgroup label="Groups">
                {Object.entries(groups).map(([groupName, members]) => (
                  <option key={groupName} value={groupName}>
                    {groupName} ({members.length} module{members.length !== 1 ? "s" : ""})
                  </option>
                ))}
              </optgroup>
            )}

            <optgroup label="Individual modules">
              {modules.map((m) => (
                <option key={m.id} value={m.id}>{m.name || m.id}</option>
              ))}
            </optgroup>
          </select>
        </div>

        <div className="form-row">
          <label htmlFor="mode-select">Mode</label>
          <select
            id="mode-select"
            value={recordingMode}
            onChange={(e) => setRecordingMode(e.target.value)}
          >
            <option value="immediate">Immediate - manual stop</option>
            <option value="timed">Timed - auto-stop after duration</option>
            <option value="scheduled">Scheduled - daily time window</option>
          </select>
        </div>

        {recordingMode !== "scheduled" && (
          <div className="form-row">
            <label htmlFor="unattended-check">Unattended</label>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: "normal" }}>
              <input
                id="unattended-check"
                type="checkbox"
                checked={unattended}
                onChange={(e) => setUnattended(e.target.checked)}
              />
              Long-term run — self-heal module dropouts instead of stopping, daily fault digest
            </label>
          </div>
        )}

        {recordingMode === "timed" && (
          <div className="form-row">
            <label>Duration</label>
            <div className="duration-inputs">
              <input
                type="number"
                min="0"
                max="99"
                value={durationHours}
                onChange={(e) => setDurationHours(e.target.value)}
                className="duration-input"
              />
              <span className="duration-unit">h</span>
              <input
                type="number"
                min="0"
                max="59"
                value={durationMinutes}
                onChange={(e) => setDurationMinutes(e.target.value)}
                className="duration-input"
              />
              <span className="duration-unit">m</span>
              <input
                type="number"
                min="0"
                max="59"
                value={durationSeconds}
                onChange={(e) => setDurationSeconds(e.target.value)}
                className="duration-input"
              />
              <span className="duration-unit">s</span>
            </div>
          </div>
        )}

        {recordingMode === "scheduled" && (
          <>
            <TimeSelect label="From" hour={startHour} setHour={setStartHour} minute={startMinute} setMinute={setStartMinute} />
            <TimeSelect label="To"   hour={endHour}   setHour={setEndHour}   minute={endMinute}   setMinute={setEndMinute} />
            <div className="form-row">
              <label>Days</label>
              <div className="day-picker">
                {DAY_NAMES.map((name, i) => (
                  <button
                    key={i}
                    type="button"
                    className={`day-btn${scheduledDays.has(i) ? " day-btn--active" : ""}`}
                    onClick={() => toggleDay(i)}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="session-description">
          {recordingMode === "immediate" && (
            <p>Recording starts on {targetLabel} in ~3 seconds.</p>
          )}
          {recordingMode === "timed" && (
            <p>{targetLabel} will record for {durationLabel}, then stop automatically.</p>
          )}
          {recordingMode === "scheduled" && (
            <p>{targetLabel} will record from {startHour}:{startMinute} to {endHour}:{endMinute}, {daysDescription}.</p>
          )}
          <div className="session-name-preview-block">
            Session name <strong>{sessionNamePreview}</strong>
          </div>
        </div>

        {submitError && (
          <p className="form-warning">{submitError}</p>
        )}
        {nameAlreadyUsed && (
          <p className="form-warning">Session name already used - previous recordings exist with this name. Consider updating the trial or rat ID.</p>
        )}
        {!canStart && anyTargetRecording && (
          <p className="form-warning">One or more target modules are already recording.</p>
        )}
        {!canStart && !anyTargetRecording && targetModules.length > 0 && !allTargetReady && (
          <p className="form-warning">Not all target modules are ready.</p>
        )}
        {!timedDurationValid && (
          <p className="form-warning">Enter a duration greater than 0.</p>
        )}
        {ptpSyncStatus !== null && !ptpSyncStatus.ok && (
          <p className="form-warning">
            PTP not synchronised -{" "}
            {ptpSyncStatus.failures?.map((f) => `${f.module_id}: ${f.reason}`).join("; ")}
          </p>
        )}
        {ptpSyncStatus?.ok && (
          <p className="form-ok">
            PTP synchronised to within {ptpSyncStatus.max_offset_us}µs
          </p>
        )}
        {dataRate?.total_mb_per_min > 0 && (
          <p className="form-hint">
            Est. data rate: ~{dataRate.total_mb_per_min} MB/min
            {" "}({dataRate.total_gb_per_hour} GB/hour)
            {dataRate.share_runway_hours != null && (
              <> · share holds ~{
                dataRate.share_runway_hours >= 48
                  ? `${Math.round(dataRate.share_runway_hours / 24)} days`
                  : `${Math.round(dataRate.share_runway_hours)} hours`
              } at this rate</>
            )}
          </p>
        )}

        <div className="button-row">
          {/* Useful regardless of mode -- a scheduled session still benefits
              from confirming modules are ready right now, even though the
              actual start is deferred to the scheduled window. */}
          <button type="button" className="secondary-button" onClick={checkReady}>
            Check Ready
          </button>
          <button type="submit" className="primary-button" disabled={recordingMode === "scheduled" ? !canSchedule : !canStart}>
            {recordingMode === "scheduled" ? "Schedule Session" : "Create Session"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewSessionForm;
