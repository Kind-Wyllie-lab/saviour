import { useMemo, useState } from "react";
import { groupModulesByGroup, resolveTargetModules, isModuleReady } from "../../pages/Recording/targetModules";
import "./ReadinessSummary.css";

// Distinct from ModuleList (full fleet table: name/status/group/IP/version,
// Update All/Reboot All) -- this is scoped to whatever target is currently
// selected in NewSessionForm, and answers one question: "is what I'm about
// to start actually ready?" A healthy group of any size collapses to one
// line; only a group with something wrong expands automatically, so a
// 20-camera habitat deployment where everything's fine stays a single row
// rather than 20.
const STATUS_META = {
  READY:     { label: "Ready",     className: "readiness-summary__dot--ready" },
  RECORDING: { label: "Recording", className: "readiness-summary__dot--recording" },
  NOT_READY: { label: "Not ready", className: "readiness-summary__dot--not-ready" },
  WAITING:   { label: "Waiting",   className: "readiness-summary__dot--waiting" },
  FAULT:     { label: "Fault",     className: "readiness-summary__dot--fault" },
  OFFLINE:   { label: "Offline",   className: "readiness-summary__dot--offline" },
};

function moduleStatusMeta(module) {
  if (module.online === false) return STATUS_META.OFFLINE;
  return STATUS_META[module.status] ?? STATUS_META.WAITING;
}

export default function ReadinessSummary({ modules, target }) {
  // Per-group manual open/closed override -- undefined means "not touched
  // yet, use the health-based default" (see isOpen below).
  const [manuallyToggled, setManuallyToggled] = useState({});

  const groups = useMemo(() => groupModulesByGroup(modules), [modules]);
  const targetModules = useMemo(
    () => resolveTargetModules(modules, target, groups),
    [target, modules, groups]
  );

  const byGroup = useMemo(() => {
    const map = new Map();
    targetModules.forEach((m) => {
      const key = m.group || "No group";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(m);
    });
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [targetModules]);

  if (targetModules.length === 0) {
    return <p className="readiness-summary__empty">No modules match this target.</p>;
  }

  return (
    <div className="readiness-summary">
      <h3 className="readiness-summary__title">Module Readiness</h3>
      {byGroup.map(([groupName, groupModules]) => {
        const readyCount = groupModules.filter(isModuleReady).length;
        const allReady = readyCount === groupModules.length;
        const isOpen = manuallyToggled[groupName] ?? !allReady;

        return (
          <div key={groupName} className="readiness-summary__group">
            <button
              type="button"
              className={`readiness-summary__group-header ${allReady ? "readiness-summary__group-header--ok" : "readiness-summary__group-header--warn"}`}
              onClick={() => setManuallyToggled((prev) => ({ ...prev, [groupName]: !isOpen }))}
            >
              <span className="readiness-summary__group-marker">{allReady ? "✓" : "⚠"}</span>
              <span className="readiness-summary__group-name">{groupName}</span>
              <span className="readiness-summary__group-count">{readyCount}/{groupModules.length} ready</span>
              <span className="readiness-summary__group-toggle">{isOpen ? "▲" : "▼"}</span>
            </button>
            {isOpen && (
              <div className="readiness-summary__rows">
                {groupModules.map((m) => {
                  const meta = moduleStatusMeta(m);
                  return (
                    <div key={m.id} className="readiness-summary__row">
                      <span className={`readiness-summary__dot ${meta.className}`} />
                      <span className="readiness-summary__row-name" title={m.id}>{m.name || m.id}</span>
                      <span className="readiness-summary__row-status">{meta.label}</span>
                      {m.status !== "READY" && m.ready_message && (
                        <span className="readiness-summary__row-message" title={m.ready_message}>
                          {m.ready_message}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
