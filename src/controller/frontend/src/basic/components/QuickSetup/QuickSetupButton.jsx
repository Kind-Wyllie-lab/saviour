import { useState } from "react";
import useQuickSetup, { describeRun } from "/src/hooks/useQuickSetup";
import QuickSetupModal from "./QuickSetupModal";
import "./QuickSetup.css";

// "Quick setup" button + its modal (plans/rig-quick-setup.md). Drop it in a
// header; `className` styles the button like its neighbours.
// `onDone({externalSync})`: after Done, e.g. so an open New Session form
// picks up the external sync just chosen.
export default function QuickSetupButton({ className = "quick-setup-btn", label = "Quick setup", onDone }) {
  const [open, setOpen] = useState(false);
  const lastRun = useQuickSetup();
  const last = describeRun(lastRun);
  return (
    <>
      <button
        type="button"
        className={className}
        onClick={() => setOpen(true)}
        title={last ? `Walk through the rig before recording. Last run: ${last}`
          : "Walk through the rig before recording: rig type, external sync, expected modules, each module's preview"}
      >
        {label}
      </button>
      {open && <QuickSetupModal lastRun={lastRun} onClose={() => setOpen(false)} onDone={onDone} />}
    </>
  );
}
