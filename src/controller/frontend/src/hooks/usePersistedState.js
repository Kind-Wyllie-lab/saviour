import { useEffect, useState } from "react";

// Persists a piece of UI state in sessionStorage so it survives a component
// unmounting and remounting later -- e.g. switching from the Recording page
// to Settings and back leaving an in-progress form's fields intact. Scoped
// to sessionStorage (not localStorage) so it doesn't leak across unrelated
// browser sessions, but does survive a same-tab reload.
export default function usePersistedState(key, defaultValue, { serialize = JSON.stringify, deserialize = JSON.parse } = {}) {
  const [value, setValue] = useState(() => {
    try {
      const stored = sessionStorage.getItem(key);
      return stored !== null ? deserialize(stored) : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(key, serialize(value));
    } catch {
      // Ignore quota/serialization errors -- persistence is a nice-to-have.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, value]);

  return [value, setValue];
}
