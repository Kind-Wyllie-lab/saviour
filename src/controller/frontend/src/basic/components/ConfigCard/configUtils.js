/**
 * Compare the section keys (top-level object-valued keys, excluding private "_" keys)
 * of two config objects. Returns null if compatible, or a descriptive string if not.
 *
 * Checks both directions: keys present in source but missing from dest, and keys
 * present in dest but missing from source. A section-copy clipboard ({ camera: {...} })
 * is checked against whether dest has that section at all.
 */
export function checkClipboardCompatibility(clipboardData, formData) {
  const sectionKeys = obj =>
    Object.keys(obj ?? {}).filter(
      k => !k.startsWith("_") && obj[k] !== null && typeof obj[k] === "object"
    );

  const srcSections = sectionKeys(clipboardData);
  const dstSections = new Set(sectionKeys(formData));

  const missing = srcSections.filter(k => !dstSections.has(k));
  if (missing.length === 0) return null;

  return `Cannot paste: section${missing.length > 1 ? "s" : ""} not present on this device: ${missing.join(", ")}`;
}

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
 * Recursively removes keys prefixed with "_", non-plain-object/non-array
 * values (ArrayBuffer, typed arrays, etc.) and prunes any objects that
 * become empty as a result.  Returns undefined when the whole object is empty.
 *
 * Plain Arrays (color tuples, NDC positions, label lists, etc.) pass through
 * unchanged rather than being dropped -- config values like loom_stimulus's
 * background_rgba are legitimate JSON arrays of primitives, not binary
 * buffers, and need to round-trip through save/paste like any other field.
 */
export function filterPrivateKeys(obj) {
  // Primitives pass through as-is
  if (typeof obj !== "object" || obj === null) return obj;
  if (Array.isArray(obj)) return obj;
  // Non-plain, non-array objects (ArrayBuffer, typed arrays, Dates, etc.)
  if (!isPlainObject(obj)) return undefined;

  const filtered = {};
  for (const [key, value] of Object.entries(obj)) {
    if (key.startsWith("_")) continue;

    let filteredValue;
    if (isPlainObject(value)) {
      filteredValue = filterPrivateKeys(value);
    } else if (Array.isArray(value)) {
      filteredValue = value; // array of primitives — pass through unchanged
    } else if (typeof value !== "object" || value === null) {
      filteredValue = value; // primitive or null
    } else {
      continue; // ArrayBuffer, typed arrays, Dates, etc. — skip silently
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
