import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./MJPEGStreamCard.css";

const STALL_TIMEOUT_MS = 8000;
const RECONNECT_DELAY_MS = 2500;
// Floor between actual reconnects, regardless of how many things ask for one.
// Each bump() fully unmounts/remounts the <img>, opening a fresh long-lived
// MJPEG connection — a burst of triggers (e.g. several rapid config saves,
// each restarting the camera and separately tripping the stall/error/config-
// sync handlers) can otherwise open more concurrent connections to the same
// host than the browser allows, leaving the newest one stuck pending forever
// with neither onLoad nor onError ever firing to recover it.
const MIN_RECONNECT_MS = 3000;

/**
 * Generic MJPEG stream card.
 * Handles stall detection and reconnection for any module that serves
 * an MJPEG stream at http://{ip}:{port}/video_feed.
 */
function MJPEGStreamCard({ ip, port = 8080, label, isRecording = false }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  const stallTimer = useRef(null);
  const reconnectTimer = useRef(null);
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

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_TIMEOUT_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
      clearTimeout(pendingBumpTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  // Clear only, don't re-arm — Chrome fires onLoad once for a
  // multipart/x-mixed-replace (MJPEG) stream, on the first frame only, and
  // never again for subsequent frames. Rescheduling here would fire the
  // timer forever every STALL_TIMEOUT_MS regardless of stream health,
  // forcing a full reconnect (and killing the live video) on a healthy
  // stream. Recovery from a stream that actually dies is handled by onError.
  const handleLoad = () => clearTimeout(stallTimer.current);

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
            onLoad={handleLoad}
            onError={handleError}
            onClick={() => setFullscreen(true)}
          />
          {isRecording && <span className="mjpeg-rec-dot" title="Recording" />}
        </div>
      </div>

      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default MJPEGStreamCard;
