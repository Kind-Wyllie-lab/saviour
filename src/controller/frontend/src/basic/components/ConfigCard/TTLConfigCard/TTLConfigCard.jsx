import { useState } from "react";
import socket from "../../../../socket";
import "./TTLConfigCard.css";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";

const OUTPUT_MODES = new Set(["experiment_clock", "pseudorandom"]);

function TTLConfigCard({ module }) {
  const { formData, setFormData } = useConfigForm(module.config);
  const [collapsed, setCollapsed] = useState(false);
  const [newPin, setNewPin] = useState("");
  const [newMode, setNewMode] = useState("");

  const ttlCfg = formData?.ttl ?? {};
  const availablePins = module.config?.ttl?._available_pins ?? [];
  const availableModes = (module.config?.ttl?._available_modes ?? []).filter((m) => m !== "None");
  const schema = module.config?.ttl?._mode_settings_schema ?? {};
  const activeLogicOptions = module.config?.ttl?._active_logic_options ?? ["active_low", "active_high"];

  const assignedPins = Object.keys(ttlCfg.pins ?? {}).map(Number);
  const unassignedPins = availablePins.filter((p) => !assignedPins.includes(p));
  const sortedPins = Object.keys(ttlCfg.pins ?? {}).sort((a, b) => Number(a) - Number(b));

  const addPin = () => {
    const parsed = parseInt(newPin, 10);
    if (isNaN(parsed) || !newMode) return;
    const defaults = schema[`_${newMode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [parsed]: {
            mode: newMode,
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
    setNewPin("");
    setNewMode("");
  };

  const removePin = (pin) => {
    setFormData((prev) => {
      const newPins = { ...prev.ttl.pins };
      delete newPins[pin];
      return { ...prev, ttl: { ...prev.ttl, pins: newPins } };
    });
  };

  const changeMode = (pin, mode) => {
    const defaults = schema[`_${mode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: {
            mode,
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
  };

  const changeField = (pin, field, val) => {
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: { ...prev.ttl.pins[pin], [field]: val },
        },
      },
    }));
  };

  const saveConfig = () => {
    socket.emit("save_module_config", { id: module.id, config: filterPrivateKeys(formData) });
  };

  return (
    <div className={`config-card ${collapsed ? "collapsed" : ""}`}>
      <div className="card-header">
        <h3 onClick={() => setCollapsed(!collapsed)} style={{ cursor: "pointer" }}>
          {module.id} {collapsed ? "(+)" : "(-)"}
        </h3>
      </div>

      {!collapsed && (
        <div className="config-card-body">
          <div className="form-field">
            <label>Active Logic</label>
            <select
              value={ttlCfg.active_logic ?? "active_low"}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, ttl: { ...prev.ttl, active_logic: e.target.value } }))
              }
            >
              {activeLogicOptions.map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          </div>

          <div className="pin-list">
            {sortedPins.map((pin) => {
              const settings = ttlCfg.pins[pin];
              const mode = settings.mode ?? "None";
              const isOutput = OUTPUT_MODES.has(mode);
              const modeFields = schema[`_${mode}`] ?? {};

              return (
                <div key={pin} className={`pin-card pin-card--${isOutput ? "output" : "input"}`}>
                  <div className="pin-card-header">
                    <span className="pin-label">GPIO {pin}</span>
                    <span className={`pin-type-badge ${isOutput ? "badge--output" : "badge--input"}`}>
                      {isOutput ? "OUTPUT" : "INPUT"}
                    </span>
                    <button className="btn-remove" onClick={() => removePin(pin)}>✕</button>
                  </div>

                  <div className="field">
                    <label>Mode</label>
                    <select value={mode} onChange={(e) => changeMode(pin, e.target.value)}>
                      {availableModes.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>

                  {Object.entries(modeFields).map(([k, meta]) => {
                    const fieldName = k.replace(/^_/, "");
                    return (
                      <div key={k} className="field">
                        <label>{fieldName}</label>
                        <input
                          type={meta.type === "float" || meta.type === "int" ? "number" : "text"}
                          min={meta.min}
                          max={meta.max}
                          step={meta.type === "float" ? "any" : undefined}
                          value={settings[fieldName] ?? ""}
                          onChange={(e) => changeField(pin, fieldName, e.target.value)}
                        />
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          <div className="add-pin-row">
            <select value={newPin} onChange={(e) => setNewPin(e.target.value)}>
              <option value="">Pin…</option>
              {unassignedPins.map((p) => (
                <option key={p} value={p}>GPIO {p}</option>
              ))}
            </select>
            <select value={newMode} onChange={(e) => setNewMode(e.target.value)}>
              <option value="">Mode…</option>
              {availableModes.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <button type="button" onClick={addPin} disabled={!newPin || !newMode}>
              Add Pin
            </button>
          </div>

          <button className="save-button" type="button" onClick={saveConfig}>
            Save
          </button>
        </div>
      )}
    </div>
  );
}

export default TTLConfigCard;
