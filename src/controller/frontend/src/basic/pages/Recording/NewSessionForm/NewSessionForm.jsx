import { useState, useMemo } from "react";
import socket from "/src/socket";
import "./NewSessionForm.css";

import useExperimentTitle from "/src/hooks/useExperimentTitle";
import SessionName from "../SessionName/SessionName";
import TimeSelect from "./TimeSelect/TimeSelect";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ALL_DAYS = new Set([0, 1, 2, 3, 4, 5, 6]);

function NewSessionForm({ modules, sessionList = {} }) {
  const [target, setTarget] = useState("all");
  const { experimentName } = useExperimentTitle();

  const [recordingMode, setRecordingMode] = useState("immediate");

  // Timed mode
  const [durationHours, setDurationHours]     = useState("0");
  const [durationMinutes, setDurationMinutes] = useState("10");

  // Scheduled mode
  const [startHour, setStartHour]     = useState("19");
  const [startMinute, setStartMinute] = useState("00");
  const [endHour, setEndHour]         = useState("23");
  const [endMinute, setEndMinute]     = useState("00");
  const [scheduledDays, setScheduledDays] = useState(new Set(ALL_DAYS));

  // Derive groups from module list
  const groups = useMemo(() => {
    const map = {};
    modules.forEach((m) => {
      if (m.group) {
        map[m.group] = [...(map[m.group] ?? []), m];
      }
    });
    return map;
  }, [modules]);

  const hasGroups = Object.keys(groups).length > 0;

  const targetModules = useMemo(() => {
    if (target === "all")    return modules;
    if (target in groups)   return groups[target];
    return modules.filter((m) => m.id === target);
  }, [target, modules, groups]);

  const allTargetReady     = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");

  const totalDurationMins = parseInt(durationHours || 0) * 60 + parseInt(durationMinutes || 0);
  const timedDurationValid = recordingMode !== "timed" || totalDurationMins > 0;

  const canStart = experimentName && allTargetReady && !anyTargetRecording && timedDurationValid;

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
    if (h > 0 && m > 0) return `${h}h ${m}m`;
    if (h > 0) return `${h}h`;
    return `${m}m`;
  })();

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return;

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
      });
    } else {
      socket.emit("create_session", {
        target,
        session_name: experimentName,
        duration_minutes: recordingMode === "timed" ? totalDurationMins : null,
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
      <h2>New Session</h2>

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
            <option value="immediate">Immediate — manual stop</option>
            <option value="timed">Timed — auto-stop after duration</option>
            <option value="scheduled">Scheduled — daily time window</option>
          </select>
        </div>

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
            Session name <strong>{experimentName ? `${experimentName}-(TIMESTAMP)` : "—"}</strong>
          </div>
        </div>

        {nameAlreadyUsed && (
          <p className="form-warning">Session name already used — previous recordings exist with this name. Consider updating the trial or rat ID.</p>
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

        <div className="button-row">
          <button type="button" className="secondary-button" onClick={checkReady}>
            Check Ready
          </button>
          <button type="submit" className="primary-button" disabled={!canStart}>
            {recordingMode === "scheduled" ? "Schedule Session" : "Start Recording"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewSessionForm;
