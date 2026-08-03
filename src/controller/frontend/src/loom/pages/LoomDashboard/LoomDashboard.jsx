import React, { useState, useEffect, useRef } from "react";
import "./LoomDashboard.css";

import useModules from "/src/hooks/useModules";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import LoomStimulusControl from "/src/loom/components/LoomStimulusControl/LoomStimulusControl";
import { StageToggle } from "/src/loom/LoomStageContext";

const CAMERA_PORT  = 8080;
const MIC_PORT     = 8081;
const STALL_MS     = 8000;
const RECONNECT_MS = 2500;
// Floor between actual reconnects, regardless of how many things ask for one.
// Each bump() fully unmounts/remounts the <img>, opening a fresh long-lived
// MJPEG connection — a burst of triggers (e.g. several rapid config saves,
// each restarting the camera and separately tripping the stall/error/config-
// sync handlers) can otherwise open more concurrent connections to the same
// host than the browser allows, leaving the newest one stuck pending forever
// with neither onLoad nor onError ever firing to recover it.
const MIN_RECONNECT_MS = 3000;

function StreamTile({ ip, port, label, isRecording, syncStatus }) {
  const [streamKey, setStreamKey] = useState(Date.now());
  const [fullscreen, setFullscreen] = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const configTimer    = useRef(null);
  const prevStatus     = useRef(syncStatus);
  const lastBumpAt      = useRef(0);
  const pendingBumpTimer = useRef(null);
  const imgRef           = useRef(null);

  // Changing `key` unmounts the old <img>, but Chrome doesn't reliably abort
  // the underlying multipart/x-mixed-replace connection just because the
  // element left the DOM — it can keep the socket open until GC gets to it.
  // Repeated reconnects can then pile up dangling connections against the
  // browser's per-host limit, so the *next* reconnect never gets a socket at
  // all — stuck with neither onLoad nor onError to recover it, exactly the
  // failure a manual refresh "fixes" by tearing down every connection to the
  // origin at once. Clearing src synchronously forces an immediate abort
  // before the remount.
  const doBump = () => {
    if (imgRef.current) imgRef.current.src = "";
    lastBumpAt.current = Date.now();
    setStreamKey(Date.now());
  };

  const bump = () => {
    const now = Date.now();
    const elapsed = now - lastBumpAt.current;
    if (elapsed >= MIN_RECONNECT_MS) {
      doBump();
    } else {
      clearTimeout(pendingBumpTimer.current);
      pendingBumpTimer.current = setTimeout(doBump, MIN_RECONNECT_MS - elapsed);
    }
  };

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
      clearTimeout(pendingBumpTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  // A live MJPEG connection that dies after its first frame (e.g. the OS
  // suspends/changes the network under it — Chrome surfaces this as
  // ERR_NETWORK_IO_SUSPENDED / ERR_NETWORK_CHANGED on laptop sleep/wake or
  // wifi roam) fires neither onLoad nor onError again, so the stream sits
  // frozen until something remounts it (opening fullscreen, or a manual
  // refresh). Force a reconnect on the two OS-level signals that correlate
  // with those errors.
  useEffect(() => {
    const handleVisibility = () => { if (document.visibilityState === "visible") bump(); };
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("online", bump);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("online", bump);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Bump stream when a save completes (PENDING → SYNCED means camera restarted).
  useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = syncStatus;
    if (prev !== "PENDING" || syncStatus !== "SYNCED") return;
    clearTimeout(configTimer.current);
    configTimer.current = setTimeout(bump, 2000);
    return () => clearTimeout(configTimer.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncStatus]);

  // Clear only, don't re-arm — Chrome fires onLoad once for a
  // multipart/x-mixed-replace (MJPEG) stream, on the first frame only, and
  // never again for subsequent frames. Rescheduling here would fire the
  // timer forever every STALL_MS regardless of stream health, forcing a
  // full reconnect (and killing the live video) on a healthy stream.
  // Recovery from a stream that actually dies is handled by onError.
  const resetStall = () => clearTimeout(stallTimer.current);

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
          ref={imgRef}
          src={`http://${ip}:${port}/video_feed?t=${streamKey}`}
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

// Overlaid prev/next picker — only renders when there are multiple options.
function TileSelector({ modules, selectedIndex, onSelect }) {
  if (modules.length <= 1) return null;
  const prev = () => onSelect((selectedIndex - 1 + modules.length) % modules.length);
  const next = () => onSelect((selectedIndex + 1) % modules.length);
  const label = modules[selectedIndex]?.name || modules[selectedIndex]?.id || "";
  return (
    <div className="loom-tile-selector">
      <button className="loom-tile-selector-btn" onClick={prev} title="Previous">&#8249;</button>
      <span className="loom-tile-selector-label">{label}</span>
      <button className="loom-tile-selector-btn" onClick={next} title="Next">&#8250;</button>
    </div>
  );
}

function TilePlaceholder({ label }) {
  return (
    <div className="loom-tile-placeholder">
      <span>{label}</span>
    </div>
  );
}

function LoomDashboard() {
  const { moduleList } = useModules();
  const [sideCamIdx, setSideCamIdx] = useState(0);
  const [micIdx,     setMicIdx]     = useState(0);

  const loomCam  = moduleList.find((m) => m.type === "loom_camera");
  const sideCams = moduleList.filter((m) => m.type === "camera");
  const mics     = moduleList.filter((m) => m.type === "microphone");

  // Clamp stored indices when modules disconnect
  const safeSideCamIdx = sideCams.length ? Math.min(sideCamIdx, sideCams.length - 1) : 0;
  const safeMicIdx     = mics.length     ? Math.min(micIdx,     mics.length     - 1) : 0;
  const sideCam = sideCams[safeSideCamIdx] ?? null;
  const mic     = mics[safeMicIdx]         ?? null;

  return (
    <div className="loom-dashboard">
      <div className="loom-dashboard-topbar">
        <RecordingStatusWidget />
        <StageToggle />
      </div>

      <div className="loom-dashboard-main">
        <div className="loom-dashboard-cameras">

          {/* Top row: loom cam (wide, left) + selectable side cam (right) */}
          <div className="loom-cameras-top">
            <div className="loom-tile-wrap loom-tile-main">
              {loomCam ? (
                <StreamTile
                  ip={loomCam.ip}
                  port={CAMERA_PORT}
                  label={loomCam.name}
                  isRecording={loomCam.status === "RECORDING"}
                  syncStatus={loomCam.config_sync_status}
                />
              ) : (
                <TilePlaceholder label="No loom camera connected" />
              )}
            </div>
            <div className="loom-tile-wrap loom-tile-side">
              <TileSelector modules={sideCams} selectedIndex={safeSideCamIdx} onSelect={setSideCamIdx} />
              {sideCam ? (
                <StreamTile
                  key={sideCam.id}
                  ip={sideCam.ip}
                  port={CAMERA_PORT}
                  label={sideCam.name}
                  isRecording={sideCam.status === "RECORDING"}
                  syncStatus={sideCam.config_sync_status}
                />
              ) : (
                <TilePlaceholder label="No side camera connected" />
              )}
            </div>
          </div>

          {/* Bottom row: selectable microphone stream */}
          <div className="loom-cameras-bottom">
            <div className="loom-tile-wrap loom-tile-mic">
              <TileSelector modules={mics} selectedIndex={safeMicIdx} onSelect={setMicIdx} />
              {mic ? (
                <StreamTile
                  key={mic.id}
                  ip={mic.ip}
                  port={MIC_PORT}
                  label={mic.name}
                  isRecording={mic.status === "RECORDING"}
                  syncStatus={mic.config_sync_status}
                />
              ) : (
                <TilePlaceholder label="No microphone connected" />
              )}
            </div>
          </div>

        </div>

        {/* Right panel */}
        <div className="loom-dashboard-panel">
          <HealthSummaryWidget />
          <ModuleList modules={moduleList} />
          <LoomStimulusControl />
        </div>
      </div>
    </div>
  );
}

export default LoomDashboard;
