import { useMemo } from "react";
import { expand, highSpans } from "../../utils/sessionProtocol";

// Expand a protocol against this module's scheduled pins and shape it for
// ProtocolTimeline: { pins, result, rows, spans }. Shared by the designer
// and the card's read-only preview, so both show the same thing.
export default function useProtocolExpansion(protocol, scheduledPins, pinConfigs, moduleId) {
  // Keyed on the contents: callers pass a fresh array every render.
  const pinsKey = [...(scheduledPins ?? [])].sort((a, b) => a - b).join(",");
  const pins = useMemo(() => (pinsKey ? pinsKey.split(",").map(Number) : []), [pinsKey]);

  const result = useMemo(
    () => (protocol ? expand(protocol, { moduleId, allowedPins: pins }) : null),
    [protocol, moduleId, pins]);

  const rows = useMemo(() => {
    const keys = new Set([...pins, ...(result?.pins ?? [])]);
    return [...keys].sort((a, b) => a - b).map((p) => ({
      key: p,
      label: `GPIO ${p}`,
      sublabel: pinConfigs?.[p]?.description || undefined,
    }));
  }, [pins, result, pinConfigs]);

  const spans = useMemo(() => {
    if (!result) return [];
    return highSpans(result.edges).map((s) => ({
      row: s.pin,
      start: s.start,
      end: s.end === null && result.endState === "all_inactive" ? result.durationS : s.end,
      label: `${s.action} (step ${s.step})`,
    }));
  }, [result]);

  return { pins, result, rows, spans };
}
