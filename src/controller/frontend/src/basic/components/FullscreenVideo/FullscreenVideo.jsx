import React, { useEffect } from "react";
import "./FullscreenVideo.css";
import SnapshotButton from "../SnapshotButton/SnapshotButton";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";

function FullscreenVideo({ ip, port = 8080, onClose }) {
  // Handle ESC key to close fullscreen
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="fullscreen-overlay">
      <div className="video-panel snapshot-hover-parent">
        {/* Video fills the overlay */}
        <img
          src={videoFeedUrl(ip, { port })}
          alt="Fullscreen camera stream"
          className="fullscreen-video"
        />
        {ip && (
          <SnapshotButton
            module={{ ip }}
            port={port}
            className="fullscreen-snapshot-button"
          />
        )}
        {/* Close button in top-right corner */}
        <button className="fullscreen-close-btn" onClick={onClose}>
          ✕
        </button>
      </div>
    </div>
  );
}

export default FullscreenVideo;
