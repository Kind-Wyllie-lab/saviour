import React, { useEffect, useState } from "react";
import "./Settings.css";
import socket from "../../../socket";
import useModules from "/src/hooks/useModules";


import ConfigCard from "../../components/ConfigCard/ConfigCard";
import ControllerConfigCard from "../../components/ConfigCard/ControllerConfigCard/ControllerConfigCard";


function Settings() {
  const { modules } = useModules();
  const [selectedId, setSelectedId] = useState("controller");

  const moduleOptions = [
    { id: "controller", name: "Controller" },
    ...Object.entries(modules).map(([id, module]) => ({
      id,
      name: module.name + " (" + id + ")" || id,
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
