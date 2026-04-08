import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./MJPEGStreamCard.css";

const STALL_TIMEOUT_MS = 8000;
const RECONNECT_DELAY_MS = 2500;

/**
 * Generic MJPEG stream card.
 * Handles stall detection and reconnection for any module that serves
 * an MJPEG stream at http://{ip}:{port}/video_feed.
 */
function MJPEGStreamCard({ ip, port = 8080, label }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const stallTimer = useRef(null);
  const reconnectTimer = useRef(null);

  const bump = () => setStreamKey(Date.now());

  const resetStallTimer = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_TIMEOUT_MS);
  };

  useEffect(() => {
    resetStallTimer();
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_DELAY_MS);
  };

  return (
    <>
      <div className="mjpeg-stream-card card">
        {label && (
          <div className="mjpeg-stream-header">
            <span className="mjpeg-stream-label">{label}</span>
          </div>
        )}
        <div className="mjpeg-stream-video">
          <img
            key={streamKey}
            src={`http://${ip}:${port}/video_feed`}
            alt={label || "stream"}
            onLoad={resetStallTimer}
            onError={handleError}
            onClick={() => setFullscreen(true)}
          />
        </div>
      </div>

      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default MJPEGStreamCard;
