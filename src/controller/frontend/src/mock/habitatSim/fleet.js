// The simulated habitat's modules for the habitat mock build: a camera per
// box (named after it), a microphone module per column (named after it) with
// an AudioMoth per box labelled with that box, the RFID gate bus. The DMX
// lighting module comes from lightingMock.js.

import { params } from "./index";
import { simLayout } from "./model";

const layout = simLayout(params);
export const BOXES = layout.boxes.map((b) => b.id);
export const COLUMNS = [...new Set(BOXES.map((b) => b[0]))];

export const RFID_ID = "rfid-module-mock";
export const cameraId = (box) => `habitat_camera_${box.toLowerCase()}`;
export const micId = (col) => `microphone-mock-${col.toLowerCase()}`;
export const mothSerial = (box) => `24E1440${box}`;
// One AudioMoth on D with no box label (Placement / live map show it).
export const STRAY_MOTH = { module: "D", serial: "24E1440XYZ" };

const base = (id, name, type, ip, group) => ({
  id, name, type, ip, status: "READY", online: true, ready: true, checks: {}, group,
  version: "v0.6.1-mock", config_sync_status: "SYNCED",
});

function micConfig(col) {
  const boxes = BOXES.filter((b) => b[0] === col);
  return {
    module: { name: col, group: "microphones" },
    audiomoth: { sample_rate: 192000, gain: 2, filter_type: "none" },
    monitoring: { enabled: true, freq_lo_hz: 15000, freq_hi_hz: 96000, time_window_s: 3,
      peak_hold_s: 2, plot_mode: "spectrogram", freq_range: "full", layout: "stacked",
      colormap: "inferno", colormap_stops: [], colour_mode: "bands", call_bands: [] },
    audiomoth_labels: Object.fromEntries(boxes.map((b) => [mothSerial(b), b])),
  };
}

export function habitatModules() {
  const out = {};
  BOXES.forEach((box, i) => {
    out[cameraId(box)] = { ...base(cameraId(box), box, "habitat_camera", `10.0.0.${101 + i}`, "cameras"),
      checks: { _check_picam: [true, "imx708 present"], _check_ptp: [true, "PTP locked"] } };
  });
  COLUMNS.forEach((col, i) => {
    out[micId(col)] = { ...base(micId(col), col, "microphone", `10.0.0.${131 + i}`, "microphones"),
      config: micConfig(col) };
  });
  out[RFID_ID] = base(RFID_ID, "Gate bus", "rfid", "10.0.0.150", "rfid");
  return out;
}

// ModuleHealthSnapshot-ish entries for the modules above.
export function habitatHealth() {
  const t = Date.now() / 1000;
  const out = {};
  Object.values(habitatModules()).forEach((m, i) => {
    const cam = m.type === "habitat_camera";
    out[m.id] = {
      cpu_usage: (cam ? 30 : 12) + (i * 7) % 15, cpu_temp: 45 + (i * 5) % 12, memory_usage: 25 + (i * 3) % 20,
      memory_total_gb: 8, disk_space: 20 + (i * 11) % 50, disk_used_gb: 30 + (i * 13) % 50, disk_total_gb: 118,
      ptp: 0.0000012, phc: 0.0000009, ptp4l_offset_ns: 800 + (i * 137) % 1500, phc2sys_offset_ns: 600 + (i * 91) % 900,
      status: "online", recording: false, hardware_fault: null, throttled: 0, last_heartbeat: t,
      rec_bytes_per_s: null, audio_clip_pct: m.type === "microphone" ? 0 : null,
      frame_clip_pct: cam ? 0 : null, version: "v0.6.1-mock",
    };
  });
  return out;
}
