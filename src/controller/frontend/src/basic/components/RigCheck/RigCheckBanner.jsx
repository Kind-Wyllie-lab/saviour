import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import useRigCheck, { rigShortfalls } from "/src/hooks/useRigCheck";
import RigCheckList from "./RigCheckList";
import "./RigCheck.css";

// One-line strip under the top status bar when this rig is missing
// expected hardware (rig.expected; src/controller/rig_check.py). Red for a
// required item, amber when only optional ones are short. Click for the
// full list.
export default function RigCheckBanner() {
  const rig = useRigCheck();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!rig || (rig.level !== "error" && rig.level !== "warning")) return null;
  const missing = rigShortfalls(rig);
  const tone = rig.level === "error" ? "error" : "warning";
  const text = missing
    .map((i) => `${i.label} (${i.count} of ${i.min}${i.status === "faulty" ? ", fault" : ""})`)
    .join(", ");

  return (
    <div className={`rig-check-banner rig-check-banner--${tone}`} ref={ref}>
      <button type="button" className="rig-check-banner__btn"
        onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <strong>{tone === "error" ? "Missing hardware:" : "Optional hardware missing:"}</strong>{" "}
        {text}
        <span className="rig-check-banner__more">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="rig-check-popover" role="dialog">
          <RigCheckList rig={rig} />
          <div className="rig-check-popover__foot">
            <Link to="/system" onClick={() => setOpen(false)}>System →</Link>
            <Link to="/settings#controller/rig" onClick={() => setOpen(false)}>Edit expected hardware →</Link>
          </div>
        </div>
      )}
    </div>
  );
}
