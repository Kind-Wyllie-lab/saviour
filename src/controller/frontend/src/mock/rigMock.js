// Rig check for dev:mock: a JS twin of src/controller/rig_check.py's
// evaluate(), run over the mock fleet, with each variant's shipped
// rig.expected (read straight from its <variant>_controller_config.json).
// The basic build ships none, so the mock gives it a demo profile whose
// microphone has a hardware fault (red banner).

const VARIANT_CONFIGS = import.meta.glob(
  "../../../variants/*/*_controller_config.json", { eager: true, import: "default" });

const BASIC_DEMO = {
  expected: [
    { label: "Cameras", types: ["*camera"], min: 2, severity: "required" },
    { label: "Microphone", types: ["microphone"], min: 1, severity: "required" },
    { label: "TTL", types: ["ttl"], min: 1, severity: "optional" },
    { label: "Ephys sync", types: ["ttl"], application: "ephys_sync", min: 1,
      severity: "required", when: "ephys" },
  ],
  startup_grace_s: 120,
  alert_after_s: 600,
};

export function rigDefaults(variant = import.meta.env.VITE_VARIANT || "basic") {
  const key = Object.keys(VARIANT_CONFIGS).find(
    (k) => k.includes(`/variants/${variant}/`));
  const rig = key ? VARIANT_CONFIGS[key]?.rig : null;
  return JSON.parse(JSON.stringify(rig || (variant === "basic" ? BASIC_DEMO : {})));
}

function globToRegExp(glob) {
  const esc = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${esc}$`);
}

const isOnline = (m) => m.online !== false && m.status !== "OFFLINE";
const ttlApp = (m) => (m.type === "ttl" ? (m.config?.ttl?.application || "mixed") : null);

export function evaluateRig(rig, modules, health) {
  const expected = (rig?.expected || []).filter((e) => (e.types || []).length);
  const items = expected.map((e) => {
    const res = e.types.map(globToRegExp);
    const present = [], faulty = [], offline = [];
    for (const [id, m] of Object.entries(modules || {}).sort()) {
      if (!res.some((r) => r.test(m.type || ""))) continue;
      if (e.application && ttlApp(m) !== e.application) continue;
      const who = { id, name: m.name || id };
      const fault = health?.[id]?.hardware_fault;
      if (!isOnline(m)) offline.push(who);
      else if (fault) faulty.push({ ...who, reason: fault });
      else present.push(who);
    }
    const min = Number(e.min ?? 1);
    const status = present.length >= min ? "ok" : faulty.length ? "faulty" : "short";
    return { label: e.label || e.types.join(" / "), types: e.types, min,
      severity: e.severity || "required", count: present.length, status,
      present, faulty, offline, active: !e.when,
      ...(e.application ? { application: e.application } : {}), ...(e.when ? { when: e.when } : {}) };
  });
  const bad = items.filter((i) => i.active && i.status !== "ok");
  const level = bad.some((i) => i.severity === "required") ? "error" : bad.length ? "warning" : "ok";
  const summary = bad.length
    ? bad.map((i) => `${i.label}: ${i.count} of ${i.min}`).join("; ")
    : items.length ? "All expected hardware present" : "No expected hardware set";
  return { level, ok: level === "ok", pending: false, items, summary,
    alert_after_s: rig?.alert_after_s ?? 600 };
}
