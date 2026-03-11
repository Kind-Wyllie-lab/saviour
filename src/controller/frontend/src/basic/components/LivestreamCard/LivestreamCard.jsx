import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

const STALL_TIMEOUT_MS = 8000;  // reconnect if no frame arrives within this window
const RECONNECT_DELAY_MS = 2500; // wait after an error before retrying (stream needs time to restart)

function LivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const stallTimer = useRef(null);
  const reconnectTimer = useRef(null);

  const bump = () => setStreamKey(Date.now());

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

  const handleLoad = () => resetStallTimer();

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
              src={`http://${module.ip}:8080/video_feed`}
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
