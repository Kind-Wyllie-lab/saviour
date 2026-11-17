import ProtocolTimeline from "../../ProtocolTimeline/ProtocolTimeline";
import { fmtTime } from "../../../utils/sessionProtocol";
import useProtocolExpansion from "./useProtocolExpansion";
import { sameProtocol } from "./protocolLibrary";

// The TTL card's Protocol tab: which library protocol this module runs
// (picking copies it into ttl.protocol; the card's Save applies it), how
// its copy compares with the library, a read-only timeline and the Test
// routine. Steps are edited in the designer modal (ProtocolDesigner).

const MODULE_ONLY = "__module_only__";

export default function ProtocolTab({
  protocol, onPick, library, scheduledPins, pinConfigs, moduleId,
  onOpenDesigner, renderTest, cursorT,
}) {
  const { pins, result, rows, spans } = useProtocolExpansion(protocol, scheduledPins, pinConfigs, moduleId);
  const items = library.items ?? [];
  const inLibrary = protocol?.name ? items.find((i) => i.name === protocol.name) : null;
  const matches = inLibrary && sameProtocol(protocol, inLibrary.protocol);

  const value = !protocol ? "" : inLibrary ? protocol.name : MODULE_ONLY;
  const pick = (v) => {
    if (v === "") onPick(null);
    else if (v !== MODULE_ONLY) {
      const it = items.find((i) => i.name === v);
      if (it) onPick(structuredClone(it.protocol));
    }
  };

  let status = null;
  if (!protocol) {
    status = <span>No protocol: Scheduled pins stay inactive during recordings.</span>;
  } else if (library.items === null) {
    status = null;
  } else if (!inLibrary) {
    status = (
      <>
        <span>
          {protocol.name ? `“${protocol.name}” isn't in the library any more` : "This protocol has no name"}:
          it only exists in this module's settings.
        </span>
        <button type="button" className="pe-small"
          onClick={() => onOpenDesigner({ protocol, name: protocol.name })}>
          Save to library…
        </button>
      </>
    );
  } else if (!matches) {
    status = (
      <>
        <span>This module has an older or edited copy of “{protocol.name}”.</span>
        <button type="button" className="pe-small" onClick={() => onPick(structuredClone(inLibrary.protocol))}>
          Use the library version
        </button>
      </>
    );
  } else {
    status = <span>Same as the library version.</span>;
  }

  return (
    <div className="pe">
      <p className="pe-intro">
        The protocol runs on this module during every recording, timed from session start on
        its PTP clock. Only pins in <strong>Scheduled</strong> mode (Pins tab) can be used, and
        manual pulses on them are refused while recording. Pick a protocol from the library,
        then Save to apply; design and edit protocols in the designer.
      </p>

      {pins.length === 0 && (
        <div className="pe-notice">No scheduled pins yet: set a pin's mode to “Scheduled (session protocol)” on the Pins tab.</div>
      )}

      <div className="pe-top">
        <label className="pe-field pt-pick">
          <span className="pe-field-label">Protocol</span>
          <select value={value} onChange={(e) => pick(e.target.value)}>
            <option value="">None</option>
            {value === MODULE_ONLY && (
              <option value={MODULE_ONLY}>{protocol.name || "Unnamed"} (only on this module)</option>
            )}
            {items.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
        </label>
        <div className="pe-top-right">
          <button type="button" className="pe-small pt-designer"
            onClick={() => onOpenDesigner(inLibrary ? { name: inLibrary.name }
              : protocol ? { protocol, name: protocol.name } : {})}>
            Open protocol designer
          </button>
        </div>
      </div>

      {status && <div className="pt-status">{status}</div>}
      {library.items !== null && items.length === 0 && !protocol && (
        <div className="pe-notice">The library is empty: open the designer to make the first protocol.</div>
      )}

      {protocol && result && (
        <div className="pe-check">
          <div className={`pe-summary${result.ok ? "" : " pe-summary--bad"}`}>
            <span className="pe-summary-icon" aria-hidden="true">{result.ok ? "✓" : "✕"}</span>
            {result.ok
              ? `Valid: ${result.edges.length} edges on ${result.pins.map((p) => `GPIO ${p}`).join(", ") || "no pins"}, last at ${fmtTime(result.durationS, result.durationS < 60)}`
              : `${result.errors.length} problem${result.errors.length === 1 ? "" : "s"} on this module: it will refuse to record. ${result.errors[0]}`}
          </div>
        </div>
      )}

      {protocol && renderTest && renderTest(result, protocol)}

      {protocol && (
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
