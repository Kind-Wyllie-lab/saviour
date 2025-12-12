import React, { useEffect } from "react";
import "./APAFullscreenVideo.css";
import APACommands from "../APACommands/APACommands";

function APAFullscreenVideo({ ip, moduleList, onClose }) {
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
          src={`http://${ip}:8080/video_feed`}
          alt="Fullscreen camera stream"
          className="fullscreen-video"
        />
        {/* Close button in top-right corner */}
        <button className="fullscreen-close-btn" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="commands-panel">
        {/* <p>Hi</p> */}
        <APACommands modules={moduleList} />
      </div>
    </div>
  );
}

export default APAFullscreenVideo;
