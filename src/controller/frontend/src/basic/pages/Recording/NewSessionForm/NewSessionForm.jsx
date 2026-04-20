import { useState, useMemo } from "react";
import socket from "/src/socket";
import "./NewSessionForm.css";

import useExperimentTitle from "/src/hooks/useExperimentTitle";
import SessionName from "../SessionName/SessionName";
import TimeSelect from "./TimeSelect/TimeSelect";


function NewSessionForm({ modules, sessionList = {} }) {
  const [target, setTarget] = useState("all");
  const { experimentName } = useExperimentTitle();

  const [isScheduled, setIsScheduled] = useState(false);
  const [startHour, setStartHour]     = useState("19");
  const [startMinute, setStartMinute] = useState("00");
  const [endHour, setEndHour]         = useState("23");
  const [endMinute, setEndMinute]     = useState("00");

  // Derive groups from module list: { groupName: [module, ...] }
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

  // Resolve the modules that will be targeted
  const targetModules = useMemo(() => {
    if (target === "all")          return modules;
    if (target in groups)          return groups[target];
    return modules.filter((m) => m.id === target);
  }, [target, modules, groups]);

  const allTargetReady     = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");
  const canStart           = experimentName && allTargetReady && !anyTargetRecording;

  const nameAlreadyUsed = experimentName
    ? Object.values(sessionList).some(s => s.session_name.startsWith(experimentName + "-"))
    : false;

  // Human-readable label for the target description line
  const targetLabel = target === "all"
    ? `all ${modules.length} module${modules.length !== 1 ? "s" : ""}`
    : target in groups
      ? `group "${target}" (${groups[target].length} module${groups[target].length !== 1 ? "s" : ""})`
      : modules.find((m) => m.id === target)?.name || target;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return;

    if (isScheduled) {
      socket.emit("create_scheduled_session", {
        target,
        session_name: experimentName,
        start_time: `${startHour}:${startMinute}`,
        end_time:   `${endHour}:${endMinute}`,
      });
    } else {
      socket.emit("create_session", { target, session_name: experimentName });
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

        <div className="form-row form-row--checkbox">
          <label>
            <input
              type="checkbox"
              checked={isScheduled}
              onChange={() => setIsScheduled(!isScheduled)}
            />
            Scheduled recording
          </label>
        </div>

        {isScheduled && (
          <>
            <TimeSelect label="Start" hour={startHour} setHour={setStartHour} minute={startMinute} setMinute={setStartMinute} />
            <TimeSelect label="End"   hour={endHour}   setHour={setEndHour}   minute={endMinute}   setMinute={setEndMinute} />
          </>
        )}

        <div className="session-description">
          {isScheduled ? (
            <p>
              {targetLabel} will record daily between {startHour}:{startMinute} and {endHour}:{endMinute}.
            </p>
          ) : (
            <p>Recording starts on {targetLabel} in ~3 seconds.</p>
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

        <div className="button-row">
          <button type="button" className="secondary-button" onClick={checkReady}>
            Check Ready
          </button>
          <button type="submit" className="primary-button" disabled={!canStart}>
            {isScheduled ? "Schedule Session" : "Start Recording"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewSessionForm;
