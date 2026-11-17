import { useState } from "react";
import ProtocolTimeline from "../../ProtocolTimeline/ProtocolTimeline";
import { fmtTime } from "../../../utils/sessionProtocol";
import useProtocolExpansion from "../../Protocol/useProtocolExpansion";
import { freeName, sameProtocol } from "../../Protocol/protocolLibrary";
import "../../Protocol/ProtocolEditor.css";

// The TTL card's Protocol tab. Protocols are picked per session (New
// Session form); here a library protocol can be checked against this
// module's Scheduled pins, previewed and run with Test routine at the rig.
// A protocol left in the module's settings (ttl.protocol, from before
// protocols moved to the session) is no longer run: the tab offers to move
// it into the library.

export default function ProtocolTab({
  legacyProtocol, onClearLegacy, library, scheduledPins, pinConfigs, moduleId,
  moduleLabel, onOpenDesigner, renderTest, cursorT, canEdit,
}) {
  const items = library.items ?? [];
  const [previewName, setPreviewName] = useState("");
  const preview = items.find((i) => i.name === previewName)?.protocol ?? null;
  const { pins, result, rows, spans } = useProtocolExpansion(preview, scheduledPins, pinConfigs, moduleId);

  const moveToLibrary = () => {
    const same = legacyProtocol?.name && items.find((i) => i.name === legacyProtocol.name);
    if (same && sameProtocol(same.protocol, legacyProtocol)) {
      onClearLegacy();
      setPreviewName(same.name);
      return;
    }
    const base = legacyProtocol?.name
      ? (same ? `${legacyProtocol.name} (from ${moduleLabel})` : legacyProtocol.name)
      : `Protocol from ${moduleLabel}`;
    const name = freeName(base, items);
    library.save(name, { ...legacyProtocol, name }, null);
    onClearLegacy();
    setPreviewName(name);
  };

  return (
    <div className="pe">
      <p className="pe-intro">
        Protocols are picked for each session in the <strong>New Session</strong> form on the
        Recording page, and run on this module's <strong>Scheduled</strong> pins (Pins tab),
        timed from session start. Here you can check a protocol against this module, preview it
        and run it on the pins with Test routine.
      </p>

      {legacyProtocol && (
        <div className="pt-legacy">
          <span>
            This module's settings still hold a protocol
            {legacyProtocol.name ? ` (“${legacyProtocol.name}”)` : ""}. It is no longer run:
            protocols now come with the session.
          </span>
          <button type="button" className="pe-small" onClick={moveToLibrary} disabled={!canEdit}
            title={canEdit ? "Save it to the library and remove it from this module's settings (then Save Config)"
              : "Sign in as admin"}>
            Move to library
          </button>
        </div>
      )}

      {pins.length === 0 && (
        <div className="pe-notice">No scheduled pins yet: set a pin's mode to “Scheduled (session protocol)” on the Pins tab.</div>
      )}

      <div className="pe-top">
        <label className="pe-field pt-pick">
          <span className="pe-field-label">Check a protocol on this module</span>
          <select value={previewName} onChange={(e) => setPreviewName(e.target.value)}>
            <option value="">{items.length ? "Pick one…" : "The library is empty"}</option>
            {items.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
        </label>
        <div className="pe-top-right">
          <button type="button" className="pe-small pt-designer"
            onClick={() => onOpenDesigner(previewName ? { name: previewName } : {}, setPreviewName)}>
            Open protocol designer
          </button>
        </div>
      </div>

      {preview && result && (
        <div className="pe-check">
          <div className={`pe-summary${result.ok ? "" : " pe-summary--bad"}`}>
            <span className="pe-summary-icon" aria-hidden="true">{result.ok ? "✓" : "✕"}</span>
            {result.ok
              ? `Runs on this module: ${result.edges.length} edges on ${result.pins.map((p) => `GPIO ${p}`).join(", ") || "no pins"}, last at ${fmtTime(result.durationS, result.durationS < 60)}`
              : `Won't run on this module (a session with it would be refused): ${result.errors[0]}`
                + (result.errors.length > 1 ? ` (+${result.errors.length - 1} more)` : "")}
          </div>
        </div>
      )}

      {preview && renderTest && renderTest(result, preview)}

      {preview && (
        <div className={`pe-timeline${result && !result.ok ? " pe-timeline--invalid" : ""}`}>
          <ProtocolTimeline
            rows={rows}
            spans={spans}
            duration={result?.durationS ?? 0}
            cursorT={cursorT}
            emptyText="Add a scheduled pin to see the timeline."
          />
        </div>
      )}
    </div>
  );
}
