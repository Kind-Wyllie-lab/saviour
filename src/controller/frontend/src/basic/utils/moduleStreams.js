import { applicationLabel, roleLabel, DEFAULT_APPLICATION } from "/src/basic/utils/ttlApplications";

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

// The few settings worth checking next to a module's preview (Quick setup),
// as [{label, value}]. Reads the module's config (`m.config`); missing keys
// are left out.
const SYNC_MODE = { server: "Server", client: "Client", none: "Off" };

function khz(hz) {
  const n = Number(hz);
  return Number.isFinite(n) ? `${n % 1000 ? (n / 1000).toFixed(1) : n / 1000} kHz` : null;
}

export function moduleSettingsSummary(m) {
  const c = m?.config ?? {};
  const out = [];
  const add = (label, value) => {
    if (value !== null && value !== undefined && value !== "") out.push({ label, value: String(value) });
  };
  if (m?.type?.includes("camera") && c.camera) {
    const cam = c.camera;
    add("Resolution", cam.width && cam.height ? `${cam.width} × ${cam.height}` : null);
    add("Frame rate", cam.fps ? `${cam.fps} fps` : null);
    add("Exposure", cam.manual_exposure ? `Manual, ${Math.round((cam.exposure_time ?? 0) / 100) / 10} ms` : "Auto");
    add("Gain", cam.gain);
    if (cam.monochrome) add("Colour", "Monochrome");
    if (cam.rotation) add("Rotation", `${cam.rotation}°`);
    if (cam.hflip || cam.vflip) add("Flip", [cam.hflip && "horizontal", cam.vflip && "vertical"].filter(Boolean).join(" + "));
    if (cam.crop_rect) add("Crop", "On");
    add("Frame sync", SYNC_MODE[cam.sync_mode] ?? cam.sync_mode);
  } else if (m?.type === "basler_camera" && c.basler) {
    const b = c.basler;
    add("Resolution", b.width && b.height ? `${b.width} × ${b.height}` : null);
    add("Frame rate", b.fps ? `${b.fps} fps` : null);
    add("Pixel format", b.pixel_format);
    add("Exposure", b.exposure_auto === "Off" ? `${b.exposure_time_us} µs` : b.exposure_auto);
  } else if (m?.type === "microphone") {
    const am = c.audiomoth ?? {};
    add("Sample rate", khz(am.sample_rate ?? c.microphone?._sample_rate));
    add("Gain", am.gain);
    if (am.filter_type && am.filter_type !== "none") {
      add("Filter", `${am.filter_type} ${khz(am.filter_lo_hz) ?? ""}–${khz(am.filter_hi_hz) ?? ""}`.trim());
    } else {
      add("Filter", "None");
    }
    const labels = Object.keys(c.audiomoth_labels ?? {}).length;
    if (labels) add("AudioMoths labelled", labels);
  } else if (m?.type === "ttl" && c.ttl) {
    const pins = Object.entries(c.ttl.pins ?? {});
    add("Used for", applicationLabel(c.ttl.application || DEFAULT_APPLICATION));
    add("Pins", pins.length
      ? pins.map(([g, p]) => `GPIO ${g} (${p.role ? roleLabel(p.role) : p.mode ?? "?"})`).join(", ")
      : "None");
  } else if (m?.type === "rfid" && c.rfid) {
    add("Reader", c.rfid.mock?.enabled ? "Simulated bus" : (c.rfid.serial_port || "Auto-detect"));
    add("Baud", c.rfid.baud);
    add("Presence", c.rfid.presence?.enabled ? "On" : "Off");
  } else if (m?.type === "sound" && c.sound) {
    add("Volume", c.sound.volume);
    add("Duration", c.sound.duration != null ? `${c.sound.duration} s` : null);
  }
  return out;
}
