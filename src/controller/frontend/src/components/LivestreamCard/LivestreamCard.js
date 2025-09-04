import React, { useState } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

function LivestreamCard({ module }) {
  const [showStream, setShowStream] = useState(true); // Show placeholder vs stream
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen

  return (
    <>
      <div className="livestream-card">
        <div className="stream-card-header">
          <h3>{module.id}</h3>
          <span className="stream-ip">{module.ip}</span>
        </div>

        <div className="stream-content">
          {showStream ? (
            <div className="stream-video">
              <img
                src={`http://${module.ip}:8080/video_feed`}
                alt={`Stream for ${module.id}`}
                onError={() => setShowStream(false)}
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
