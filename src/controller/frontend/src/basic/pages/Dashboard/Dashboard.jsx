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
  const [currentStreamIndex, setCurrentStreamIndex] = useState(0);
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

  // Flat ordered list of all streams — used by the compact carousel
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

  // Clamp displayed index if the stream list shrinks while browsing
  const safeIndex = allStreams.length > 0
    ? Math.min(currentStreamIndex, allStreams.length - 1)
    : 0;

  const prev = () =>
    setCurrentStreamIndex(i => (i - 1 + allStreams.length) % allStreams.length);
  const next = () =>
    setCurrentStreamIndex(i => (i + 1) % allStreams.length);

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
        /* ── Compact: single-stream carousel + status below ── */
        <div className="dashboard-compact">
          <div className="dashboard-carousel">
            {allStreams.length === 0 ? (
              <div className="dashboard-no-cameras">No streams connected</div>
            ) : (
              <>
                <div className="carousel-stream">
                  <MJPEGStreamCard
                    key={allStreams[safeIndex].id}
                    ip={allStreams[safeIndex].ip}
                    port={allStreams[safeIndex].port}
                    label={allStreams[safeIndex].label}
                    isRecording={allStreams[safeIndex].isRecording}
                  />
                </div>
                {allStreams.length > 1 && (
                  <div className="carousel-controls">
                    <button className="carousel-btn" onClick={prev} aria-label="Previous stream">
                      &#8249;
                    </button>
                    <div className="carousel-dots">
                      {allStreams.map((s, i) => (
                        <button
                          key={s.id}
                          className={`carousel-dot${i === safeIndex ? " carousel-dot--active" : ""}`}
                          onClick={() => setCurrentStreamIndex(i)}
                          aria-label={s.label}
                        />
                      ))}
                    </div>
                    <button className="carousel-btn" onClick={next} aria-label="Next stream">
                      &#8250;
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="dashboard-compact-panel">
            <HealthSummaryWidget />
            <ModuleList modules={visibleModules} />
          </div>
        </div>
      ) : (
        /* ── Wide: cameras left, status panel right ── */
        <div className="dashboard-main">
          <div className="dashboard-cameras">
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
