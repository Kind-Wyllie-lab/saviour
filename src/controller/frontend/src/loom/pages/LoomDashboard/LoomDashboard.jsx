import React, { useState, useEffect, useRef } from "react";
import "./LoomDashboard.css";

import useModules from "/src/hooks/useModules";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";

const CAMERA_PORT    = 8080;
const STALL_MS       = 8000;
const RECONNECT_MS   = 2500;

function StreamTile({ ip, port, label, isRecording }) {
  const [streamKey, setStreamKey] = useState(Date.now());
  const [fullscreen, setFullscreen] = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const bump = () => setStreamKey(Date.now());

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  const resetStall = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_MS);
  };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_MS);
  };

  return (
    <>
      <div className="loom-stream-tile">
        <img
          key={streamKey}
          src={`http://${ip}:${port}/video_feed`}
          alt={label || "stream"}
          onLoad={resetStall}
          onError={handleError}
          onClick={() => setFullscreen(true)}
        />
        {isRecording && <span className="loom-rec-dot" title="Recording" />}
      </div>

      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

function LoomDashboard() {
  const { moduleList } = useModules();

  const loomCameras  = moduleList.filter((m) => m.type === "loom_camera");
  const basicCameras = moduleList.filter((m) => m.type === "camera" || m.type === "apa_camera");

  // Ordered: loom first (left), basic second (right)
  const orderedCameras = [...loomCameras, ...basicCameras];

  return (
    <div className="loom-dashboard">
      <RecordingStatusWidget />

      <div className="loom-dashboard-main">
        <div className="loom-dashboard-cameras">
          {orderedCameras.length === 0 ? (
            <div className="loom-dashboard-empty">No camera modules connected</div>
          ) : (
            orderedCameras.map((m) => (
              <StreamTile
                key={m.id}
                ip={m.ip}
                port={CAMERA_PORT}
                label={m.name}
                isRecording={m.status === "RECORDING"}
              />
            ))
          )}
        </div>

        <div className="loom-dashboard-panel">
          <HealthSummaryWidget />
          <ModuleList modules={moduleList} />
        </div>
      </div>
    </div>
  );
}

export default LoomDashboard;
