import { useState } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import AddPinModal from "./AddPinModal";
import SyncGuideModal from "./SyncGuideModal";
import {
  APPLICATIONS, ROLES, CONFIRM_MAX_AGE_DAYS, SYNC_APPLICATIONS,
  rolesFor, roleLabel, pinLabel, channelLabel, systemName, validateTTL, DIGITAL_IN_CHANNELS,
} from "/src/basic/utils/ttlApplications";
import { formatUtc } from "/src/basic/utils/time";

// The Lines tab of a TTL module with an application other than "mixed"
// (plans/ttl-applications-ephys-sync.md): each pin is a line with a role
// (sync, experiment start, stimulus, event marker) instead of a free mode,
// and sync applications get wiring guidance plus a "seen on the acquisition
// system" confirmation per line, kept on the controller (ttl_lines.py).

const MODE_LABELS = {
  None: "Manual output",
  pseudorandom: "Pseudorandom pulses",
  experiment_start: "One pulse at start",
  interval_pulse: "Regular pulses",
  experiment_clock: "Clock",
  scheduled: "Session protocol",
  input: "Input",
};

const STATE_TEXT = {
  confirmed: "Confirmed",
  stale: "Confirm again",
  changed: "Wiring changed",
  unconfirmed: "Not confirmed",
};

function defaultsFor(schema, mode) {
  return Object.fromEntries(
    Object.entries(schema[`_${mode}`] ?? {}).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""]));
}

function formatWhen(epochS) {
  if (!epochS) return "";
  return formatUtc(epochS);
}

function ModeFields({ schema, mode, settings, onChange }) {
  return Object.entries(schema[`_${mode}`] ?? {}).map(([k, meta]) => {
    const field = k.replace(/^_/, "");
    return (
      <div key={k} className="field">
        <label title={meta.description}>{field.replace(/_/g, " ")}</label>
        <input
          type={meta.type === "float" || meta.type === "int" ? "number" : "text"}
          min={meta.min} max={meta.max}
          step={meta.type === "float" ? "any" : undefined}
          value={settings[field] ?? ""}
          onChange={(e) => {
            const raw = e.target.value;
            let val = raw;
            if (meta.type === "float") val = raw === "" ? "" : parseFloat(raw);
            else if (meta.type === "int") val = raw === "" ? "" : parseInt(raw, 10);
            onChange(field, Number.isNaN(val) ? raw : val);
          }}
        />
      </div>
    );
  });
}

export default function TTLLinesEditor({
  id, module, ttlCfg, schema, availablePins, setFormData, loggedIn,
  testPin, pinTestState, status,
}) {
  const app = ttlCfg.application;
  const spec = APPLICATIONS[app];
  const roles = rolesFor(app) ?? [];
  const isSync = SYNC_APPLICATIONS.includes(app);
  const [adding, setAdding] = useState(false);
  const pins = Object.entries(ttlCfg.pins ?? {}).sort(([a], [b]) => Number(a) - Number(b));
  const unassigned = availablePins.filter((p) => !(String(p) in (ttlCfg.pins ?? {})));
  const problems = validateTTL(ttlCfg);
  const lineState = Object.fromEntries((status?.lines ?? []).map((l) => [String(l.gpio), l]));
  const anyUnconfirmed = isSync && pins.some(([g, p]) =>
    ROLES[p.role]?.channel && lineState[g]?.state !== "confirmed");
  const [guideOpen, setGuideOpen] = useState(false);
  const savedPins = module.config?.ttl?.pins ?? {};

  const setPin = (gpio, next) => setFormData((prev) => ({
    ...prev, ttl: { ...prev.ttl, pins: { ...prev.ttl.pins, [gpio]: next } } }));

  const changeRole = (gpio, role) => {
    const pin = ttlCfg.pins[gpio];
    const modes = ROLES[role]?.modes ?? [];
    const mode = modes.includes(pin.mode) ? pin.mode : modes[0];
    const base = mode === pin.mode ? pin : { mode, ...defaultsFor(schema, mode) };
    const next = { ...base, mode, role, label: pin.label ?? "" };
    if (ROLES[role]?.channel) next.channel = pin.channel ?? "";
    else delete next.channel;
    setPin(gpio, next);
  };
  const changeMode = (gpio, mode) => {
    const pin = ttlCfg.pins[gpio];
    setPin(gpio, { role: pin.role, label: pin.label ?? "", channel: pin.channel,
      mode, ...defaultsFor(schema, mode) });
  };
  const changeField = (gpio, field, val) =>
    setPin(gpio, { ...ttlCfg.pins[gpio], [field]: val });
  const removePin = (gpio) => setFormData((prev) => {
    const next = { ...prev.ttl.pins };
    delete next[gpio];
    return { ...prev, ttl: { ...prev.ttl, pins: next } };
  });
  const addLine = (gpio, role) => {
    const mode = ROLES[role].modes[0];
    setPin(gpio, { mode, role, label: "",
      ...(ROLES[role].channel ? { channel: "" } : {}), ...defaultsFor(schema, mode) });
  };

  // A confirmation is tied to the saved wiring; unsaved edits to the line
  // would make it describe something the module isn't doing.
  const unsaved = (gpio) => {
    const a = ttlCfg.pins[gpio] ?? {}, b = savedPins[gpio] ?? {};
    return a.role !== b.role || a.mode !== b.mode || String(a.channel ?? "") !== String(b.channel ?? "");
  };
  const confirm = (gpio) => socket.emit("confirm_ttl_line", { module_id: id, gpio: Number(gpio) });
  const clear = (gpio) => socket.emit("clear_ttl_line", { module_id: id, gpio: Number(gpio) });

  return (
    <div className="ttl-lines">
      <p className="field-hint">{spec.description}</p>
      {problems.length > 0 && (
        <ul className="ttl-lines__problems">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      {isSync && status?.last_verified && (
        <p className="ttl-lines__verified">
          ✓ Sync verified by the alignment of <strong>{status.last_verified.session}</strong>
          {status.last_verified.r2 != null && <> (R² {Number(status.last_verified.r2).toFixed(6)})</>}
          {" "}on {formatWhen(status.last_verified.at)}
        </p>
      )}
      {isSync && (
        <div className="ttl-guide-bar">
          <button type="button"
            className={`copy-btn${anyUnconfirmed ? " ttl-guide-bar__btn--todo" : ""}`}
            onClick={() => setGuideOpen(true)}>
            Wiring and checking guide
          </button>
          {anyUnconfirmed && (
            <span className="ttl-guide-bar__hint">
              Some lines aren't confirmed in {systemName(app)} yet
            </span>
          )}
        </div>
      )}
      {isSync && guideOpen && (
        <SyncGuideModal app={app} lines={pins.filter(([, p]) => ROLES[p.role]?.channel)}
          onClose={() => setGuideOpen(false)} />
      )}

      <div className="pin-list">
        {pins.length === 0 && <p className="no-pins-hint">No lines yet. Press Add pin below.</p>}
        {pins.map(([gpio, pin]) => {
          const role = ROLES[pin.role];
          const isOutput = role ? role.output : pin.mode !== "input";
          const ls = lineState[gpio];
          const testing = pinTestState[gpio];
          return (
            <div key={gpio} className={`pin-card pin-card--${isOutput ? "output" : "input"}`}>
              <div className="pin-card-header">
                <span className="pin-label">{pinLabel(gpio)}</span>
                <span className="ttl-line-role">{roleLabel(pin.role)}</span>
                {isOutput && (
                  <button type="button"
                    className={`btn-test-pin${testing === "testing" ? " btn-test-pin--active" : testing === "done" ? " btn-test-pin--done" : ""}`}
                    disabled={testing === "testing" || unsaved(gpio)}
                    title={unsaved(gpio) ? "Save first" : "Run the line for 5 seconds so you can see it downstream"}
                    onClick={() => testPin(gpio)}>
                    {testing === "testing" ? "Testing…" : testing === "done" ? "Done" : "Test line"}
                  </button>
                )}
                <button className="btn-remove" type="button" onClick={() => removePin(gpio)}>✕</button>
              </div>

              <div className="field">
                <label>Role</label>
                <select value={pin.role ?? ""} onChange={(e) => changeRole(gpio, e.target.value)}>
                  {!pin.role && <option value="">Choose…</option>}
                  {roles.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                </select>
              </div>
              {role && role.modes.length > 1 && (
                <div className="field">
                  <label>Drives</label>
                  <select value={pin.mode} onChange={(e) => changeMode(gpio, e.target.value)}>
                    {role.modes.map((m) => <option key={m} value={m}>{MODE_LABELS[m] ?? m}</option>)}
                  </select>
                </div>
              )}
              {role?.channel && (
                <div className="field">
                  <label>{channelLabel(app)}</label>
                  <select value={pin.channel ?? ""}
                    onChange={(e) => changeField(gpio, "channel",
                      e.target.value === "" ? "" : parseInt(e.target.value, 10))}>
                    <option value="">Choose…</option>
                    {/* keep a saved value outside 1-8 visible rather than dropping it */}
                    {[...new Set([...DIGITAL_IN_CHANNELS,
                      ...(pin.channel ? [Number(pin.channel)] : [])])].sort((a, b) => a - b)
                      .map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              )}
              <div className="field">
                <label>Label</label>
                <input type="text" value={pin.label ?? ""} placeholder="optional"
                  onChange={(e) => changeField(gpio, "label", e.target.value)} />
              </div>
              {pin.role === "sync" ? (
                <details className="ttl-line-timing">
                  <summary>Pulse timing</summary>
                  <ModeFields schema={schema} mode={pin.mode} settings={pin}
                    onChange={(f, v) => changeField(gpio, f, v)} />
                </details>
              ) : (
                <ModeFields schema={schema} mode={pin.mode} settings={pin}
                  onChange={(f, v) => changeField(gpio, f, v)} />
              )}

              {isSync && role?.channel && (
                <div className={`ttl-line-confirm ttl-line-confirm--${ls?.state ?? "unconfirmed"}`}>
                  <span className="ttl-line-confirm__state">
                    {STATE_TEXT[ls?.state] ?? STATE_TEXT.unconfirmed}
                    {ls?.confirmed_at && ls.state !== "unconfirmed" && ` · ${formatWhen(ls.confirmed_at)}`}
                  </span>
                  <button type="button" className="copy-btn"
                    disabled={!loggedIn || unsaved(gpio) || !pin.channel}
                    title={!loggedIn ? "Sign in as admin"
                      : unsaved(gpio) ? "Save first: the confirmation is for the saved wiring"
                      : !pin.channel ? `Choose the ${channelLabel(app)} first`
                      : `You saw this line's pulses in ${systemName(app)}`}
                    onClick={() => confirm(gpio)}>
                    Seen in {systemName(app)}
                  </button>
                  {ls?.confirmed_at && loggedIn && (
                    <button type="button" className="copy-btn" onClick={() => clear(gpio)}>Clear</button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {isSync && (
        <p className="field-hint">
          A confirmation lasts {CONFIRM_MAX_AGE_DAYS} days, or until the line's pin, timing mode or
          channel changes.
        </p>
      )}

      <div className="add-pin-row">
        <button type="button" onClick={() => setAdding(true)}>Add pin</button>
      </div>
      {adding && (
        <AddPinModal
          pins={unassigned}
          options={roles.map((r) => ({ value: r, label: roleLabel(r) }))}
          choiceLabel="Role"
          onAdd={addLine}
          onClose={() => setAdding(false)}
        />
      )}
    </div>
  );
}
