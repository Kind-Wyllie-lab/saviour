import React, { useEffect } from "react";
import "./FullscreenVideo.css";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";

function FullscreenVideo({ ip, onClose }) {
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
      <div className="video-panel">
        {/* Video fills the overlay */}
        <img
          src={videoFeedUrl(ip)}
          alt="Fullscreen camera stream"
          className="fullscreen-video"
        />
        {/* Close button in top-right corner */}
        <button className="fullscreen-close-btn" onClick={onClose}>
          ✕
        </button>
      </div>
    </div>
  );
}

export default FullscreenVideo;
