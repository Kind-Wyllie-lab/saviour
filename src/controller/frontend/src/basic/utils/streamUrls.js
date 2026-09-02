// Central place the frontend builds camera-module preview URLs.
//
// Normally these point straight at the module's own tiny Flask server
// (http://<module.ip>:8080/...), which the browser hits directly -- no
// controller proxy. In mock mode (npm run dev:mock) there are no modules, so
// we point at the dev server's /mock-media placeholder (see vite.config.js's
// mockMediaPlugin) instead of leaving broken <img>s everywhere.
//
// Accepts either a module object ({ ip, name, id }) or a bare ip string.
// `key` (a reconnect nonce) is appended as ?t= only when given, so callers
// that want a stable URL across re-renders keep one.

const MOCK = import.meta.env.VITE_MOCK === "1";

const host = (m) => (typeof m === "string" ? m : m?.ip);
const label = (m) =>
  typeof m === "string" ? m : (m?.name || m?.id || "camera");

/** Live MJPEG stream URL. */
export function videoFeedUrl(moduleOrIp, { port = 8080, key } = {}) {
  const nonce = key != null ? `t=${key}` : "";
  if (MOCK) {
    const q = `label=${encodeURIComponent(label(moduleOrIp))}${nonce ? `&${nonce}` : ""}`;
    return `/mock-media/stream.svg?${q}`;
  }
  return `http://${host(moduleOrIp)}:${port}/video_feed${nonce ? `?${nonce}` : ""}`;
}

/** Single-frame JPEG snapshot URL. */
export function snapshotUrl(moduleOrIp) {
  const t = Date.now();
  if (MOCK) {
    return `/mock-media/frame.svg?t=${t}&label=${encodeURIComponent(label(moduleOrIp))}`;
  }
  return `http://${host(moduleOrIp)}:8080/snapshot.jpg?t=${t}`;
}
