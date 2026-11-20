import "./RigCheck.css";

const STATUS_TEXT = {
  ok: "OK",
  pending: "Waiting",
  short: "Missing",
  faulty: "Fault",
};

function names(list) {
  return list.map((m) => m.name || m.id).join(", ");
}

// Every expected item with its count, faulty modules (and why) and offline
// ones. Used by the banner's popover, the System page and the New Session
// form.
// `conditions`: judge conditional items (when: "ephys") as for a session with
// these; without them those items are listed greyed, "ephys sessions only".
export default function RigCheckList({ rig, onlyProblems = false, conditions = [] }) {
  const isActive = (i) => !i.when || conditions.includes(i.when);
  const items = (rig?.items || []).filter(
    (i) => !onlyProblems || (isActive(i) && (i.status === "short" || i.status === "faulty")));
  if (!items.length) return null;
  return (
    <ul className="rig-check-list">
      {items.map((i) => {
        const active = isActive(i);
        const bad = active && (i.status === "short" || i.status === "faulty");
        const tone = !active ? "inactive" : !bad ? i.status : i.severity === "required" ? "error" : "warning";
        return (
          <li key={i.label} className={`rig-check-item rig-check-item--${tone}`}>
            <span className="rig-check-item__dot" aria-hidden="true" />
            <span className="rig-check-item__label">{i.label}</span>
            <span className="rig-check-item__count">
              {i.count} of {i.min}
              {i.severity === "optional" && <span className="rig-check-item__opt"> · optional</span>}
              {i.when && <span className="rig-check-item__opt"> · {i.when} sessions only</span>}
            </span>
            <span className="rig-check-item__status">
              {active ? (STATUS_TEXT[i.status] ?? i.status) : "Not needed"}
            </span>
            {(i.faulty.length > 0 || i.offline.length > 0 || (bad && i.present.length > 0)) && (
              <div className="rig-check-item__detail">
                {i.faulty.map((f) => (
                  <div key={f.id}>{f.name || f.id}: {f.reason}</div>
                ))}
                {i.offline.length > 0 && <div>Offline: {names(i.offline)}</div>}
                {bad && i.present.length > 0 && <div>Working: {names(i.present)}</div>}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const LEVEL_TEXT = {
  ok: "All expected hardware present",
  pending: "Waiting for modules to register",
  warning: "Optional hardware missing",
  error: "Required hardware missing",
};

// Titled block for the System page: nothing when the rig expects nothing.
export function RigCheckSummary({ rig }) {
  if (!rig?.items?.length) return null;
  return (
    <section className="rig-check-summary">
      <h3 className="rig-check-summary__title">
        Expected hardware — {LEVEL_TEXT[rig.level] ?? rig.summary}
      </h3>
      <RigCheckList rig={rig} />
    </section>
  );
}
