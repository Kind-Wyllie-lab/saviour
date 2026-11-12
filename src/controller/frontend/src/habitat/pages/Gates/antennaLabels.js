// Your own names for the antennas on a simulated RFID bus (the Gates page's
// Simulated bus panel): pasted as `address, label` lines and kept in this
// browser only, per RFID module. The controller never sees them.

const STORE_PREFIX = "saviour.gates.antennaLabels.";

/** Parse `address, label` lines (commas, tabs or semicolons; spaces when
 *  there are none). Blank lines and `#` comments are skipped. Returns
 *  {labels: {address: label}, unknown: [addresses not on the bus]} or
 *  {error}. A repeated address or label is an error. */
export function parseLabels(text, busAddresses = null) {
  const labels = {};
  const seen = new Map();
  const lines = String(text ?? "").split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/#.*/, "").trim();
    if (!line) continue;
    const parts = (/[,\t;]/.test(line) ? line.split(/\s*[,\t;]\s*/) : line.split(/\s+/)).filter(Boolean);
    if (!/^\d+$/.test(parts[0] ?? "")) return { error: `Line ${i + 1}: start with the unit address (a number)` };
    const address = Number(parts[0]);
    const label = parts.slice(1).join(" ").trim();
    if (!label) return { error: `Line ${i + 1}: no label after address ${address}` };
    if (address in labels) return { error: `Address ${address} is listed twice (line ${i + 1})` };
    const key = label.toLowerCase();
    if (seen.has(key)) return { error: `"${label}" is listed twice (lines ${seen.get(key)} and ${i + 1})` };
    seen.set(key, i + 1);
    labels[address] = label;
  }
  if (!Object.keys(labels).length) return { error: "The list is empty" };
  const on = busAddresses ? new Set(busAddresses) : null;
  const unknown = on ? Object.keys(labels).map(Number).filter((a) => !on.has(a)) : [];
  return { labels, unknown };
}

export function loadLabels(moduleId) {
  try {
    const raw = window.localStorage.getItem(STORE_PREFIX + moduleId);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function saveLabels(moduleId, labels) {
  try {
    if (labels && Object.keys(labels).length) {
      window.localStorage.setItem(STORE_PREFIX + moduleId, JSON.stringify(labels));
    } else {
      window.localStorage.removeItem(STORE_PREFIX + moduleId);
    }
  } catch {
    // Private window or blocked storage: the labels last until a reload.
  }
}

export const labelsToText = (labels) => Object.entries(labels)
  .sort(([a], [b]) => Number(a) - Number(b))
  .map(([a, l]) => `${a}, ${l}`).join("\n");
