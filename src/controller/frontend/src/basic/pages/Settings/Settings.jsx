import React, { useState, useEffect } from "react";
import "./Settings.css";
import useModules from "/src/hooks/useModules";

import ConfigCard from "/src/basic/components/ConfigCard/ConfigCard";

// Hash format is "#<deviceId>/<tab>" — the tab half is owned by each config
// card's useHashTab, this only ever reads/writes the device half so a tab
// deep-link isn't clobbered by module-selection logic.
const getHashId = () => {
  const raw = window.location.hash.slice(1);
  const slash = raw.indexOf("/");
  return (slash === -1 ? raw : raw.slice(0, slash)) || "controller";
};

function Settings() {
  const { modules } = useModules();
  const [selectedId, setSelectedId] = useState(getHashId);
  const [clipboard, setClipboard] = useState(null); // { label, data }
  const [configDirty, setConfigDirty] = useState(false);

  // The currently-mounted ConfigCard's useConfigForm broadcasts its dirty
  // state here so switching modules can warn before discarding edits.
  useEffect(() => {
    const handler = (e) => setConfigDirty(!!e.detail?.dirty);
    window.addEventListener("saviour:config-dirty", handler);
    return () => window.removeEventListener("saviour:config-dirty", handler);
  }, []);

  const trySelectId = (newId) => {
    if (newId === selectedId) return;
    if (configDirty && !window.confirm(
      "You have unsaved config changes that will be lost if you switch modules. Continue?"
    )) {
      return;
    }
    setConfigDirty(false);
    setSelectedId(newId);
  };

  // Write hash whenever selection changes — but only if the device id
  // actually changed (e.g. on mount, it hasn't) so a tab suffix already in
  // the hash isn't wiped out before the newly-mounted card's useHashTab
  // gets a chance to read it.
  useEffect(() => {
    if (getHashId() === selectedId) return;
    window.location.hash = selectedId;
  }, [selectedId]);

  // Sync from hash on browser back/forward — same unsaved-changes guard,
  // reverting the hash if the user cancels so it doesn't drift from state.
  useEffect(() => {
    const onHashChange = () => {
      const newId = getHashId();
      if (newId === selectedId) return;
      if (configDirty && !window.confirm(
        "You have unsaved config changes that will be lost if you switch modules. Continue?"
      )) {
        window.location.hash = selectedId;
        return;
      }
      setConfigDirty(false);
      setSelectedId(newId);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [selectedId, configDirty]);

  // Controller is pinned at the top (it isn't a "module" -- always its own
  // top-level option, never grouped in with the rest). Everything else is
  // grouped by module type (a real <optgroup> per type, e.g. "Camera",
  // "Microphone"), with both the groups themselves and the modules inside
  // each group in alphabetical order, so a large habitat-scale fleet (many
  // modules of a handful of types) stays scannable instead of listing in
  // whatever order modules happened to connect/discover in.
  const formatTypeLabel = (type) => {
    if (!type) return "Other";
    return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const moduleGroups = {};
  for (const [id, module] of Object.entries(modules)) {
    const type = module.type || "other";
    (moduleGroups[type] ??= []).push({ id, name: module.name ? `${module.name} (${id})` : id });
  }
  for (const group of Object.values(moduleGroups)) {
    group.sort((a, b) => a.name.localeCompare(b.name));
  }
  const sortedTypes = Object.keys(moduleGroups).sort((a, b) =>
    formatTypeLabel(a).localeCompare(formatTypeLabel(b))
  );

  const selectedModule =
    selectedId === "controller" ? null : modules[selectedId];

  const syncServerModule =
    Object.entries(modules)
      .filter(([, m]) => m.type?.includes("camera"))
      .map(([id, m]) => ({ id, ...m }))
      .find(m => m.config?.camera?.sync_mode === "server") ?? null;

  return (
    <main className="settings-page">
      <div className="settings-toolbar">
        <label className="settings-label">
          <span className="settings-label-text">Device:</span>
          <select
            value={selectedId}
            onChange={(e) => trySelectId(e.target.value)}
          >
            <option value="controller">Controller</option>
            {sortedTypes.map((type) => (
              <optgroup key={type} label={formatTypeLabel(type)}>
                {moduleGroups[type].map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      </div>

      <div className="module-grid">
        <ConfigCard id={selectedId} module={selectedModule} clipboard={clipboard} onCopy={setClipboard} syncServerModule={syncServerModule} />
      </div>
    </main>
  );
}

export default Settings;
