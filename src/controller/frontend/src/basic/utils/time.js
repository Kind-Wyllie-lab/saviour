// Times in SAVIOUR are UTC, written with an explicit Z (CHANGELOG 2026-11-20,
// src/shared/timestamps.py). Everything shown goes through here so it reads
// in UTC whatever the browser's zone. Local-time intentions (habitat
// recording windows, the lighting day/night schedule) don't use these.

const COMPACT = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(Z?)$/;

// A stamp ("20261120-143000Z", "2026-11-20T14:30:00Z", legacy zone-less
// forms), epoch seconds (number) or a Date, as a Date; null if unreadable.
// Legacy zone-less stamps were the controller's local time (Europe/London);
// they're read as the browser's local time, as before.
export function parseStamp(value) {
  if (value == null || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") return new Date(value * 1000);
  const s = String(value).trim();
  const m = s.match(COMPACT);
  if (m) {
    const [y, mo, d, h, mi, se] = m.slice(1, 7).map(Number);
    return m[7] ? new Date(Date.UTC(y, mo - 1, d, h, mi, se)) : new Date(y, mo - 1, d, h, mi, se);
  }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// "14:30" / "14:30:05" in UTC. `utc`: append " UTC".
export function formatUtcTime(value, { seconds = false, utc = false } = {}) {
  const d = parseStamp(value);
  if (!d) return "–";
  const t = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${seconds ? `:${pad(d.getUTCSeconds())}` : ""}`;
  return utc ? `${t} UTC` : t;
}

// "20 Nov 14:30 UTC" (`year`: "20 Nov 2026 14:30 UTC"; `seconds`; `utc`
// false drops the suffix, e.g. under a "UTC" column header).
export function formatUtc(value, { seconds = false, year = false, utc = true } = {}) {
  const d = parseStamp(value);
  if (!d) return "–";
  const day = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${year ? ` ${d.getUTCFullYear()}` : ""}`;
  return `${day} ${formatUtcTime(d, { seconds, utc })}`;
}

// "20 Nov 2026" (UTC).
export function formatUtcDate(value, { weekday = false, year = true } = {}) {
  const d = parseStamp(value);
  if (!d) return "–";
  return `${weekday ? `${DAYS[d.getUTCDay()]} ` : ""}${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${year ? ` ${d.getUTCFullYear()}` : ""}`;
}

// "YYYY-MM-DD" (UTC), e.g. for grouping by day.
export function utcDateKey(value = new Date()) {
  const d = parseStamp(value);
  if (!d) return "";
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

// "20261120-143000Z": the compact stamp for now (session names).
export function compactUtcStamp(date = new Date()) {
  return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}-`
    + `${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`;
}
