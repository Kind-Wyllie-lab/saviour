import useQuickSetup, { describeRun } from "/src/hooks/useQuickSetup";
import QuickSetupButton from "./QuickSetupButton";
import "./QuickSetup.css";

// One line on the New Session form: when Quick setup last ran and how it
// went, with a button to run it (plans/rig-quick-setup.md). Informational
// only; the session copies the last run into its metadata either way.
export default function QuickSetupLastRun({ onDone }) {
  const lastRun = useQuickSetup();
  if (lastRun === undefined) return null; // not loaded yet
  const text = describeRun(lastRun);
  return (
    <div className={`quick-setup-last${text ? "" : " quick-setup-last--never"}`}>
      <span>{text ? `Quick setup: ${text}` : "Quick setup hasn't been run on this rig."}</span>
      <QuickSetupButton className="quick-setup-btn" label={text ? "Run again" : "Run quick setup"}
        onDone={onDone} />
    </div>
  );
}
