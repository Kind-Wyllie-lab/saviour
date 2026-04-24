import React from "react";
import "./HabitatModuleStatusList.css";

function HabitatModuleStatusList({ modules }) {
  const list = Object.values(modules);

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
          return (
            <React.Fragment key={module.id}>
              {showGroup && group && (
                <div className="hmsl-group">{group}</div>
              )}
              <div className="hmsl-item">
                <div className={`hmsl-dot ${module.status?.toLowerCase()}`} />
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
