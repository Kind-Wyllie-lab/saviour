/**
 * Returns true only for plain JSON-style objects ({}).
 * Rejects Arrays, ArrayBuffers, typed arrays, Dates, etc.
 */
export function isPlainObject(v) {
  if (v === null || typeof v !== "object") return false;
  const p = Object.getPrototypeOf(v);
  return p === Object.prototype || p === null;
}

/**
 * Recursively removes keys prefixed with "_", non-plain-object values
 * (ArrayBuffer, Array, typed arrays, etc.) and prunes any objects that
 * become empty as a result.  Returns undefined when the whole object is empty.
 */
export function filterPrivateKeys(obj) {
  // Primitives pass through as-is
  if (typeof obj !== "object" || obj === null) return obj;
  // Non-plain objects (Array, ArrayBuffer, typed arrays, etc.) are dropped
  if (!isPlainObject(obj)) return undefined;

  const filtered = {};
  for (const [key, value] of Object.entries(obj)) {
    if (key.startsWith("_")) continue;

    let filteredValue;
    if (isPlainObject(value)) {
      filteredValue = filterPrivateKeys(value);
    } else if (typeof value !== "object" || value === null) {
      filteredValue = value; // primitive or null
    } else {
      continue; // Array, ArrayBuffer, etc. — skip silently
    }

    if (
      filteredValue !== undefined &&
      filteredValue !== null &&
      (typeof filteredValue !== "object" || Object.keys(filteredValue).length > 0)
    ) {
      filtered[key] = filteredValue;
    }
  }
  return Object.keys(filtered).length > 0 ? filtered : undefined;
}
