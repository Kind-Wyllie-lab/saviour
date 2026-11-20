import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import RigCheckList from "/src/basic/components/RigCheck/RigCheckList";
import {
  EXTERNAL_SYNC, moduleApplication, moduleKindLabel,
} from "/src/basic/utils/ttlApplications";

// Quick setup step 3 (plans/rig-quick-setup.md): the live rig check for the
// chosen external sync, the connected modules no item expects (each with
// "Add as expected"), and an "Add expected item" form. Adding writes
// `rig.expected` on the controller (add_rig_expected_item, admin only); the
// rig check updates itself.

// An external sync kind whose sync application is `app`, e.g. "ephys" for
// "ephys_sync", so a sync TTL module is expected only for those sessions.
function whenForApplication(app) {
  return Object.entries(EXTERNAL_SYNC).find(([, v]) => v.application === app)?.[0] ?? "";
}

// The item "Add as expected" proposes for a module nobody expects: its type,
// (for a TTL module) its application, and as many as are connected now.
function itemFor(m, modules) {
  const app = moduleApplication(m);
  const same = modules.filter((o) => o.type === m.type && moduleApplication(o) === app);
  const item = {
    label: moduleKindLabel(m) || m.type,
    types: [m.type],
    min: same.length,
    severity: "required",
  };
  if (app && app !== "mixed") item.application = app;
  const when = whenForApplication(app);
  if (when) item.when = when;
  return item;
}

const BLANK = { label: "", type: "", min: 1, severity: "required", when: "" };

export default function ExpectedModulesStep({ rig, conditions, modules, offline, onClose }) {
  const loggedIn = useIsLoggedIn();
  const [types, setTypes] = useState([]);
  const [form, setForm] = useState(null); // null = closed, else BLANK-shaped
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null); // {ok, text}

  useEffect(() => {
    const onTypes = (d) => setTypes(Array.isArray(d) ? d : (d?.types ?? []));
    const onAdded = (d) => {
      setBusy(false);
      setForm(null);
      setMessage({ ok: true, text: `Added “${d?.item?.label}” to this rig's expected hardware.` });
    };
    const onError = (d) => { setBusy(false); setMessage({ ok: false, text: d?.error ?? "Couldn't add it" }); };
    socket.on("module_types", onTypes);
    socket.on("rig_expected_added", onAdded);
    socket.on("rig_expected_error", onError);
    socket.emit("get_module_types");
    return () => {
      socket.off("module_types", onTypes);
      socket.off("rig_expected_added", onAdded);
      socket.off("rig_expected_error", onError);
    };
  }, []);

  // Connected modules that no expected item (active or not) counts.
  const unexpected = useMemo(() => {
    const seen = new Set();
    for (const i of rig?.items ?? []) {
      for (const who of [...(i.present ?? []), ...(i.faulty ?? []), ...(i.offline ?? [])]) seen.add(who.id);
    }
    return modules.filter((m) => !seen.has(m.id));
  }, [rig, modules]);

  const add = (item) => {
    setBusy(true);
    setMessage(null);
    socket.emit("add_rig_expected_item", { item });
  };
  const submitForm = () => {
    const item = { label: form.label.trim(), types: [form.type], min: Number(form.min) || 0,
      severity: form.severity };
    if (form.when) item.when = form.when;
    add(item);
  };
  const typeOptions = [...new Set(["*camera", ...types, ...modules.map((m) => m.type)])]
    .filter(Boolean).sort();
  const adminHint = loggedIn ? undefined : "Sign in as admin to change the expected hardware";

  return (
    <>
      <div className="qs-card">
        {rig?.items?.length ? (
          <RigCheckList rig={rig} conditions={conditions} />
        ) : (
          <p className="qs-muted">This rig doesn't list any expected hardware yet.</p>
        )}
      </div>

      {unexpected.length > 0 && (
        <div className="qs-card quick-setup__unexpected">
          <h4 className="qs-card-title">Connected, not expected</h4>
          <ul>
            {unexpected.map((m) => {
              const item = itemFor(m, modules);
              return (
                <li key={m.id}>
                  <span>
                    <strong>{m.name || m.id}</strong>{" "}
                    <span className="quick-setup__hint">{moduleKindLabel(m) || m.type}</span>
                  </span>
                  <button type="button" className="quick-setup__btn quick-setup__btn--small"
                    disabled={!loggedIn || busy} title={adminHint
                      ?? `Expect ${item.min} × ${item.label}${item.when ? ` (${EXTERNAL_SYNC[item.when]?.label} sessions)` : ""}`}
                    onClick={() => add(item)}>
                    Add as expected
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {form ? (
        <div className="quick-setup__add-form">
          <h4>Add expected item</h4>
          <div className="quick-setup__add-grid">
            <label>
              Module type
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                <option value="">Choose…</option>
                {typeOptions.map((t) => (
                  <option key={t} value={t}>{t === "*camera" ? "Any camera (*camera)" : t}</option>
                ))}
              </select>
            </label>
            <label>
              Label
              <input type="text" value={form.label} placeholder={form.type || "e.g. Overhead camera"}
                onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </label>
            <label>
              At least
              <input type="number" min={0} max={64} value={form.min}
                onChange={(e) => setForm({ ...form, min: e.target.value })} />
            </label>
            <label>
              If missing
              <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                <option value="required">Required (warn before starting)</option>
                <option value="optional">Optional (note only)</option>
              </select>
            </label>
            <label>
              Needed for
              <select value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })}>
                <option value="">Every session</option>
                {Object.entries(EXTERNAL_SYNC).map(([k, v]) => (
                  <option key={k} value={k}>{v.label} sessions only</option>
                ))}
              </select>
            </label>
          </div>
          <div className="quick-setup__add-actions">
            <button type="button" className="quick-setup__btn" onClick={() => setForm(null)}>Cancel</button>
            <button type="button" className="quick-setup__btn quick-setup__btn--primary"
              disabled={!form.type || busy} onClick={submitForm}>
              {busy ? "Adding…" : "Add"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="quick-setup__btn quick-setup__btn--small"
          disabled={!loggedIn} title={adminHint} onClick={() => { setForm(BLANK); setMessage(null); }}>
          + Add expected item
        </button>
      )}

      {message && (
        <p className={`quick-setup__msg${message.ok ? "" : " quick-setup__msg--error"}`}>{message.text}</p>
      )}

      <p className="quick-setup__hint">
        {modules.length} module{modules.length === 1 ? "" : "s"} connected
        {offline.length > 0 && `, ${offline.length} offline (${offline.map((m) => m.name || m.id).join(", ")})`}.{" "}
        {!loggedIn && "Sign in as admin to add to the list. "}
        <Link to="/settings#controller/rig" onClick={onClose}>Edit or remove items on the Rig tab →</Link>
      </p>
    </>
  );
}
