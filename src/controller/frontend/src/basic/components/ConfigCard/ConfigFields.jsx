import { useState } from "react";
import { filterPrivateKeys, isPlainObject } from "./configUtils";

/**
 * Recursively renders form fields for a plain config object.
 * Nested objects become collapsible fieldsets.
 * Special-purpose cards (e.g. TTL) render this component for the generic
 * sections and handle their own overrides separately.
 */
function ConfigFields({ data, handleChange, sectionExtras = {} }) {
  const [collapsedSections, setCollapsedSections] = useState({});

  const renderFields = (obj, path = []) => {
    const filtered = filterPrivateKeys(obj);
    if (!filtered) return null;

    return Object.entries(filtered).map(([key, value]) => {
      const fieldPath = [...path, key];
      const fieldKey = fieldPath.join(".");

      // Only recurse into plain objects — skip Arrays, ArrayBuffers, typed arrays, etc.
      if (isPlainObject(value)) {
        const isCollapsed = collapsedSections[fieldKey] ?? false;
        const extra = path.length === 0 ? sectionExtras[key] : undefined;
        return (
          <fieldset key={fieldKey} className="nested-fieldset">
            <legend
              className="nested-fieldset-legend"
              onClick={() =>
                setCollapsedSections(prev => ({ ...prev, [fieldKey]: !isCollapsed }))
              }
            >
              <span className="nested-fieldset-arrow">{isCollapsed ? "▸" : "▾"}</span>
              {key}
            </legend>
            {!isCollapsed && (
              <div className="nested">
                {renderFields(value, fieldPath)}
                {extra}
              </div>
            )}
          </fieldset>
        );
      }

      // Skip non-plain objects that filterPrivateKeys may not have caught
      if (typeof value === "object" && value !== null) return null;

      return (
        <div key={fieldKey} className="form-field">
          <label>{key}:</label>
          <input
            type={
              typeof value === "number" ? "number" :
              typeof value === "boolean" ? "checkbox" :
              "text"
            }
            value={typeof value === "boolean" ? undefined : (value ?? "")}
            checked={typeof value === "boolean" ? value : undefined}
            onChange={(e) => handleChange(fieldPath, e)}
          />
        </div>
      );
    });
  };

  return <>{renderFields(data)}</>;
}

export default ConfigFields;
