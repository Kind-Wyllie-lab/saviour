import React, { useState, useEffect } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./LivestreamCard.css";

function LivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen
  const [lastFrameTime, setLastFrameTime] = useState(Date.now());
  const [streamKey, setStreamKey] = useState(Date.now());

  return (
    <>
      <div className="livestream-card card">
        {/* <div className="stream-card-header">
          <h3>{module.name}</h3>
          <span className="stream-ip">{module.ip}</span>
        </div> */}

        <div className="stream-content">
          <div className="stream-video">
            <img
              src={`http://${module.ip}:8080/video_feed`}
              alt={`Stream for ${module.id}`}
              onLoad={() => setLastFrameTime(Date.now())}
              onError={(e) => {
                console.log("Stream error, forcing reconnect");
                setStreamKey(Date.now());
              }}
              onClick={() => setFullscreen(true)}
            />
          </div>
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
