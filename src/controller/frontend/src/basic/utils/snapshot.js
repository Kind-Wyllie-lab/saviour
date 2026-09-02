// Save a still frame from any module's live MJPEG preview.
//
// Every module type that serves a monitoring stream (camera preview,
// microphone spectrogram, TTL trace, RFID reads) also serves
// GET http://<ip>:<port>/snapshot.jpg -- the exact bytes the stream is
// currently showing, overlays baked in, at preview resolution. We fetch it
// into a Blob and download that: a plain
// <a href="http://<module-ip>:<port>/..." download> ignores the `download`
// attribute cross-origin and just navigates, whereas a blob: URL is
// same-origin so the filename is honoured and it's a one-click save.
//
// Requires Access-Control-Allow-Origin on /snapshot.jpg (set in
// mjpeg_stream.py) so fetch() can read the response cross-origin.

import { snapshotUrl } from "./streamUrls";

/**
 * @param {{ip: string, id?: string, name?: string}} module
 * @param {{port?: number}} [opts] stream port (defaults to 8080, the camera port)
 * @returns {Promise<void>} resolves once the download has been triggered
 */
export async function captureLivestreamSnapshot(module, { port } = {}) {
  if (!module?.ip) throw new Error("No module IP for snapshot");

  const res = await fetch(snapshotUrl(module, { port }));
  if (!res.ok) {
    throw new Error(`Snapshot failed (${res.status} ${res.statusText})`);
  }
  const blob = await res.blob();

  const label = (module.id || module.name || "stream").replace(/[^\w.-]+/g, "_");
  const ts = new Date().toISOString().replace(/[:.]/g, "-").replace("T", "_").slice(0, 19);
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = `${label}_${ts}.jpg`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Revoke on the next tick so the click has been processed.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
