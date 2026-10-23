import React, { useState } from "react";
import APAFullscreenVideo from "../APAFullscreenVideo/APAFullscreenVideo";
import SnapshotButton from "/src/basic/components/SnapshotButton/SnapshotButton";
import GridBadge from "../GridBadge/GridBadge";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";
import "./APALivestreamCard.css";

/**
 * Shown whenever the stream on screen isn't the APA camera: either a
 * different camera was picked, or there's no APA camera at all. Never
 * dismissible -- the arena view is what the operator shocks from.
 */
export function NotApaCameraFlag({ module, apaPresent }) {
  if (!module || module.type === "apa_camera") return null;
  const name = module.name || module.id;
  return (
    <div className="apa-stream-flag" role="status">
      {apaPresent
        ? `Showing ${name}, not the APA camera`
        : `No APA camera connected: showing ${name}`}
    </div>
  );
}

function APALivestreamCard({ module, cameras = [], onSelect, apaPresent, moduleList }) {
  const [showStream, setShowStream] = useState(true); // Show placeholder vs stream
  const [fullscreen, setFullscreen] = useState(false); // Track fullscreen
  const [streamKey, setStreamKey] = useState(Date.now());

  return (
    <>
      <div className="livestream-card">
        <div className="stream-card-header">
          {cameras.length > 1 && onSelect ? (
            <select
              className="apa-stream-picker"
              value={module.id}
              onChange={(e) => onSelect(e.target.value)}
              aria-label="Camera to show"
            >
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name || c.id}{c.type === "apa_camera" ? " (APA camera)" : ""}
                </option>
              ))}
            </select>
          ) : (
            <h3>{module.name || module.id}</h3>
          )}
          <span className="stream-ip">{module.ip}</span>
        </div>
        <NotApaCameraFlag module={module} apaPresent={apaPresent} />

        <div className="stream-content">
          {showStream ? (
            <div className="stream-video stream-video--clickable snapshot-hover-parent" onClick={() => setFullscreen(true)}>
              <img
                key={`${module.id}-${streamKey}`}
                src={videoFeedUrl(module, { key: streamKey })}
                alt={`Stream for ${module.id}`}
                onError={() => {
                  console.log("Stream error, forcing reconnect");
                  setStreamKey(Date.now());
                }}
              />
              <GridBadge module={module} />
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
        <APAFullscreenVideo
          module={module}
          apaPresent={apaPresent}
          moduleList={moduleList}
          onClose={() => setFullscreen(false)}
        />
      )}
    </>
  );
}

export default APALivestreamCard;
