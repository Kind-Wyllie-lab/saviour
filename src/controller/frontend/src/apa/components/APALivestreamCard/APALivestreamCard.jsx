import React, { useState } from "react";
import APAFullscreenVideo from "../APAFullscreenVideo/APAFullscreenVideo";
import SnapshotButton from "/src/basic/components/SnapshotButton/SnapshotButton";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";
import "./APALivestreamCard.css";

function APALivestreamCard({ module, moduleList }) {
  const [showStream, setShowStream] = useState(true); // Show placeholder vs stream
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen
  const [streamKey, setStreamKey] = useState(Date.now());

  return (
    <>
      <div className="livestream-card">
        <div className="stream-card-header">
          <h3>{module.id}</h3>
          <span className="stream-ip">{module.ip}</span>
        </div>

        <div className="stream-content">
          {showStream ? (
            <div className="stream-video stream-video--clickable" onClick={() => setFullscreen(true)}>
              <img
                key={streamKey}
                src={videoFeedUrl(module, { key: streamKey })}
                alt={`Stream for ${module.id}`}
                onError={() => {
                  console.log("Stream error, forcing reconnect");
                  setStreamKey(Date.now());
                }}
              />
              <SnapshotButton module={module} />
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
        <APAFullscreenVideo ip={module.ip} moduleList={moduleList} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default APALivestreamCard;