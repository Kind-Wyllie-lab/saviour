import { Link } from "react-router";
import { rigShortfalls } from "/src/hooks/useRigCheck";
import { EXTERNAL_SYNC, syncApplication, syncWarnings } from "/src/basic/utils/ttlApplications";

// "External sync" for a new session (plans/ttl-applications-ephys-sync.md):
// what records alongside SAVIOUR (Open Ephys, a neurologger) and so must get
// the sync pulses. The choice makes rig items marked `when: <kind>` count
// (e.g. an Ephys sync module becomes required) and records that kind's sync
// wiring with the session. Shows what would be flagged before Start.
export default function ExternalSyncField({ value, onChange, rig, ttlLines }) {
  const kind = EXTERNAL_SYNC[value] ? value : "";
  // Only the conditional items: the rest are flagged whatever is chosen.
  const missing = kind ? rigShortfalls(rig, "required", [kind]).filter((i) => i.when) : [];
  const sync = kind ? syncWarnings(ttlLines, syncApplication(kind)) : [];
  return (
    <div className="external-sync-field">
      <div className="form-row">
        <label htmlFor="external-sync-select">External sync</label>
        <select id="external-sync-select" value={kind} onChange={(e) => onChange(e.target.value)}>
          <option value="">None</option>
          {Object.entries(EXTERNAL_SYNC).map(([k, s]) => (
            <option key={k} value={k}>{s.label}</option>
          ))}
        </select>
      </div>
      {kind && <p className="external-sync-field__hint">{EXTERNAL_SYNC[kind].description}</p>}
      {(missing.length > 0 || sync.length > 0) && (
        <ul className="external-sync-field__warnings">
          {missing.map((i) => (
            <li key={i.label}>
              {i.label}: {i.count} of {i.min} — on a TTL module, set a sync line&apos;s “Used with” to{" "}
              {EXTERNAL_SYNC[kind].label.replace(/ \(.*\)$/, "")}
            </li>
          ))}
          {sync.map((w) => <li key={w}>{w}</li>)}
          <li className="external-sync-field__link">
            <Link to="/settings">Check the lines on the TTL module's card →</Link>
          </li>
        </ul>
      )}
    </div>
  );
}
