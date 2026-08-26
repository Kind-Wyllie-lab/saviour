import React, { useState, useEffect, useMemo } from "react";
import socket from "/src/socket";
import "./LoomRecording.css";

import { useLoomStage, StageToggle, CAMERA_TYPES, HABITUATION_GROUP } from "/src/loom/LoomStageContext";
import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import usePersistedState from "/src/hooks/usePersistedState";
import SessionList from "/src/basic/pages/Recording/SessionList/SessionList";
import SessionName from "/src/basic/pages/Recording/SessionName/SessionName";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";

function padTwo(n) { return String(n).padStart(2, "0"); }

function formatTs(date) {
  return `${date.getFullYear()}${padTwo(date.getMonth()+1)}${padTwo(date.getDate())}-${padTwo(date.getHours())}${padTwo(date.getMinutes())}${padTwo(date.getSeconds())}`;
}

function safeName(str) {
  return (str || "").replace(/[^a-zA-Z0-9 \-_]/g, "").trim().replace(/ /g, "_");
}

const DEFAULT_DURATION_MINUTES = 12;
const DEFAULT_DURATION_SECONDS = 15; // researcher-requested default: 12m15s

export default function LoomRecording() {
  const { stage } = useLoomStage();
  const { moduleList } = useModules();
  const { sessionList } = useSessions();
  const { experimentName, experimenter } = useExperimentTitle();

  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const [timedEnabled, setTimedEnabled] = usePersistedState("saviour_loom_form_timed_enabled", true);
  const [durationMinutes, setDurationMinutes] = usePersistedState(
    "saviour_loom_form_duration_m",
    String(DEFAULT_DURATION_MINUTES)
  );
  const [durationSeconds, setDurationSeconds] = usePersistedState(
    "saviour_loom_form_duration_s",
    String(DEFAULT_DURATION_SECONDS)
  );
  const totalDurationMins = parseInt(durationMinutes || 0) + parseInt(durationSeconds || 0) / 60;
  const durationValid = !timedEnabled || totalDurationMins > 0;

  const cameraModules = useMemo(
    () => moduleList.filter((m) => CAMERA_TYPES.has(m.type)),
    [moduleList]
  );

  // Habituation records cameras only; loom records everything.
  const targetModules = stage === "habituation" ? cameraModules : moduleList;

  const allReady     = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyRecording = targetModules.some((m) => m.status === "RECORDING");
  const canStart     = !!experimentName && allReady && !anyRecording && durationValid;
  const notReadyModules = targetModules.filter((m) => m.status === "NOT_READY");

  const nameAlreadyUsed = experimentName
    ? sessionList.some((s) => s.session_name?.startsWith(safeName(experimentName) + "-"))
    : false;

  // Habituation uses the auto-assigned "cameras" group; loom uses all.
  const target = stage === "habituation" ? HABITUATION_GROUP : "all";

  const sessionPreview = useMemo(() => {
    if (!experimentName) return "-";
    const base = safeName(experimentName);
    const ts = formatTs(now);
    // Match backend _format_session_name: append target when not "all".
    return target !== "all" ? `${base}-${target}-${ts}` : `${base}-${ts}`;
  }, [experimentName, now, target]);

  const handleStart = () => {
    if (!experimentName) return;
    socket.emit("create_session", {
      target,
      session_name: `${safeName(experimentName)}-${formatTs(now)}`,
      duration_minutes: timedEnabled ? totalDurationMins : null,
      researcher: experimenter || null,
    });
  };

  const handleCheckReady = () => {
    socket.emit("check_ready", { target });
  };

  const stageLabel = stage === "habituation"
    ? `Camera modules (group: "${HABITUATION_GROUP}") - loom stimulus disabled`
    : `All modules - loom stimulus active`;

  return (
    <div className="loom-recording-page">
      <div className="loom-recording-layout">

        <div className="loom-recording-form card">
          <h2>New Session</h2>

          <StageToggle />

          <p className="loom-recording-scope">
            <span className={`loom-recording-scope-dot loom-recording-scope-dot--${stage}`} />
            {stageLabel}
          </p>

          <SessionName experimentName={experimentName} stageOverride={stage} />

          <div className="loom-recording-preview">
            Session name: <strong>{sessionPreview}</strong>
          </div>

          <div className="loom-recording-duration">
            <label className="loom-recording-duration-toggle">
              <input
                type="checkbox"
                checked={timedEnabled}
                onChange={(e) => setTimedEnabled(e.target.checked)}
              />
              Timed recording - auto-stop after
            </label>
            <div className="loom-recording-duration-inputs">
              <input
                type="number"
                min="0"
                max="999"
                disabled={!timedEnabled}
                value={durationMinutes}
                onChange={(e) => setDurationMinutes(e.target.value)}
                className="duration-input"
              />
              <span className="duration-unit">min</span>
              <input
                type="number"
                min="0"
                max="59"
                disabled={!timedEnabled}
                value={durationSeconds}
                onChange={(e) => setDurationSeconds(e.target.value)}
                className="duration-input"
              />
              <span className="duration-unit">sec</span>
            </div>
          </div>
          {!durationValid && (
            <p className="loom-recording-warning">Enter a duration greater than 0.</p>
          )}

          {nameAlreadyUsed && (
            <p className="loom-recording-warning">
              Session name already used - previous recordings exist with this name.
            </p>
          )}
          {!canStart && anyRecording && (
            <p className="loom-recording-warning">Target modules are already recording.</p>
          )}
          {!canStart && !anyRecording && targetModules.length > 0 && !allReady && (
            <div className="loom-recording-warning">
              <p>Not all target modules are ready:</p>
              <ul>
                {notReadyModules.map((m) => (
                  <li key={m.id}>{m.name}: {m.ready_message || "no reason given"}</li>
                ))}
              </ul>
            </div>
          )}
          {targetModules.length === 0 && (
            <p className="loom-recording-warning">No target modules connected.</p>
          )}

          <div className="loom-recording-actions">
            <button className="secondary-button" onClick={handleCheckReady}>
              Check Ready
            </button>
            <button className="primary-button" disabled={!canStart} onClick={handleStart}>
              Start Recording
            </button>
          </div>
        </div>

        <div className="loom-recording-sessions">
          <SessionList sessionList={sessionList} modules={moduleList} />
        </div>

        <div className="loom-recording-modules">
          <ModuleList modules={moduleList} />
        </div>

      </div>
    </div>
  );
}
