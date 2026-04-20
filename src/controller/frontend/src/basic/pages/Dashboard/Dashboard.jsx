import React, { useState, useMemo, useEffect } from "react";
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

const COMPACT_BREAKPOINT = 1280;

function useIsCompact() {
  const [isCompact, setIsCompact] = useState(
    () => window.innerWidth <= COMPACT_BREAKPOINT
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${COMPACT_BREAKPOINT}px)`);
    const handler = (e) => setIsCompact(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return isCompact;
}

function Dashboard() {
  const { moduleList } = useModules();
  const [selectedGroup, setSelectedGroup] = useState("all");
  const isCompact = useIsCompact();

  const groups = useMemo(() => {
    const names = [...new Set(moduleList.map(m => m.group).filter(Boolean))].sort();
    return names;
  }, [moduleList]);

  const visibleModules = selectedGroup === "all"
    ? moduleList
    : moduleList.filter(m => m.group === selectedGroup);

  const cameraModules = visibleModules.filter(m => m.type === "camera");
  const micModules    = visibleModules.filter(m => m.type === "microphone");
  const ttlModules    = visibleModules.filter(m => m.type === "ttl");

  // Optimal column count for wide camera grid
  const cameraCols = (() => { const n = cameraModules.length; return n <= 2 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4; })();

  // Flat ordered list of all streams — used by the compact grid
  const allStreams = useMemo(() => [
    ...cameraModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.camera,
      label: m.name, isRecording: m.status === "RECORDING",
    })),
    ...micModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.microphone,
      label: `${m.name} — Audio`, isRecording: m.status === "RECORDING",
    })),
    ...ttlModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.ttl,
      label: `${m.name} — TTL`, isRecording: m.status === "RECORDING",
    })),
  ], [cameraModules, micModules, ttlModules]);

  const compactCols = (() => { const ns = allStreams.length; return ns <= 1 ? 1 : ns <= 4 ? 2 : ns <= 9 ? 3 : 4; })();

  return (
    <div className="dashboard">
      <RecordingStatusWidget />

      {groups.length > 0 && (
        <div className="dashboard-group-filter">
          <label htmlFor="group-select">Group:</label>
          <select
            id="group-select"
            value={selectedGroup}
            onChange={e => setSelectedGroup(e.target.value)}
          >
            <option value="all">All modules</option>
            {groups.map(g => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
        </div>
      )}

      {isCompact ? (
        /* ── Compact: stream grid + status below ── */
        <div className="dashboard-compact">
          {allStreams.length === 0 ? (
            <div className="dashboard-no-cameras">No streams connected</div>
          ) : (
            <div className="dashboard-compact-streams" style={{ gridTemplateColumns: `repeat(${compactCols}, 1fr)` }}>
              {allStreams.map(s => (
                <MJPEGStreamCard
                  key={s.id}
                  ip={s.ip}
                  port={s.port}
                  label={s.label}
                  isRecording={s.isRecording}
                />
              ))}
            </div>
          )}

          <div className="dashboard-compact-panel">
            <HealthSummaryWidget />
            <ModuleList modules={visibleModules} />
          </div>
        </div>
      ) : (
        /* ── Wide: cameras left, status panel right ── */
        <div className="dashboard-main">
          <div className="dashboard-cameras" style={{ gridTemplateColumns: `repeat(${cameraCols}, 1fr)` }}>
            {cameraModules.length === 0 ? (
              <div className="dashboard-no-cameras">No camera modules connected</div>
            ) : (
              cameraModules.map(m => (
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

          <div className="dashboard-panel">
            <HealthSummaryWidget />
            <ModuleList modules={visibleModules} />
            {micModules.map(m => (
              <MJPEGStreamCard
                key={m.id}
                ip={m.ip}
                port={STREAM_PORTS.microphone}
                label={`${m.name} — Audio`}
                isRecording={m.status === "RECORDING"}
              />
            ))}
            {ttlModules.map(m => (
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
      )}
    </div>
  );
}

export default Dashboard;
