import React, { useState } from "react";
import { captureLivestreamSnapshot } from "/src/basic/utils/snapshot";
import "./SnapshotButton.css";

/**
 * Round overlay button that saves a still frame from a camera module's live
 * preview (module's /snapshot.jpg -> Blob download). Drop it inside any
 * position: relative stream container.
 *
 * @param {{ip: string, id?: string, name?: string}} module
 * @param {string} [className] extra class for per-card positioning
 */
function SnapshotButton({ module, className = "" }) {
  const [snapping, setSnapping] = useState(false);

  const handleClick = async (e) => {
    e.stopPropagation();          // don't trigger the card's fullscreen click
    if (snapping || !module?.ip) return;
    setSnapping(true);
    try {
      await captureLivestreamSnapshot(module);
    } catch (err) {
      console.error("Livestream snapshot failed:", err);
      window.alert("Could not take a picture — is the stream live?");
    } finally {
      setSnapping(false);
    }
  };

  return (
    <button
      type="button"
      className={`snapshot-button ${className}`.trim()}
      onClick={handleClick}
      disabled={snapping}
      title="Take a picture"
      aria-label="Take a picture of the livestream"
    >
      {snapping ? "…" : "📷"}
    </button>
  );
}

export default SnapshotButton;
