import { Link } from "react-router";
import { rigShortfalls } from "/src/hooks/useRigCheck";
import { syncWarnings } from "/src/basic/utils/ttlApplications";

// "Records ephys" for a new session (plans/ttl-applications-ephys-sync.md):
// with it ticked, rig items marked when: "ephys" count (an Ephys sync module
// becomes required) and the sync wiring is recorded with the session. Shows
// what would be flagged before the user presses Start.
export default function EphysSessionField({ checked, onChange, rig, ttlLines }) {
  // Only the conditional items: the rest are flagged whether or not it's ticked.
  const missing = checked ? rigShortfalls(rig, "required", ["ephys"]).filter((i) => i.when) : [];
  const sync = checked ? syncWarnings(ttlLines) : [];
  return (
    <div className="ephys-session-field">
      <label className="ephys-session-field__check">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        Records ephys
        <span className="ephys-session-field__hint">
          {" "}(Open Ephys or a logger alongside SAVIOUR)
        </span>
      </label>
      {(missing.length > 0 || sync.length > 0) && (
        <ul className="ephys-session-field__warnings">
          {missing.map((i) => (
            <li key={i.label}>{i.label}: {i.count} of {i.min} — set a TTL module's “Used for” to Ephys sync</li>
          ))}
          {sync.map((w) => <li key={w}>{w}</li>)}
          <li className="ephys-session-field__link"><Link to="/settings">Check the lines on the TTL module's card →</Link></li>
        </ul>
      )}
    </div>
  );
}
