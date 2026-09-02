// Save a still frame from a camera module's live MJPEG preview.
//
// The module already serves GET http://<ip>:8080/snapshot.jpg -- the exact
// bytes the stream is currently pushing, overlays (timestamp / fps / motion)
// baked in, at preview resolution. We fetch it into a Blob and download that:
// a plain <a href="http://<module-ip>:8080/..." download> ignores the
// `download` attribute cross-origin and just navigates, whereas a blob: URL
// is same-origin so the filename is honoured and it's a one-click save.
//
// Requires Access-Control-Allow-Origin on /snapshot.jpg (set in
// camera_base.py) so fetch() can read the response cross-origin.

/**
 * @param {{ip: string, id?: string, name?: string}} module
 * @returns {Promise<void>} resolves once the download has been triggered
 */
export async function captureLivestreamSnapshot(module) {
  if (!module?.ip) throw new Error("No module IP for snapshot");

  const res = await fetch(`http://${module.ip}:8080/snapshot.jpg?t=${Date.now()}`);
  if (!res.ok) {
    throw new Error(`Snapshot failed (${res.status} ${res.statusText})`);
  }
  const blob = await res.blob();

  const label = (module.id || module.name || "camera").replace(/[^\w.-]+/g, "_");
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
