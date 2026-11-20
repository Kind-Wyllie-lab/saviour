// Which live preview a module serves (MJPEG port + label). Shared by the
// dashboard and Quick setup (plans/rig-quick-setup.md).
export const STREAM_PORTS = {
  camera:     8080,
  microphone: 8081,
  ttl:        8082,
  rfid:       8083,
};

// Stream port + display label for a module, or null if it has no stream.
export function streamInfo(m) {
  if (m.type?.includes("camera")) {
    return { port: STREAM_PORTS.camera, label: m.name, isCamera: true };
  }
  if (m.type === "microphone") {
    return { port: STREAM_PORTS.microphone, label: `${m.name} - Audio` };
  }
  if (m.type === "ttl") {
    return { port: STREAM_PORTS.ttl, label: `${m.name} - TTL` };
  }
  if (m.type === "rfid") {
    return {
      port: m.config?.monitoring?._port ?? STREAM_PORTS.rfid,
      label: `${m.name} - RFID`,
    };
  }
  return null;
}
export const isStreamModule = (m) => streamInfo(m) != null;
