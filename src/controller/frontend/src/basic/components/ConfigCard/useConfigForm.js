import { useState, useEffect } from "react";

/**
 * Shared form-state hook for config cards.
 *
 * Fixes the mutation bug present in the original handleChange: the previous
 * implementation did a shallow `{ ...formData }` spread and then traversed
 * into nested objects via reference, silently mutating original state.  This
 * version uses structuredClone so every update is a clean deep copy.
 */
export function useConfigForm(initialData) {
  const [formData, setFormData] = useState(initialData ?? {});

  // Sync when the parent-supplied data changes (e.g. websocket push).
  useEffect(() => {
    if (initialData !== undefined) setFormData(initialData);
  }, [initialData]);

  const handleChange = (path, e) => {
    setFormData(prev => {
      const cloned = structuredClone(prev ?? {});
      let pointer = cloned;
      for (let i = 0; i < path.length - 1; i++) {
        // Module config may not have this section yet (e.g. a module that
        // hasn't finished syncing has config: {}) — create it rather than
        // crashing on undefined.
        if (pointer[path[i]] == null) pointer[path[i]] = {};
        pointer = pointer[path[i]];
      }
      const lastKey = path[path.length - 1];
      const oldValue = pointer[lastKey];

      // Infer type from the input itself first — falling back to oldValue's
      // type is wrong when the key is missing from config (e.g. a checkbox
      // whose oldValue is undefined would otherwise store e.target.value,
      // the string "on", instead of the boolean checked state).
      if (e.target.type === "checkbox") pointer[lastKey] = e.target.checked;
      else if (e.target.type === "number" || e.target.type === "range" || typeof oldValue === "number") {
        pointer[lastKey] = Number(e.target.value);
      } else pointer[lastKey] = e.target.value;

      return cloned;
    });
  };

  return { formData, setFormData, handleChange };
}
