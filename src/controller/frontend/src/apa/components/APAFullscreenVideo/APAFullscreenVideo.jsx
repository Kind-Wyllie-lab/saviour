import React, { useEffect } from "react";
import "./APAFullscreenVideo.css";
import APACommands from "../APACommands/APACommands";
import SnapshotButton from "/src/basic/components/SnapshotButton/SnapshotButton";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";

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
        {/* Wrapper anchors the screenshot button to the video without making
            .video-panel positioned (that would re-anchor the close button). */}
        <div className="fullscreen-video-wrap snapshot-hover-parent">
          <img
            src={videoFeedUrl(ip)}
            alt="Fullscreen camera stream"
            className="fullscreen-video"
          />
          {ip && (
            <SnapshotButton
              module={{ ip }}
              className="fullscreen-snapshot-button"
            />
          )}
        </div>
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
