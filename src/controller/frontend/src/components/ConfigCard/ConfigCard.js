import React, { useState, useEffect } from "react";
import './ConfigCard.css';

function ConfigCard({ id, config }) {
  const [formData, setFormData] = useState(config);
  const [collapsed, setCollapsed] = useState(false); // top-level collapse
  const [collapsedSections, setCollapsedSections] = useState({}); // per-section collapse

  // Keep formData synced if parent updates config
  useEffect(() => setFormData(config), [config]);

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

  const getValueFromPath = (path) => path.reduce((acc, key) => acc[key], formData);

  const renderFields = (obj, path = []) => {
    return Object.entries(obj).map(([key, value]) => {
      const fieldPath = [...path, key];
      const fieldKey = fieldPath.join(".");

      if (typeof value === "object" && value !== null) {
        const collapsed = collapsedSections[fieldKey] ?? false;
        return (
          <fieldset key={fieldKey} className="nested-fieldset">
            <legend
              onClick={() => setCollapsedSections(prev => ({ ...prev, [fieldKey]: !collapsed }))}
              style={{ cursor: "pointer" }}
            >
              {key} {collapsed ? "(+)" : "(-)"}
            </legend>
            {!collapsed && <div className="nested">{renderFields(value, fieldPath)}</div>}
          </fieldset>
        );
      }

      return (
        <div key={fieldKey} className="form-field">
          <label>{key}:</label>
          <input
            type={typeof value === "number" ? "number" : typeof value === "boolean" ? "checkbox" : "text"}
            value={typeof value === "boolean" ? undefined : getValueFromPath(fieldPath)}
            checked={typeof value === "boolean" ? getValueFromPath(fieldPath) : undefined}
            onChange={(e) => handleChange(fieldPath, e)}
          />
        </div>
      );
    });
  };

  const handleSave = () => {
    import("../../socket").then(({ default: socket }) => {
      const formattedData = { editable: formData };
      console.log("Saving config for module", id, formattedData);
      socket.emit("save_module_config", { id, config: formattedData });
    });
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3 onClick={() => setCollapsed(!collapsed)} style={{ cursor: "pointer" }}>
          {id} {collapsed ? "(+)" : "(-)"}
        </h3>
      </div>

      {!collapsed && (
        <>
          <form>{renderFields(formData)}</form>
          <button className="save-button" type="button" onClick={handleSave}>Save Config</button>
        </>
      )}
    </div>
  );
}

export default ConfigCard;
