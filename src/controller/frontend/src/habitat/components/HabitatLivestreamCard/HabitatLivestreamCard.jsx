import React, { useState, useEffect } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import SnapshotButton from "/src/basic/components/SnapshotButton/SnapshotButton";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";
import "./HabitatLivestreamCard.css";

function HabitatLivestreamCard({ module }) {
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen

  return (
    <>
      <div className="habitat-livestream-card">
        <div className="habitat-stream-video">
          {module.status === "RECORDING" && (
            <div
              className="recording-indicator"
              title="Recording"
            />
          )}
          
          { module.status === "OFFLINE" ? (
            <div className="offline-livestream">
              <p>{module.name} OFFLINE</p>
            </div>
          ) : (
            <>
              <img
                src={videoFeedUrl(module)}
                alt={`Stream for ${module.name}`}
                onClick={() => setFullscreen(true)}
              />
              <SnapshotButton module={module} />
            </>
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

export default HabitatLivestreamCard;
