import React, { useState, useEffect } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import "./HabitatLivestreamCard.css";

function HabitatLivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen

  return (
    <>
      <div className="livestream-card">
        <div className="stream-video">
          <img
            src={`http://${module.ip}:8080/video_feed`}
            alt={`Stream for ${module.name}`}
            onClick={() => setFullscreen(true)}
          />
        </div>
      </div>
      {/* Conditional fullscreen overlay */}
      {fullscreen && (
        <FullscreenVideo ip={module.ip} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default HabitatLivestreamCard;
