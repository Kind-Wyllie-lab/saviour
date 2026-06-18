import React, { useState, useEffect } from "react";
import "./Settings.css";
import useModules from "/src/hooks/useModules";

import ConfigCard from "/src/basic/components/ConfigCard/ConfigCard";

const getHashId = () => window.location.hash.slice(1) || "controller";

function Settings() {
  const { modules } = useModules();
  const [selectedId, setSelectedId] = useState(getHashId);
  const [clipboard, setClipboard] = useState(null); // { label, data }

  // Write hash whenever selection changes
  useEffect(() => {
    window.location.hash = selectedId;
  }, [selectedId]);

  // Sync from hash on browser back/forward
  useEffect(() => {
    const onHashChange = () => setSelectedId(getHashId());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const moduleOptions = [
    { id: "controller", name: "Controller" },
    ...Object.entries(modules).map(([id, module]) => ({
      id,
      name: module.name + " (" + id + ")" || id,
    })),
  ];

  const selectedModule =
    selectedId === "controller" ? null : modules[selectedId];

  const syncServerModule =
    Object.entries(modules)
      .filter(([, m]) => m.type?.includes("camera"))
      .map(([id, m]) => ({ id, ...m }))
      .find(m => m.config?.camera?.sync_mode === "server") ?? null;

  return (
    <main className="settings">
      <h2>Module Settings</h2>

      <label className="settings-label">
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
        >
          {moduleOptions.map((opt) => (
            <option key={opt.id} value={opt.id}>
              {opt.name}
            </option>
          ))}
        </select>
      </label>

      <div className="module-grid">
        <ConfigCard id={selectedId} module={selectedModule} clipboard={clipboard} onCopy={setClipboard} syncServerModule={syncServerModule} />
      </div>

    </main>
  );
}

export default Settings;
