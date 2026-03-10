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
      const cloned = structuredClone(prev);
      let pointer = cloned;
      for (let i = 0; i < path.length - 1; i++) pointer = pointer[path[i]];
      const lastKey = path[path.length - 1];
      const oldValue = pointer[lastKey];

      if (typeof oldValue === "boolean") pointer[lastKey] = e.target.checked;
      else if (typeof oldValue === "number") pointer[lastKey] = Number(e.target.value);
      else pointer[lastKey] = e.target.value;

      return cloned;
    });
  };

  return { formData, setFormData, handleChange };
}
