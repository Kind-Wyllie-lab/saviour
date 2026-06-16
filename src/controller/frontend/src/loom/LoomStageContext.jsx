/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, useCallback } from "react";
import socket from "/src/socket";
import "./LoomStageContext.css";

const LoomStageContext = createContext(null);

export const CAMERA_TYPES = new Set(["camera", "loom_camera", "apa_camera"]);

// Group name auto-assigned to camera modules in habituation so a single
// "cameras" target can be used for recording rather than individual sessions.
export const HABITUATION_GROUP = "cameras";

function pushStageConfig(stage) {
  // Enable/disable loom stimulus on loom_camera modules.
  socket.emit("apply_section_to_type", {
    module_type: "loom_camera",
    section: "loom_stimulus",
    data: { enabled: stage === "loom" },
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

export function LoomStageProvider({ children }) {
  const [stage, setStageState] = useState("habituation");

  const setStage = useCallback((newStage) => {
    setStageState(newStage);
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

export function StageToggle() {
  const { stage, setStage } = useLoomStage();
  return (
    <div className="stage-toggle" role="group" aria-label="Experiment stage">
      <button
        className={`stage-toggle__btn${stage === "habituation" ? " stage-toggle__btn--active stage-toggle__btn--hab" : ""}`}
        onClick={() => setStage("habituation")}
      >
        Habituation
      </button>
      <button
        className={`stage-toggle__btn${stage === "loom" ? " stage-toggle__btn--active stage-toggle__btn--loom" : ""}`}
        onClick={() => setStage("loom")}
      >
        Loom
      </button>
    </div>
  );
}
