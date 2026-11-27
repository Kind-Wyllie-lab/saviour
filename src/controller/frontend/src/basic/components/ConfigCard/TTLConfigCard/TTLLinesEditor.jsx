import { useState } from "react";
import socket from "/src/socket";
import AddPinModal from "./AddPinModal";
import SyncGuideModal from "./SyncGuideModal";
import {
  ROLES, USES, SYNC_SYSTEMS, CONFIRM_MAX_AGE_DAYS, SYNC_APPLICATIONS,
  roleLabel, pinLabel, pinRole, pinSystem, applicationOf, channelLabel, systemName,
  validateTTL, DIGITAL_IN_CHANNELS,
} from "/src/basic/utils/ttlApplications";
import { formatUtc } from "/src/basic/utils/time";

// The TTL card's Pins tab (plans/ttl-applications-ephys-sync.md). Each pin
// has a use: a sync line (pseudorandom pulses another system records), an
// experiment marker (active for the whole recording) or a protocol output
// (driven by the session protocol, offered in the Protocol designer under
// its label), plus less common uses under "More". A sync line can say which
// system it feeds (Open Ephys / Neurologger): that's what makes the module
// an "Ephys sync" module for rig checks and sessions, and brings the wiring
// guide and the "seen on the acquisition system" confirmations (kept on the
// controller, ttl_lines.py).

const MORE = Object.keys(ROLES).filter((r) => !USES.includes(r));

const LABEL_HINT = {
  protocol: "e.g. LED bar (the name in protocols)",
  sync: "optional, e.g. Sync to Open Ephys",
  experiment_marker: "optional, e.g. Recording on",
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
  const app = applicationOf(ttlCfg);
  const isSync = SYNC_APPLICATIONS.includes(app);
  const [adding, setAdding] = useState(false);
  const pins = Object.entries(ttlCfg.pins ?? {}).sort(([a], [b]) => Number(a) - Number(b));
  const unassigned = availablePins.filter((p) => !(String(p) in (ttlCfg.pins ?? {})));
  const problems = validateTTL(ttlCfg);
  const lineState = Object.fromEntries((status?.lines ?? []).map((l) => [String(l.gpio), l]));
  // Lines wired to the acquisition system: confirmations and the guide.
  const wired = isSync ? pins.filter(([, p]) => ROLES[pinRole(p)]?.channel) : [];
  const anyUnconfirmed = wired.some(([g]) => lineState[g]?.state !== "confirmed");
  const [guideOpen, setGuideOpen] = useState(false);
  const savedTtl = module.config?.ttl ?? {};
  const savedPins = savedTtl.pins ?? {};

  const setPin = (gpio, next) => setFormData((prev) => ({
    ...prev, ttl: { ...prev.ttl, pins: { ...prev.ttl.pins, [gpio]: next } } }));

  const changeUse = (gpio, role) => {
    const pin = ttlCfg.pins[gpio];
    const mode = ROLES[role].modes[0];
    const base = mode === pin.mode ? pin : { mode, ...defaultsFor(schema, mode) };
    const { role: _legacy, channel: _c, system: _s, ...rest } = base;
    const next = { ...rest, mode, label: pin.label ?? "" };
    if (ROLES[role].channel && pin.channel) next.channel = pin.channel;
    if (role === "sync") next.system = pinSystem(pin, ttlCfg) ?? "";
    setPin(gpio, next);
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
    setPin(gpio, { mode, label: "", ...(role === "sync" ? { system: "" } : {}),
      ...defaultsFor(schema, mode) });
  };

  // A confirmation is tied to the saved wiring; unsaved edits to the line
  // would make it describe something the module isn't doing.
  const unsaved = (gpio) => {
    const a = ttlCfg.pins[gpio] ?? {}, b = savedPins[gpio] ?? {};
    return a.mode !== b.mode || String(a.channel ?? "") !== String(b.channel ?? "")
      || pinSystem(a, ttlCfg) !== pinSystem(b, savedTtl);
  };
  const confirm = (gpio) => socket.emit("confirm_ttl_line", { module_id: id, gpio: Number(gpio) });
  const clear = (gpio) => socket.emit("clear_ttl_line", { module_id: id, gpio: Number(gpio) });

  const useOptions = (
    <>
      {USES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
      <optgroup label="More">
        {MORE.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
      </optgroup>
    </>
  );

  return (
    <div className="ttl-lines">
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
              Some lines aren&apos;t confirmed in {systemName(app)} yet
            </span>
          )}
        </div>
      )}
      {isSync && guideOpen && (
        <SyncGuideModal app={app}
          lines={wired.map(([g, p]) => [g, { ...p, role: pinRole(p) }])}
          onClose={() => setGuideOpen(false)} />
      )}

      <div className="pin-list">
        {pins.length === 0 && <p className="no-pins-hint">No pins yet. Press Add pin below.</p>}
        {pins.map(([gpio, pin]) => {
          const roleKey = pinRole(pin);
          const role = ROLES[roleKey];
          const isOutput = role ? role.output : pin.mode !== "input";
          const system = pinSystem(pin, ttlCfg);
          const ls = lineState[gpio];
          const testing = pinTestState[gpio];
          const showChannel = role?.channel && (roleKey === "sync" ? !!system : isSync);
          const confirmable = isSync && showChannel;
          return (
            <div key={gpio} className={`pin-card pin-card--${isOutput ? "output" : "input"}`}>
              <div className="pin-card-header">
                <span className="pin-label">{pinLabel(gpio)}</span>
                <span className="ttl-line-role">
                  {pin.label || roleLabel(roleKey)}
                </span>
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
                <label>Use</label>
                <select value={role ? roleKey : ""} onChange={(e) => changeUse(gpio, e.target.value)}>
                  {!role && <option value="">{pin.mode} (unknown)</option>}
                  {useOptions}
                </select>
              </div>
              {role && <p className="field-hint ttl-use-hint">{role.description}</p>}

              {roleKey === "sync" && (
                <div className="field">
                  <label title="Optional. Naming the system adds its wiring guide and line checks, and lets sessions with External sync find this module.">
                    Used with
                  </label>
                  <select value={system ?? ""} onChange={(e) => changeField(gpio, "system", e.target.value)}>
                    <option value="">Not specified</option>
                    {Object.entries(SYNC_SYSTEMS).map(([k, s]) => (
                      <option key={k} value={k}>{s.label}</option>
                    ))}
                  </select>
                </div>
              )}
              {showChannel && (
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
                <input type="text" value={pin.label ?? ""}
                  placeholder={LABEL_HINT[roleKey] ?? "optional"}
                  onChange={(e) => changeField(gpio, "label", e.target.value)} />
              </div>
              {roleKey === "sync" ? (
                <details className="ttl-line-timing">
                  <summary>Pulse timing</summary>
                  <ModeFields schema={schema} mode={pin.mode} settings={pin}
                    onChange={(f, v) => changeField(gpio, f, v)} />
                </details>
              ) : (
                <ModeFields schema={schema} mode={pin.mode} settings={pin}
                  onChange={(f, v) => changeField(gpio, f, v)} />
              )}

              {confirmable && (
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
          A confirmation lasts {CONFIRM_MAX_AGE_DAYS} days, or until the line&apos;s pin, timing mode or
          channel changes.
        </p>
      )}

      <div className="add-pin-row">
        <button type="button" onClick={() => setAdding(true)}>Add pin</button>
      </div>
      {adding && (
        <AddPinModal
          pins={unassigned}
          options={[...USES, ...MORE].map((r) => ({ value: r, label: roleLabel(r) }))}
          choiceLabel="Use"
          onAdd={addLine}
          onClose={() => setAdding(false)}
        />
      )}
    </div>
  );
}
