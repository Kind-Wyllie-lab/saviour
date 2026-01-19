import React, { useState, useEffect } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

function LivestreamCard({ module }) {
  const [showStream, setShowStream] = useState(true); // Show placeholder vs stream
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen
  const [lastFrameTime, setLastFrameTime] = useState(Date.now());
  const [streamKey, setStreamKey] = useState(Date.now());

  // heartbeat watchdog
/*
  useEffect(() => {
    const interval = setInterval(() => {
      if (Date.now() - lastFrameTime > 5000) {
        console.log("Stream frozen, forcing reconnect...");
        setStreamKey(Date.now()); // refresh stream src with new query param
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [lastFrameTime]);
*/
  return (
    <>
      <div className="livestream-card">
        <div className="stream-card-header">
          <h3>{module.name}</h3>
          <span className="stream-ip">{module.ip}</span>
        </div>

        <div className="stream-content">
          {showStream ? (
            <div className="stream-video">
              <img
                src={`http://${module.ip}:8080/video_feed`}
                alt={`Stream for ${module.id}`}
                onLoad={() => setLastFrameTime(Date.now())}
                onError={(e) => {
                  console.log("Stream error, forcing reconnect");
                  setStreamKey(Date.now());
                }}
              />
              <div className="stream-controls">
                <button onClick={() => setShowStream(false)}>Hide Stream</button>
                <button onClick={() => setFullscreen(true)}>⛶ Fullscreen</button>
              </div>
            </div>
          ) : (
            <div className="stream-placeholder">
              <p>Camera Stream</p>
              <button onClick={() => setShowStream(true)}>Show Stream</button>
            </div>
          )}
        </div>
      </div>



      {/* Conditional fullscreen overlay */}
      {fullscreen && (
        <FullscreenVideo ip={module.ip} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default LivestreamCard;
