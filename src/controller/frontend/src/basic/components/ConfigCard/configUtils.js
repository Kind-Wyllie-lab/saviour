/**
 * Recursively removes keys prefixed with "_" and prunes any objects that
 * become empty as a result.  Returns undefined when the whole object is empty.
 */
export function filterPrivateKeys(obj) {
  if (!obj || typeof obj !== "object") return obj;

  const filtered = {};
  for (const [key, value] of Object.entries(obj)) {
    if (!key.startsWith("_")) {
      const filteredValue = typeof value === "object" ? filterPrivateKeys(value) : value;
      if (
        filteredValue !== undefined &&
        filteredValue !== null &&
        (typeof filteredValue !== "object" || Object.keys(filteredValue).length > 0)
      ) {
        filtered[key] = filteredValue;
      }
    }
  }
  return Object.keys(filtered).length > 0 ? filtered : undefined;
}
