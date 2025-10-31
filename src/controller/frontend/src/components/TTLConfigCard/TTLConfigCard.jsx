import React, { useState } from "react";
import socket from "../../socket";
import "./TTLConfigCard.css";

function TTLConfigCard({ config, id }) {
  const [pins, setPins] = useState(config.pins || {});
  const modes = config._available_modes || [];
  const schema = config._mode_settings_schema || {};
  const [newPin, setNewPin] = useState("");

  const handleModeChange = (pin, newMode) => {
    const defaults = schema[`_${newMode}`]
      ? Object.fromEntries(
          Object.entries(schema[`_${newMode}`]).map(([k, v]) => [
            k.replace(/^_/, ""),
            v.default ?? "",
          ])
        )
      : {};
    setPins((prev) => ({
      ...prev,
      [pin]: { mode: newMode, ...defaults },
    }));
  };

  const handleFieldChange = (pin, field, value) => {
    setPins((prev) => ({
      ...prev,
      [pin]: { ...prev[pin], [field]: value },
    }));
  };

  const addPin = () => {
    const parsed = parseInt(newPin, 10);
    if (isNaN(parsed)) {
      alert("Please enter a valid pin number.");
      return;
    }
    if (pins[parsed]) {
      alert(`Pin ${parsed} already exists.`);
      return;
    }
    setPins((prev) => ({
      ...prev,
      [parsed]: { mode: "None" },
    }));
    setNewPin("");
  };

  const removePin = (pin) => {
    setPins((prev) => {
      const copy = { ...prev };
      delete copy[pin];
      return copy;
    });
  };

  const saveConfig = () => {
    socket.emit("update_module_config", { id, pins });
  };

  return (
    <div className="ttl-card">
      <h3>TTL Configuration</h3>

      {/* Pin entry field */}
      <div className="add-pin-row">
        <input
          type="number"
          placeholder="Enter new pin number"
          value={newPin}
          onChange={(e) => setNewPin(e.target.value)}
        />
        <button onClick={addPin}>Add Pin</button>
      </div>

      {Object.entries(pins).map(([pin, settings]) => (
        <div key={pin} className="pin-card">
          <h4>Pin {pin}</h4>

          <label>Mode:</label>
          <select
            value={settings.mode || "None"}
            onChange={(e) => handleModeChange(pin, e.target.value)}
          >
            {modes.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>

          {settings.mode !== "None" &&
            Object.entries(schema[`_${settings.mode}`] || {}).map(
              ([key, meta]) => (
                <div key={key} className="field">
                  <label>{key.replace(/^_/, "")}</label>
                  <input
                    type={meta.type === "float" ? "number" : meta.type}
                    min={meta.min}
                    max={meta.max}
                    step="any"
                    value={settings[key.replace(/^_/, "")] ?? ""}
                    onChange={(e) =>
                      handleFieldChange(
                        pin,
                        key.replace(/^_/, ""),
                        e.target.value
                      )
                    }
                  />
                </div>
              )
            )}

          <button onClick={() => removePin(pin)}>Remove Pin</button>
        </div>
      ))}

      <button onClick={saveConfig}>Save</button>
    </div>
  );
}

export default TTLConfigCard;
