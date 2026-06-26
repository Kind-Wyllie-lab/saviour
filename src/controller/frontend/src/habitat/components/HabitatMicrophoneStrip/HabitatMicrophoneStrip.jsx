import React, { useState, useRef } from "react";
import "./HabitatMicrophoneStrip.css";

const RECONNECT_DELAY_MS = 3000;
const STALL_TIMEOUT_MS = 10000;

function MicModuleColumn({ module }) {
  const [streamKey, setStreamKey] = useState(Date.now());
  const stallTimer = useRef(null);
  const reconnectTimer = useRef(null);

  const bump = () => setStreamKey(Date.now());

  const resetStall = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_TIMEOUT_MS);
  };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_DELAY_MS);
  };

  return (
    <div className="mic-module-col">
      {module.status === "OFFLINE" ? (
        <div className="mic-module-offline">
          <span>{module.name || module.id}</span>
          <span className="mic-module-offline-label">OFFLINE</span>
        </div>
      ) : (
        <img
          key={streamKey}
          src={`http://${module.ip}:8081/video_feed`}
          alt={module.name || module.id}
          onLoad={resetStall}
          onError={handleError}
        />
      )}
      {module.status === "RECORDING" && <span className="mic-rec-dot" />}
    </div>
  );
}

function HabitatMicrophoneStrip({ modules }) {
  const allModules = Array.isArray(modules) ? modules : Object.values(modules ?? {});
  const mics = allModules
    .filter(m => m.type === "microphone")
    .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));

  if (!mics.length) return null;

  return (
    <div className="mic-strip">
      {mics.map(m => <MicModuleColumn key={m.id} module={m} />)}
    </div>
  );
}

export default HabitatMicrophoneStrip;
