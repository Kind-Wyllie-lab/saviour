import React from "react";
import "./Dashboard.css";

import useModules from "/src/hooks/useModules";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";

// MJPEG stream port by module type
const STREAM_PORTS = {
  camera:     8080,
  microphone: 8081,
  ttl:        8082,
};

function Dashboard() {
  const { moduleList } = useModules();

  const cameraModules     = moduleList.filter((m) => m.type === "camera");
  const micModules        = moduleList.filter((m) => m.type === "microphone");
  const ttlModules        = moduleList.filter((m) => m.type === "ttl");

  return (
    <div className="dashboard">
      <RecordingStatusWidget />

      <div className="dashboard-main">
        {/* ── Left: camera grid ─────────────────────────────────────── */}
        <div className="dashboard-cameras">
          {cameraModules.length === 0 ? (
            <div className="dashboard-no-cameras">No camera modules connected</div>
          ) : (
            cameraModules.map((m) => (
              <MJPEGStreamCard
                key={m.id}
                ip={m.ip}
                port={STREAM_PORTS.camera}
                label={m.name}
                isRecording={m.status === "RECORDING"}
              />
            ))
          )}
        </div>

        {/* ── Right: status panel ───────────────────────────────────── */}
        <div className="dashboard-panel">
          <HealthSummaryWidget />
          <ModuleList modules={moduleList} />

          {micModules.map((m) => (
            <MJPEGStreamCard
              key={m.id}
              ip={m.ip}
              port={STREAM_PORTS.microphone}
              label={`${m.name} — Audio`}
              isRecording={m.status === "RECORDING"}
            />
          ))}

          {ttlModules.map((m) => (
            <MJPEGStreamCard
              key={m.id}
              ip={m.ip}
              port={STREAM_PORTS.ttl}
              label={`${m.name} — TTL`}
              isRecording={m.status === "RECORDING"}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export default Dashboard;
