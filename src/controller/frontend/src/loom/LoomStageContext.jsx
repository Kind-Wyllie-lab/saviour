/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, useCallback, useEffect, useRef, useMemo } from "react";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import "./LoomStageContext.css";

const LoomStageContext = createContext(null);

export const CAMERA_TYPES = new Set(["camera", "loom_camera", "apa_camera"]);

// Group name auto-assigned to camera modules in habituation so a single
// "cameras" target can be used for recording rather than individual sessions.
export const HABITUATION_GROUP = "cameras";

function pushStageConfig(stage) {
  // Arm/disarm loom stimulus on loom_camera modules.
  socket.emit("apply_section_to_type", {
    module_type: "loom_camera",
    section: "loom_stimulus",
    data: { armed: stage === "loom" },
  });

  // In habituation, assign all camera-type modules to the "cameras" group so
  // they subscribe to cmd/cameras and can be targeted as a single unit.
  if (stage === "habituation") {
    CAMERA_TYPES.forEach((type) => {
      socket.emit("apply_section_to_type", {
        module_type: type,
        section: "module",
        data: { group: HABITUATION_GROUP },
      });
    });
  }
}

const STAGE_STORAGE_KEY = "loom_stage";

export function LoomStageProvider({ children }) {
  const [stage, setStageState] = useState(
    () => localStorage.getItem(STAGE_STORAGE_KEY) || "habituation"
  );
  const stageRef = useRef(localStorage.getItem(STAGE_STORAGE_KEY) || "habituation");

  // Push the current stage config whenever the socket connects (covers both
  // initial page load and controller restarts) so modules always reflect the
  // UI state rather than their own defaults.
  useEffect(() => {
    const onConnect = () => pushStageConfig(stageRef.current);
    socket.on("connect", onConnect);
    if (socket.connected) onConnect();
    return () => socket.off("connect", onConnect);
  }, []);

  const setStage = useCallback((newStage) => {
    stageRef.current = newStage;
    setStageState(newStage);
    localStorage.setItem(STAGE_STORAGE_KEY, newStage);
    pushStageConfig(newStage);
  }, []);

  return (
    <LoomStageContext.Provider value={{ stage, setStage }}>
      {children}
    </LoomStageContext.Provider>
  );
}

export function useLoomStage() {
  return useContext(LoomStageContext);
}

// Changing stage re-arms/disarms the loom stimulus and reassigns camera
// groups (see pushStageConfig above) -- mutating that mid-recording would
// change what an active session is doing out from under the operator, so
// every stage control locks while any session is actively recording.
function useStageLocked() {
  const { sessionList } = useSessions();
  return useMemo(() => sessionList.some((s) => s.state === "active"), [sessionList]);
}

export function StageToggle() {
  const { stage, setStage } = useLoomStage();
  const locked = useStageLocked();
  return (
    <div
      className="stage-toggle"
      role="group"
      aria-label="Experiment stage"
      title={locked ? "Locked while recording" : undefined}
    >
      <button
        className={`stage-toggle__btn${stage === "habituation" ? " stage-toggle__btn--active stage-toggle__btn--hab" : ""}`}
        disabled={locked}
        onClick={() => setStage("habituation")}
      >
        Habituation
      </button>
      <button
        className={`stage-toggle__btn${stage === "loom" ? " stage-toggle__btn--active stage-toggle__btn--loom" : ""}`}
        disabled={locked}
        onClick={() => setStage("loom")}
      >
        Loom
      </button>
    </div>
  );
}

// Dropdown variant for tighter spots (e.g. under the dashboard's recording
// timer) where the two-button toggle doesn't fit as naturally.
export function StageDropdown() {
  const { stage, setStage } = useLoomStage();
  const locked = useStageLocked();
  return (
    <div className="stage-dropdown">
      <label htmlFor="loom-stage-select" className="stage-dropdown__label">Stage</label>
      <select
        id="loom-stage-select"
        className={`stage-dropdown__select stage-dropdown__select--${stage}`}
        value={stage}
        disabled={locked}
        title={locked ? "Locked while recording" : undefined}
        onChange={(e) => setStage(e.target.value)}
      >
        <option value="habituation">Habituation</option>
        <option value="loom">Loom</option>
      </select>
    </div>
  );
}
