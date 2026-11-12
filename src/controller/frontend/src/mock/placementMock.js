// Mock-mode stand-in for the habitat placement service
// (variants/habitat/placement.py), for the dashboard's Placement panel.
// The simulated habitat's modules (habitatSim/fleet.js): every camera in
// its box, every microphone module on its column, an AudioMoth per box
// labelled with it, plus one unlabelled AudioMoth.

import { BOXES as SIM_BOXES, COLUMNS, STRAY_MOTH, cameraId, micId, mothSerial } from "./habitatSim/fleet";

const BOXES = [...SIM_BOXES].sort((a, b) => a[1] - b[1] || a.localeCompare(b));

let p = null;
const init = () => ({
  cameras: Object.fromEntries(SIM_BOXES.map((b) => [cameraId(b), b])),
  mics: Object.fromEntries(COLUMNS.map((c) => [micId(c), c])),
  moths: Object.fromEntries(COLUMNS.map((col) => [
    micId(col),
    Object.fromEntries([
      ...SIM_BOXES.filter((b) => b[0] === col).map((b) => [mothSerial(b), b]),
      ...(col === STRAY_MOTH.module ? [[STRAY_MOTH.serial, ""]] : []),
    ]),
  ])),
  changes: [],
});

function state() {
  p ??= init();
  const box = (v) => (BOXES.includes(v) ? v : null);
  const cameras = Object.entries(p.cameras).map(([id, name]) => (
    { module_id: id, name: name || id, box: box(name), online: true, recording: false }));
  const columns = COLUMNS;
  const mics = Object.entries(p.mics).map(([id, name]) => (
    { module_id: id, name, column: columns.includes(name) ? name : null, online: true, recording: false }));
  const colOf = Object.fromEntries(mics.map((m) => [m.module_id, m.column]));
  const audiomoths = Object.entries(p.moths).flatMap(([mid, labels]) =>
    Object.entries(labels).map(([serial, label]) => (
      { module_id: mid, serial, label, box: box(label), column: colOf[mid], live: true, online: true })));
  const boxes = BOXES.map((b) => ({
    id: b,
    cameras: cameras.filter((c) => c.box === b).map((c) => c.module_id),
    audiomoths: audiomoths.filter((a) => a.box === b).map((a) => a.serial),
  }));
  return { boxes, columns, cameras, mics, audiomoths, problems: [], changes: p.changes,
    empty: { cameras: boxes.filter((b) => !b.cameras.length).map((b) => b.id),
      audiomoths: boxes.filter((b) => !b.audiomoths.length).map((b) => b.id) } };
}

function apply(assignments, reason) {
  const changes = [];
  for (const a of assignments) {
    const box = a.box || "";
    if (a.kind === "mic") {
      const col = a.column || "";
      for (const [id, name] of Object.entries(p.mics)) {
        if (col && name === col && id !== a.module_id) { p.mics[id] = ""; changes.push({ kind: "mic", module_id: id, serial: null, before: col, after: null }); }
      }
      changes.push({ kind: "mic", module_id: a.module_id, serial: null, before: p.mics[a.module_id] || null, after: col || null });
      p.mics[a.module_id] = col;
    } else if (a.kind === "camera") {
      for (const [id, name] of Object.entries(p.cameras)) {
        if (box && name === box && id !== a.module_id) { p.cameras[id] = ""; changes.push({ kind: "camera", module_id: id, serial: null, before: box, after: null }); }
      }
      changes.push({ kind: "camera", module_id: a.module_id, serial: null, before: p.cameras[a.module_id] || null, after: box || null });
      p.cameras[a.module_id] = box;
    } else {
      for (const [mid, labels] of Object.entries(p.moths)) {
        for (const [s, l] of Object.entries(labels)) {
          if (box && l === box && s !== a.serial) { labels[s] = ""; changes.push({ kind: "audiomoth", module_id: mid, serial: s, before: box, after: null }); }
        }
      }
      changes.push({ kind: "audiomoth", module_id: a.module_id, serial: a.serial, before: p.moths[a.module_id][a.serial] || null, after: box || null });
      p.moths[a.module_id][a.serial] = box;
    }
  }
  const ts = Date.now() / 1000;
  p.changes = [...changes.map((c) => ({ ...c, ts, reason, operator: "127.0.0.1" })), ...p.changes].slice(0, 20);
  return changes;
}

function parse(text) {
  const s = state();
  const assignments = [];
  const errors = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.split("#")[0].trim();
    if (!line) return;
    const [key, b] = line.split(/[,\t;]|\s+/).filter(Boolean);
    const mic = s.mics.find((m) => m.module_id.toUpperCase() === key.toUpperCase());
    if (mic) { assignments.push({ kind: "mic", module_id: mic.module_id, column: (b || "").toUpperCase() }); return; }
    const box = (b || "").toUpperCase();
    if (!BOXES.includes(box)) { if (i > 0) errors.push(`Line ${i + 1}: '${b}' is not a box on the layout`); return; }
    const cam = s.cameras.find((c) => c.module_id.toUpperCase() === key.toUpperCase());
    const moth = s.audiomoths.find((a) => a.serial.toUpperCase() === key.toUpperCase());
    if (cam) assignments.push({ kind: "camera", module_id: cam.module_id, box });
    else if (moth) assignments.push({ kind: "audiomoth", module_id: moth.module_id, serial: moth.serial, box });
    else errors.push(`Line ${i + 1}: '${key}' is not a connected camera module or a known AudioMoth serial`);
  });
  return { assignments, errors };
}

export const placementRoutes = {
  get_placement() { this._fire("placement_state", state()); },
  placement_preview_import(payload) {
    const { assignments, errors } = parse(payload?.text ?? "");
    const saved = JSON.parse(JSON.stringify(p));
    const changes = apply(assignments, "import");
    p = saved;
    this._fire("placement_preview", { assignments, changes, errors });
  },
  placement_apply(payload) {
    state();
    const changes = apply(payload?.assignments ?? [], payload?.reason === "import" ? "import" : "panel");
    this._fire("placement_applied", { changes });
    this._fire("placement_state", state());
  },
};
