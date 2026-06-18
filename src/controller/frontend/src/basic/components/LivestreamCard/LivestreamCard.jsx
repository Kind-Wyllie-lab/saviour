import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

const STALL_TIMEOUT_MS = 8000;  // reconnect if no frame arrives within this window
const RECONNECT_DELAY_MS = 2500; // wait after an error before retrying (stream needs time to restart)

function LivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const configTimer    = useRef(null);
  const prevStatus     = useRef(module?.config_sync_status);

  const bump = () => setStreamKey(Date.now());

  // Bump stream when a save completes (PENDING → SYNCED means camera restarted).
  const syncStatus = module?.config_sync_status;
  useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = syncStatus;
    if (prev !== "PENDING" || syncStatus !== "SYNCED") return;
    clearTimeout(configTimer.current);
    configTimer.current = setTimeout(bump, 2000);
    return () => clearTimeout(configTimer.current);
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
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  // Stream is live — cancel the connect watchdog so a healthy stream isn't
  // interrupted. Recovery from future errors is handled by onError.
  const handleLoad = () => clearTimeout(stallTimer.current);

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
