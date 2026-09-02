import React, { useState } from "react";
import { captureLivestreamSnapshot } from "/src/basic/utils/snapshot";
import "./SnapshotButton.css";

/**
 * Round overlay button that saves a still frame from any module's live MJPEG
 * preview (module's /snapshot.jpg -> Blob download). Drop it inside any
 * position: relative stream container; add the class `snapshot-hover-parent`
 * to that container to have the button reveal on hover.
 *
 * @param {{ip: string, id?: string, name?: string}} module
 * @param {number} [port] stream port (defaults to 8080, the camera port)
 * @param {string} [className] extra class for per-card positioning
 */
function SnapshotButton({ module, port, className = "" }) {
  const [snapping, setSnapping] = useState(false);

  const handleClick = async (e) => {
    e.stopPropagation();          // don't trigger the card's fullscreen click
    if (snapping || !module?.ip) return;
    setSnapping(true);
    try {
      await captureLivestreamSnapshot(module, { port });
    } catch (err) {
      console.error("Livestream snapshot failed:", err);
      window.alert("Could not take a screenshot — is the stream live?");
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
      title="Take a screenshot"
      aria-label="Take a screenshot of the stream"
    >
      {snapping ? (
        <span className="snapshot-button__spinner" aria-hidden="true">…</span>
      ) : (
        // "screenshot" glyph: corner brackets framing a centre focus ring.
        <svg
          className="snapshot-button__icon"
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M3 8V6a3 3 0 0 1 3-3h2" />
          <path d="M16 3h2a3 3 0 0 1 3 3v2" />
          <path d="M21 16v2a3 3 0 0 1-3 3h-2" />
          <path d="M8 21H6a3 3 0 0 1-3-3v-2" />
          <circle cx="12" cy="12" r="3.25" />
        </svg>
      )}
    </button>
  );
}

export default SnapshotButton;
