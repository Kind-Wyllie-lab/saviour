import { useState, useEffect, useRef } from "react";

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
  const [savedSnapshot, setSavedSnapshot] = useState(initialData);
  const isDirty = JSON.stringify(formData) !== JSON.stringify(savedSnapshot ?? {});

  // Read fresh inside the sync effect below without listing it as a
  // dependency (it must not re-trigger a resync by itself).
  const isDirtyRef = useRef(isDirty);
  isDirtyRef.current = isDirty;

  // Sync when the parent-supplied data changes (e.g. a periodic heartbeat's
  // websocket push carries a brand-new module.config object every ~30s).
  // Skipped while the form has unsaved edits — otherwise a routine status
  // broadcast would silently discard whatever the user is mid-typing.
  useEffect(() => {
    if (initialData === undefined) return;
    if (isDirtyRef.current) return;
    setFormData(initialData);
    setSavedSnapshot(initialData);
  }, [initialData]);

  // Warn before the tab closes/refreshes/navigates away while there are
  // unsaved edits — config changes here go out to live recording hardware,
  // so a silently discarded edit is a real footgun, not just an annoyance.
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Broadcast dirty state so pages that let the user switch between
  // multiple config cards (e.g. Settings' module picker) can warn before
  // switching away from one with unsaved edits.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("saviour:config-dirty", { detail: { dirty: isDirty } }));
  }, [isDirty]);

  // Call after a save/reset is dispatched so the just-sent formData becomes
  // the new baseline — otherwise the form would (correctly, per the guard
  // above) refuse to accept the server's own echo of what was just saved.
  // Pass an explicit value for callers (e.g. ControllerConfigCard) that load
  // data manually via setFormData rather than the initialData prop — the
  // component's `formData` closure won't reflect that update synchronously.
  const markSaved = (explicit) => setSavedSnapshot(explicit !== undefined ? explicit : formData);

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

  return { formData, setFormData, handleChange, isDirty, markSaved };
}
