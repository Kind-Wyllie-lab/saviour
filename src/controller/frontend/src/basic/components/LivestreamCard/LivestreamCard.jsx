import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

const STALL_TIMEOUT_MS = 8000;  // reconnect if no frame arrives within this window
const RECONNECT_DELAY_MS = 2500; // wait after an error before retrying (stream needs time to restart)
// Floor between actual reconnects, regardless of how many things ask for one.
// Each bump() fully unmounts/remounts the <img>, opening a fresh long-lived
// MJPEG connection — a burst of triggers (e.g. several rapid config saves,
// each restarting the camera and separately tripping the stall/error/config-
// sync handlers) can otherwise open more concurrent connections to the same
// host than the browser allows, leaving the newest one stuck pending forever
// with neither onLoad nor onError ever firing to recover it.
const MIN_RECONNECT_MS = 3000;

function LivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const [restarting, setRestarting] = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const configTimer    = useRef(null);
  const prevStatus     = useRef(module?.config_sync_status);
  const lastBumpAt       = useRef(0);
  const pendingBumpTimer = useRef(null);

  const bump = () => {
    const now = Date.now();
    const elapsed = now - lastBumpAt.current;
    if (elapsed >= MIN_RECONNECT_MS) {
      lastBumpAt.current = now;
      setStreamKey(now);
    } else {
      clearTimeout(pendingBumpTimer.current);
      pendingBumpTimer.current = setTimeout(() => {
        lastBumpAt.current = Date.now();
        setStreamKey(Date.now());
      }, MIN_RECONNECT_MS - elapsed);
    }
  };

  // Show overlay on save (→ PENDING); bump stream on SYNCED so onLoad fires on the fresh connection.
  const syncStatus = module?.config_sync_status;
  useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = syncStatus;
    if (syncStatus === "PENDING") {
      setRestarting(true);
    } else if (prev === "PENDING" && syncStatus === "SYNCED") {
      bump();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncStatus]);

  const resetStallTimer = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_TIMEOUT_MS);
  };

  // Start stall watchdog when the stream key changes (i.e. on mount / reconnect).
  useEffect(() => {
    resetStallTimer();
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
      clearTimeout(pendingBumpTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  // Stream is live — cancel the connect watchdog so a healthy stream isn't
  // interrupted. Recovery from future errors is handled by onError.
  const handleLoad = () => { clearTimeout(stallTimer.current); setRestarting(false); };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_DELAY_MS);
  };

  return (
    <>
      <div className="livestream-card card">
        <div className="stream-content">
          <div className="stream-video">
            <img
              key={streamKey}
              src={`http://${module.ip}:8080/video_feed?t=${streamKey}`}
              alt={`Stream for ${module.id}`}
              onLoad={handleLoad}
              onError={handleError}
              onClick={() => setFullscreen(true)}
            />
            {restarting && (
              <div className="stream-restarting-overlay">
                <span>Stream restarting…</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {fullscreen && (
        <FullscreenVideo ip={module.ip} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default LivestreamCard;
