import React, { useState, useEffect } from "react";
// import './ConfigCard.css';
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import socket from "/src/socket";

function GenericConfigCard({ id, module }) {
  const [formData, setFormData] = useState(module.config); // Component level config state
  const [collapsedSections, setCollapsedSections] = useState({}); // per-section collapse

  // Get config for module
  useEffect(() => {
    socket.emit("get_module_config", {module_id: module.id});
  }, []);

  // Keep formData synced if parent updates config
  useEffect(() => setFormData(module.config), [module.config]);

  // Recursively remove private keys and prune empty objects
  const filterPrivateKeys = (obj) => {
    if (!obj || typeof obj !== "object") return obj;

    const filtered = {};
    for (const [key, value] of Object.entries(obj)) {
      if (!key.startsWith("_")) {
        const filteredValue = typeof value === "object" ? filterPrivateKeys(value) : value;
        // Only include if not undefined, null, or empty object
        if (
          filteredValue !== undefined &&
          filteredValue !== null &&
          (typeof filteredValue !== "object" || Object.keys(filteredValue).length > 0)
        ) {
          filtered[key] = filteredValue;
        }
      }
    }

    // Return undefined if object became empty
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

  const getValueFromPath = (path) => path.reduce((acc, key) => acc[key], formData);

  const renderFields = (obj, path = []) => {
    const filteredObj = filterPrivateKeys(obj);
    if (!filteredObj) return null; // skip empty parents
  
    return Object.entries(filteredObj).map(([key, value]) => {
      const fieldPath = [...path, key];
      const fieldKey = fieldPath.join(".");
  
      if (typeof value === "object" && value !== null) {
        const collapsedSection = collapsedSections[fieldKey] ?? false;
        return (
          <fieldset key={fieldKey} className="nested-fieldset">
            <legend
              onClick={() =>
                setCollapsedSections(prev => ({ ...prev, [fieldKey]: !collapsedSection }))
              }
              style={{ cursor: "pointer" }}
            >
              {key} {collapsedSection ? "(+)" : "(-)"}
            </legend>
            {!collapsedSection && <div className="nested">{renderFields(value, fieldPath)}</div>}
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

  const handleUpdate = () => {
    // Send command to module to update saviour version
    socket.emit("send_command", {
      module_id: module.id,
      type: "update_saviour",
      params: {}
    })
  }

  const handleReboot = () => {
    console.log("Sending reboot");
    socket.emit("send_command", {
      module_id: module.id,
      type: "reboot",
      params: {}
    })
  }  

  const handleSave = () => {
    import("/src/socket").then(({ default: socket }) => {
      const editableData = filterPrivateKeys(formData); // only send editable keys
      const wrappedData = { config: editableData };
      console.log("Saving config for module", id, wrappedData);
      socket.emit("save_module_config", { id: id, config: wrappedData });
    });
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          <form>{renderFields(formData)}</form>
          <button className="save-button" type="button" onClick={handleSave}>Save Config</button>
        </div>

        {/* Render livestream only for camera modules */}
        {module.type.includes("camera") && (
          <div className="livestream-wrapper">
            <LivestreamCard module={module} />
          </div>
        )}

      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleUpdate}>Update Saviour Version</button>
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleReboot}>Reboot Module</button>
      </div>
    </div>
  );
}

export default GenericConfigCard;
