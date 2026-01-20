import React, { useState, useEffect } from "react";
import "./ControllerConfigCard.css";

function ControllerConfigCard() {
  const [formData, setFormData] = useState({});
  const [collapsedSections, setCollapsedSections] = useState({});

  // Load controller config on mount
  useEffect(() => {
    import("../../../../socket").then(({ default: socket }) => {
      socket.emit("get_controller_config");

      socket.on("controller_config_response", (data) => {
        setFormData(data.config || {});
      });
    });

    return () => {
      import("../../../../socket").then(({ default: socket }) => {
        socket.off("controller_config_response");
      });
    };
  }, []);

  const filterPrivateKeys = (obj) => {
    if (!obj || typeof obj !== "object") return obj;

    const filtered = {};
    for (const [key, value] of Object.entries(obj)) {
      if (!key.startsWith("_")) {
        const filteredValue =
          typeof value === "object" ? filterPrivateKeys(value) : value;

        if (
          filteredValue !== undefined &&
          filteredValue !== null &&
          (typeof filteredValue !== "object" ||
            Object.keys(filteredValue).length > 0)
        ) {
          filtered[key] = filteredValue;
        }
      }
    }

    return Object.keys(filtered).length > 0 ? filtered : undefined;
  };

  const handleChange = (path, e) => {
    const newData = { ...formData };
    let pointer = newData;

    for (let i = 0; i < path.length - 1; i++) {
      pointer = pointer[path[i]];
    }

    const lastKey = path[path.length - 1];
    const oldValue = pointer[lastKey];

    if (typeof oldValue === "boolean") pointer[lastKey] = e.target.checked;
    else if (typeof oldValue === "number") pointer[lastKey] = Number(e.target.value);
    else pointer[lastKey] = e.target.value;

    setFormData(newData);
  };

  const getValueFromPath = (path) =>
    path.reduce((acc, key) => acc[key], formData);

  const renderFields = (obj, path = []) => {
    const filteredObj = filterPrivateKeys(obj);
    if (!filteredObj) return null;

    return Object.entries(filteredObj).map(([key, value]) => {
      const fieldPath = [...path, key];
      const fieldKey = fieldPath.join(".");

      if (typeof value === "object" && value !== null) {
        const collapsedSection = collapsedSections[fieldKey] ?? false;
        return (
          <fieldset key={fieldKey} className="nested-fieldset">
            <legend
              onClick={() =>
                setCollapsedSections((prev) => ({
                  ...prev,
                  [fieldKey]: !collapsedSection,
                }))
              }
              style={{ cursor: "pointer" }}
            >
              {key} {collapsedSection ? "(+)" : "(-)"}
            </legend>

            {!collapsedSection && (
              <div className="nested">{renderFields(value, fieldPath)}</div>
            )}
          </fieldset>
        );
      }

      return (
        <div key={fieldKey} className="form-field">
          <label>{key}:</label>
          <input
            type={
              typeof value === "number"
                ? "number"
                : typeof value === "boolean"
                  ? "checkbox"
                  : "text"
            }
            value={typeof value === "boolean" ? undefined : getValueFromPath(fieldPath)}
            checked={typeof value === "boolean" ? getValueFromPath(fieldPath) : undefined}
            onChange={(e) => handleChange(fieldPath, e)}
          />
        </div>
      );
    });
  };

  const handleSave = () => {
    import("../../../../socket").then(({ default: socket }) => {
      const editableData = filterPrivateKeys(formData);
      socket.emit("save_controller_config", { config: editableData });
    });
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>Controller Config</h3>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          <form>{renderFields(formData)}</form>
          <button className="save-button" type="button" onClick={handleSave}>
            Save Config
          </button>
        </div>
      </div>
    </div>
  );
}

export default ControllerConfigCard;
