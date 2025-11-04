import React, { useState, useEffect } from "react";
import socket from "../../../socket";
import "./TTLConfigCard.css";

/* Draft for a TTL config card that dynamically updates form based on schema, for adding and removing pins. Not currently working.*/

function TTLConfigCard({ module }) {
  const [formData, setFormData] = useState(module.config);
  const [collapsed, setCollapsed] = useState(false); // top-level collapse
  const [collapsedSections, setCollapsedSections] = useState({}); // for nested fields
  const [newPin, setNewPin] = useState("");

  // Keep formData synced if parent updates config
  useEffect(() => setFormData(module.config), [module.config]);

  // Recursively filter out private keys
  const filterPrivateKeys = (obj) => {
    if (!obj || typeof obj !== "object") return obj;
    const filtered = {};
    for (const [k, v] of Object.entries(obj)) {
      if (!k.startsWith("_")) {
        const val = typeof v === "object" ? filterPrivateKeys(v) : v;
        if (
          val !== undefined &&
          val !== null &&
          (typeof val !== "object" || Object.keys(val).length > 0)
        ) {
          filtered[k] = val;
        }
      }
    }
    return Object.keys(filtered).length > 0 ? filtered : undefined;
  };

  const handleChange = (path, e) => {
    const newData = { ...formData };
    let pointer = newData;
    for (let i = 0; i < path.length - 1; i++) pointer = pointer[path[i]];
    const lastKey = path[path.length - 1];
    const oldValue = pointer[lastKey];

    if (typeof oldValue === "boolean") pointer[lastKey] = e.target.checked;
    else if (typeof oldValue === "number") pointer[lastKey] = Number(e.target.value);
    else pointer[lastKey] = e.target.value;

    setFormData(newData);
  };

  const renderFields = (obj, path = []) => {
    const filteredObj = filterPrivateKeys(obj);
    if (!filteredObj) return null;

    return Object.entries(filteredObj).map(([key, value]) => {
      const fieldPath = [...path, key];
      const fieldKey = fieldPath.join(".");

      // Special rendering for TTL pins
      if (key === "pins" && path[0] === "ttl") {
        const modes = module.config.ttl._available_modes || [];
        const schema = module.config.ttl._mode_settings_schema || {};
        return Object.entries(value).map(([pin, settings]) => (
          <div key={pin} className="pin-card">
            <h4>Pin {pin}</h4>

            <label>Mode:</label>
            <select
              value={settings.mode || "None"}
              onChange={(e) => {
                const newMode = e.target.value;
                const defaults = schema[`_${newMode}`] || {};
                setFormData((prev) => ({
                  ...prev,
                  ttl: {
                    ...prev.ttl,
                    pins: {
                      ...prev.ttl.pins,
                      [pin]: {
                        mode: newMode,
                        ...Object.fromEntries(
                          Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
                        ),
                      },
                    },
                  },
                }));
              }}
            >
              {modes.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>

            {settings.mode !== "None" &&
              Object.entries(schema[`_${settings.mode}`] || {}).map(([k, meta]) => (
                <div key={k} className="field">
                  <label>{k.replace(/^_/, "")}</label>
                  <input
                    type={meta.type === "float" ? "number" : meta.type}
                    min={meta.min}
                    max={meta.max}
                    step="any"
                    value={settings[k.replace(/^_/, "")] ?? ""}
                    onChange={(e) => {
                      const val = e.target.value;
                      setFormData((prev) => ({
                        ...prev,
                        ttl: {
                          ...prev.ttl,
                          pins: {
                            ...prev.ttl.pins,
                            [pin]: { ...prev.ttl.pins[pin], [k.replace(/^_/, "")]: val },
                          },
                        },
                      }));
                    }}
                  />
                </div>
              ))}

            <button
              onClick={() => {
                setFormData((prev) => {
                  const copy = { ...prev };
                  delete copy.ttl.pins[pin];
                  return copy;
                });
              }}
            >
              Remove Pin
            </button>
          </div>
        ));
      }

      // Render nested objects recursively
      if (typeof value === "object" && value !== null) {
        const collapsedSection = collapsedSections[fieldKey] ?? false;
        return (
          <fieldset key={fieldKey} className="nested-fieldset">
            <legend
              onClick={() =>
                setCollapsedSections((prev) => ({ ...prev, [fieldKey]: !collapsedSection }))
              }
              style={{ cursor: "pointer" }}
            >
              {key} {collapsedSection ? "(+)" : "(-)"}
            </legend>
            {!collapsedSection && <div className="nested">{renderFields(value, fieldPath)}</div>}
          </fieldset>
        );
      }

      // Render primitive fields
      return (
        <div key={fieldKey} className="form-field">
          <label>{key}</label>
          <input
            type={typeof value === "number" ? "number" : typeof value === "boolean" ? "checkbox" : "text"}
            value={typeof value === "boolean" ? undefined : value}
            checked={typeof value === "boolean" ? value : undefined}
            onChange={(e) => handleChange(fieldPath, e)}
          />
        </div>
      );
    });
  };

  const addPin = () => {
    const parsed = parseInt(newPin, 10);
    if (isNaN(parsed)) return alert("Enter a valid pin number.");
    if (formData.ttl.pins[parsed]) return alert(`Pin ${parsed} already exists.`);

    setFormData((prev) => ({
      ...prev,
      ttl: { ...prev.ttl, pins: { ...prev.ttl.pins, [parsed]: { mode: "None" } } },
    }));
    setNewPin("");
  };

  const saveConfig = () => {
    const editableData = filterPrivateKeys(formData);
    const wrappedData = { config: editableData };
    console.log("Saving config for module", module.id, wrappedData);
    socket.emit("save_module_config", { id: module.id, config: wrappedData });
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
          <div className="config-form">
            <div className="add-pin-row">
              <input
                type="number"
                placeholder="Enter new pin number"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
              />
              <button type="button" onClick={addPin}>
                Add Pin
              </button>
            </div>

            <form>{renderFields(formData)}</form>

            <button className="save-button" type="button" onClick={saveConfig}>
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default TTLConfigCard;
