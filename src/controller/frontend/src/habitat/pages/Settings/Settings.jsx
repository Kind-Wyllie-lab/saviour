import React, { useEffect, useState } from "react";
import socket from "../../../socket";
import "./Settings.css";
import ConfigCard from "../../components/ConfigCard/ConfigCard";
import ControllerConfigCard from "../../components/ConfigCard/ControllerConfigCard/ControllerConfigCard";

function Settings() {
  const [modules, setModules] = useState({});
  const [selectedId, setSelectedId] = useState("controller");

  useEffect(() => {
    socket.emit("get_module_configs");
    socket.emit("get_modules");

    socket.on("modules_update", (data) => {
      if (!data || typeof data !== "object") return;

      const withDefaults = Object.fromEntries(
        Object.entries(data).map(([id, m]) => [
          id,
          {
            ...m,
            id,
            config: m.config || {},
            ready: false,
            checks: {},
            error: null,
          },
        ])
      );

      setModules(withDefaults);
    });

    return () => {
      socket.off("modules_update");
    };
  }, []);

  const moduleOptions = [
    { id: "controller", name: "Controller" },
    ...Object.entries(modules).map(([id, module]) => ({
      id,
      name: module.name || id,
    })),
  ];

  const selectedModule =
    selectedId === "controller" ? null : modules[selectedId];

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
        <ConfigCard id={selectedId} module={selectedModule} />
      </div>

    </main>
  );
}

export default Settings;
