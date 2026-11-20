import { useEffect, useState } from "react";
import socket from "/src/socket";
import useRigCheck from "/src/hooks/useRigCheck";
import RigCheckList from "/src/basic/components/RigCheck/RigCheckList";
import { APPLICATIONS, APPLICATION_ORDER } from "/src/basic/utils/ttlApplications";

// Rig tab: the hardware this rig is expected to have (rig.expected; checked
// live by src/controller/rig_check.py and shown as the missing-hardware
// banner). Edits go into the controller config form and are saved with it.
const BLANK = { label: "", types: [], min: 1, severity: "required" };

function typesToText(types) {
  return (types || []).join(", ");
}

function textToTypes(text) {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}

export default function RigProfileSection({ rig, onChange }) {
  const live = useRigCheck();
  const [moduleTypes, setModuleTypes] = useState([]);
  const [defaults, setDefaults] = useState(null);
  // Comma-separated types are edited as raw text so typing a comma or a
  // trailing space isn't normalised away mid-word.
  const [typeText, setTypeText] = useState({});

  useEffect(() => {
    const onTypes = (d) => setModuleTypes((d?.types || []).map((t) => t.type));
    const onDefaults = (d) => setDefaults(d || {});
    socket.on("module_types", onTypes);
    socket.on("rig_defaults", onDefaults);
    socket.emit("get_module_types");
    socket.emit("get_rig_defaults");
    return () => {
      socket.off("module_types", onTypes);
      socket.off("rig_defaults", onDefaults);
    };
  }, []);

  const expected = Array.isArray(rig?.expected) ? rig.expected : [];
  const setRig = (patch) => onChange({ ...(rig || {}), ...patch });
  const setItems = (items) => setRig({ expected: items });
  const update = (idx, patch) =>
    setItems(expected.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  const remove = (idx) => {
    setItems(expected.filter((_, i) => i !== idx));
    setTypeText({});
  };
  const add = () => setItems([...expected, { ...BLANK }]);
  const resetDefaults = () => {
    if (!defaults) return;
    onChange({ ...(rig || {}), ...defaults,
      expected: (defaults.expected || []).map((e) => ({ ...e })) });
    setTypeText({});
  };

  const invalid = expected.some((e) => !(e.types || []).length);

  return (
    <div className="rig-profile-section">
      <p className="field-hint">
        What this rig should have. A module counts when it is online and reports no
        hardware fault. A missing <em>required</em> item shows a red banner on every
        page, asks for confirmation before a session starts and is noted in the
        session record; an <em>optional</em> one shows amber. Types accept wildcards:
        <code> *camera</code> matches any camera type. <em>Used for</em> narrows a TTL
        item to modules set to that job (e.g. Ephys sync); <em>Only for</em> ephys
        sessions counts the item only when a session is created with
        <em> Records ephys</em> ticked.
      </p>

      <div className="rig-profile-table-wrap">
      <table className="rig-profile-table">
        <thead>
          <tr>
            <th>Label</th>
            <th>Module types</th>
            <th>Min</th>
            <th>Severity</th>
            <th>Used for</th>
            <th>Only for</th>
            <th aria-label="Remove" />
          </tr>
        </thead>
        <tbody>
          {expected.map((e, idx) => (
            <tr key={idx}>
              <td>
                <input type="text" value={e.label ?? ""} placeholder="e.g. Shock controller"
                  onChange={(ev) => update(idx, { label: ev.target.value })} />
              </td>
              <td>
                <input type="text" list="rig-module-types"
                  className={(e.types || []).length ? "" : "rig-profile-input--invalid"}
                  value={typeText[idx] ?? typesToText(e.types)}
                  placeholder="e.g. apa_arduino"
                  onChange={(ev) => {
                    setTypeText((t) => ({ ...t, [idx]: ev.target.value }));
                    update(idx, { types: textToTypes(ev.target.value) });
                  }} />
              </td>
              <td>
                <input type="number" min={0} step={1} value={e.min ?? 1}
                  onChange={(ev) => update(idx, {
                    min: Math.max(0, parseInt(ev.target.value || "0", 10) || 0) })} />
              </td>
              <td>
                <select value={e.severity || "required"}
                  onChange={(ev) => update(idx, { severity: ev.target.value })}>
                  <option value="required">Required</option>
                  <option value="optional">Optional</option>
                </select>
              </td>
              <td>
                <select value={e.application || ""}
                  title="TTL modules only: count those set to this job"
                  onChange={(ev) => {
                    const next = { ...e, application: ev.target.value };
                    if (!ev.target.value) delete next.application;
                    setItems(expected.map((x, i) => (i === idx ? next : x)));
                  }}>
                  <option value="">Any</option>
                  {APPLICATION_ORDER.map((a) => (
                    <option key={a} value={a}>{APPLICATIONS[a].label}</option>
                  ))}
                </select>
              </td>
              <td>
                <select value={e.when || ""}
                  onChange={(ev) => {
                    const next = { ...e, when: ev.target.value };
                    if (!ev.target.value) delete next.when;
                    setItems(expected.map((x, i) => (i === idx ? next : x)));
                  }}>
                  <option value="">Every session</option>
                  <option value="ephys">Ephys sessions</option>
                </select>
              </td>
              <td>
                <button type="button" className="rig-profile-remove"
                  onClick={() => remove(idx)} title="Remove">✕</button>
              </td>
            </tr>
          ))}
          {expected.length === 0 && (
            <tr><td colSpan={7} className="rig-profile-empty">
              Nothing expected: no missing-hardware banner on this rig.
            </td></tr>
          )}
        </tbody>
      </table>
      </div>
      <datalist id="rig-module-types">
        {["*camera", ...moduleTypes].map((t) => <option key={t} value={t} />)}
      </datalist>
      {invalid && <p className="rig-profile-error">Every item needs at least one module type.</p>}

      <div className="rig-profile-actions">
        <button type="button" className="teams-test-btn" onClick={add}>Add item</button>
        <button type="button" className="teams-test-btn" onClick={resetDefaults}
          disabled={!defaults || !(defaults.expected || []).length}
          title="Replace the list with this rig type's shipped defaults (save to keep)">
          Reset to rig-type defaults
        </button>
      </div>

      <div className="threshold-field">
        <div className="form-field">
          <label>Grace after start-up (s)</label>
          <input type="number" min={0} step={10} value={rig?.startup_grace_s ?? 120}
            onChange={(ev) => setRig({ startup_grace_s: Number(ev.target.value) })} />
        </div>
        <span className="field-hint">
          After the controller (or a fleet update) restarts, wait this long for modules to
          register before calling anything missing.
        </span>
      </div>
      <div className="threshold-field">
        <div className="form-field">
          <label>Alert after (s)</label>
          <input type="number" min={0} step={60} value={rig?.alert_after_s ?? 600}
            onChange={(ev) => setRig({ alert_after_s: Number(ev.target.value) })} />
        </div>
        <span className="field-hint">
          Send one alert a day (Teams / ntfy, toggle on the Alerts tab) when a required
          item has been missing this long.
        </span>
      </div>

      {live?.items?.length > 0 && (
        <div className="rig-profile-live">
          <h4>Now (as saved)</h4>
          <RigCheckList rig={live} />
        </div>
      )}
    </div>
  );
}
