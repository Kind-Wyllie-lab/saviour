import React, { useEffect } from "react";
import "./FullscreenVideo.css";
import APACommands from "../../components/APACommands/APACommands";

function FullscreenVideo({ ip, moduleList, onClose }) {
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
  );
}

export default FullscreenVideo;
