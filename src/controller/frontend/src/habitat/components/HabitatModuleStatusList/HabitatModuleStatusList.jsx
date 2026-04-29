import React, { useMemo } from "react";
import "./HabitatModuleStatusList.css";

function HabitatModuleStatusList({ modules, sessions = [] }) {
  const list = Object.values(modules);

  // Module IDs that appear in any session currently in error state
  const faultedModuleIds = useMemo(() => {
    const ids = new Set();
    sessions.forEach((s) => {
      if (s.state === "error") s.modules?.forEach((id) => ids.add(id));
    });
    return ids;
  }, [sessions]);

  const sorted = [...list].sort((a, b) => {
    const ga = a.group || "";
    const gb = b.group || "";
    if (ga !== gb) {
      if (!ga) return 1;
      if (!gb) return -1;
      return ga.localeCompare(gb);
    }
    return (a.name || a.id).localeCompare(b.name || b.id);
  });

  if (sorted.length === 0) return null;

  let lastGroup;

  return (
    <div className="habitat-module-status-list card">
      <h2>Modules</h2>
      <div className="hmsl-list">
        {sorted.map(module => {
          const group = module.group || "";
          const showGroup = group !== lastGroup;
          lastGroup = group;
          const isFaulted = faultedModuleIds.has(module.id);
          const dotStatus = isFaulted ? "fault" : module.status?.toLowerCase();
          return (
            <React.Fragment key={module.id}>
              {showGroup && group && (
                <div className="hmsl-group">{group}</div>
              )}
              <div className={`hmsl-item${isFaulted ? " hmsl-item--fault" : ""}`}>
                <div className={`hmsl-dot ${dotStatus}`} />
                <span className="hmsl-name">{module.name}</span>
                <span className="hmsl-type">{module.type}</span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

export default HabitatModuleStatusList;
