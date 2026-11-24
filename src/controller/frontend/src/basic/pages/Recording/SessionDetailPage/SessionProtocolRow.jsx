import { useMemo, useState } from "react";
import ProtocolTimeline from "/src/basic/components/ProtocolTimeline/ProtocolTimeline";
import { expand, fmtTime } from "/src/basic/utils/sessionProtocol";
import { timelineOf } from "/src/basic/components/Protocol/useProtocolExpansion";

// "Protocol" in the session's details: the copy taken when the session was
// created (later library edits don't change it), with its planned timeline.
// Planned vs what each module actually fired comes next (session-protocol
// plan); each TTL module exports that as <csv>_protocol.json + its CSV.
export default function SessionProtocolRow({ protocol }) {
  const [open, setOpen] = useState(false);
  const result = useMemo(() => expand(protocol), [protocol]);
  const { rows, spans } = useMemo(() => timelineOf(result), [result]);
  const steps = protocol?.steps?.length ?? 0;
  return (
    <>
      <span className="session-meta-label">Protocol</span>
      <span>
        {protocol.name || "Unnamed"} · {steps} step{steps === 1 ? "" : "s"}, last at {fmtTime(result.durationS, result.durationS < 60)}
        {" "}
        <button type="button" className="session-protocol-toggle" onClick={() => setOpen(!open)}>
          {open ? "Hide timeline" : "Show timeline"}
        </button>
      </span>
      {open && (
        <div className="session-protocol-timeline">
          <ProtocolTimeline rows={rows} spans={spans} duration={result.durationS}
            emptyText="This protocol has no steps." />
        </div>
      )}
    </>
  );
}
