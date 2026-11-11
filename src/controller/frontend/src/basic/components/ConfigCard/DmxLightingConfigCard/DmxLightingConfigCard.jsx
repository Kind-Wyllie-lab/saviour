import { useEffect, useMemo, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import ConfigCardShell from "../ConfigCardShell";
import ExportConfigSection from "../ExportConfigSection";
import SchedulePreview from "./SchedulePreview";
import LightingStatus, { LightingSummary } from "./LightingStatus";
import useLightingState from "/src/hooks/useLightingState";
import { validate, channelMap, nextAddresses, CURVES } from "./lightingSchedule";
import "./DmxLightingConfigCard.css";

// Habitat lighting (dmx_lighting module): schedule with a 24 h preview,
// compartment patch (box -> DMX addresses) with strip tests, and live
// status with overrides. Plan: plans/dmx-lighting-module.md.

const TABS = [
  { key: "schedule", label: "Schedule" },
  { key: "compartments", label: "Compartments" },
  { key: "status", label: "Status" },
  { key: "basic", label: "Basic" },
  { key: "export", label: "Export" },
];

const TAB_COPY_SECTION = {
  basic: { key: "module", label: "Basic" },
  schedule: { key: "dmx_lighting", label: "Lighting" },
  compartments: { key: "dmx_lighting", label: "Lighting" },
  export: { key: "export", label: "Export" },
};

const CURVE_HELP = {
  perceptual: "Even steps in perceived brightness (looks like a steady dawn)",
  cosine: "Eases in and out, in output units",
  linear: "Straight line in output units (looks like it jumps on from dark)",
};

const TIMEZONES = ["", "UTC", "Europe/London"];

function setIn(obj, path, value) {
  const out = structuredClone(obj ?? {});
  let p = out;
  for (let i = 0; i < path.length - 1; i++) {
    if (p[path[i]] == null || typeof p[path[i]] !== "object") p[path[i]] = {};
    p = p[path[i]];
  }
  p[path[path.length - 1]] = value;
  return out;
}

const toNum = (v) => (v === "" ? "" : Number(v));
const toAddr = (v) => (v === "" ? null : Number.parseInt(v, 10));

function DmxLightingConfigCard({ id, module, clipboard, onCopy }) {
  const loggedIn = useIsLoggedIn();
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);
  const [activeTab, setActiveTab] = useHashTab("schedule", TABS.map((t) => t.key));
  const { state, send, lastResult } = useLightingState(module.id);
  const [testing, setTesting] = useState(null);   // "A1.visible"

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  const cfg = useMemo(() => formData?.dmx_lighting ?? {}, [formData]);
  const sched = cfg.schedule ?? {};
  const bits = cfg.channel_bits === 16 ? 16 : 8;
  const compartments = useMemo(() => cfg.compartments ?? [], [cfg]);
  const checks = useMemo(() => validate(cfg), [cfg]);
  const scheduleValid = checks.general.length === 0;
  const chMap = useMemo(() => channelMap(compartments, bits), [compartments, bits]);
  const savedBoxes = (module.config?.dmx_lighting?.compartments ?? []).map((r) => r.box);

  const set = (path, value) => setFormData((prev) => setIn(prev, ["dmx_lighting", ...path], value));
  const setRow = (i, key, value) => set(["compartments"],
    compartments.map((r, k) => (k === i ? { ...r, [key]: value } : r)));
  const addRow = () => {
    const [v, ir] = nextAddresses(compartments, bits);
    set(["compartments"], [...compartments, { box: "", visible: v ?? null, ir: ir ?? null }]);
  };
  const removeRow = (i) => set(["compartments"], compartments.filter((_, k) => k !== i));

  const runTest = (box, role) => {
    send("test_strip", { box, role, seconds: 30 });
    setTesting(`${box}.${role}`);
    setTimeout(() => setTesting((t) => (t === `${box}.${role}` ? null : t)), 30000);
  };

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData((prev) => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) cloned[key] = structuredClone(value);
      return cloned;
    });
  };

  const field = (label, path, props = {}, help) => {
    const value = path.reduce((o, k) => o?.[k], cfg);
    return (
      <div className="dmx-fblock">
        <label>{label}</label>
        <input {...props} value={value ?? ""}
          onChange={(e) => set(path, props.type === "number" ? toNum(e.target.value) : e.target.value)} />
        {help && <span className="dmx-help">{help}</span>}
      </div>
    );
  };

  return (
    <ConfigCardShell
      id={id}
      module={module}
      formData={formData}
      clipboard={clipboard}
      onCopy={onCopy}
      onPaste={handlePaste}
      tabs={TABS}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      tabSectionMap={TAB_COPY_SECTION}
      markSaved={markSaved}
      isDirty={isDirty}
      saveDisabled={checks.count > 0}
      tabBadges={{ compartments: Object.keys(checks.rows).length ? "⚠" : "" }}
      deviceInfoExtras={[state ? <LightingSummary key="s" state={state} /> : null]}
    >
      <div className="dmx-card">
        {checks.count > 0 && activeTab !== "status" && (
          <div className="dmx-problems">
            <p><b>Can't save yet:</b></p>
            {checks.general.map((e) => <p key={e}>{e}</p>)}
            {Object.keys(checks.rows).length > 0 && <p>Compartments tab: {Object.values(checks.rows).flat().length} problem(s)</p>}
          </div>
        )}

        {activeTab === "schedule" && (
          <>
            <div className="dmx-grid">
              {field("Lights on", ["schedule", "lights_on"], { type: "time" })}
              {field("Lights off", ["schedule", "lights_off"], { type: "time" },
                "Either order: on 19:00 / off 07:00 is a reversed cycle")}
              {field("Sunrise (min)", ["schedule", "sunrise_minutes"], { type: "number", min: 0, max: 720 },
                "Ramp starts at lights on")}
              {field("Sunset (min)", ["schedule", "sunset_minutes"], { type: "number", min: 0, max: 720 },
                "Ramp starts at lights off")}
              {field("Day level (%)", ["schedule", "day_level"], { type: "number", min: 0, max: 100 })}
              {field("Night level (%)", ["schedule", "night_level"], { type: "number", min: 0, max: 100 })}
              <div className="dmx-fblock">
                <label>Ramp curve</label>
                <select value={sched.curve ?? "perceptual"} onChange={(e) => set(["schedule", "curve"], e.target.value)}>
                  {CURVES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <span className="dmx-help">{CURVE_HELP[sched.curve ?? "perceptual"]}</span>
              </div>
              <div className="dmx-fblock">
                <label>Time zone</label>
                <select value={cfg.timezone ?? ""} onChange={(e) => set(["timezone"], e.target.value)}>
                  {[...new Set([...TIMEZONES, cfg.timezone ?? ""])].map((tz) => (
                    <option key={tz} value={tz}>{tz || "Module's system zone"}</option>
                  ))}
                </select>
                <span className="dmx-help">Europe/London follows daylight saving (the dark phase is 1 h shorter/longer on the change days); UTC never shifts.</span>
              </div>
            </div>

            <div className="config-section-divider" />
            <div className="dmx-grid">
              <div className="dmx-fblock">
                <label>IR strips</label>
                <select value={cfg.ir?.mode ?? "constant"} onChange={(e) => set(["ir", "mode"], e.target.value)}>
                  <option value="constant">On all the time</option>
                  <option value="night_only">Night only (ramps opposite the visible)</option>
                  <option value="off">Off</option>
                </select>
                <span className="dmx-help">Rodents can't see near-IR, and the cameras need it at night. 850 nm emitters glow faintly red; 940 nm don't.</span>
              </div>
              {field("IR level (%)", ["ir", "level"], { type: "number", min: 0, max: 100 })}
            </div>

            <SchedulePreview cfg={cfg} valid={scheduleValid} />

            <details className="dmx-advanced">
              <summary>Safety and hardware</summary>
              <div className="dmx-grid">
                {field("Fade limit (s for 0-100 %)", ["slew_full_scale_s"], { type: "number", min: 0, max: 3600 },
                  "After a restart, clock step or mid-day edit, the schedule fades to the right level no faster than this. Overrides ignore it.")}
                {field("Max override (min)", ["max_override_minutes"], { type: "number", min: 1, max: 1440 })}
                {field("Serial port", ["serial_port"], { type: "text", placeholder: "auto (/dev/serial/by-id/*DMX_USB_PRO*)" })}
                <div className="dmx-fblock">
                  <label>Wait for synced clock</label>
                  <input type="checkbox" checked={cfg.require_synced_clock ?? true}
                    onChange={(e) => set(["require_synced_clock"], e.target.checked)} />
                  <span className="dmx-help">Don't drive the lights until PTP or NTP has set the clock (a Pi with no RTC battery boots at a stale time).</span>
                </div>
                <div className="dmx-fblock">
                  <label>Mock widget</label>
                  <input type="checkbox" checked={cfg.mock?.enabled ?? false}
                    onChange={(e) => set(["mock", "enabled"], e.target.checked)} />
                  <span className="dmx-help">Simulated DMX USB Pro, for testing without hardware.</span>
                </div>
              </div>
            </details>
          </>
        )}

        {activeTab === "compartments" && (
          <>
            <div className="dmx-row">
              <label className="dmx-field dmx-field--inline">Decoder mode
                <select value={bits} onChange={(e) => set(["channel_bits"], Number(e.target.value))}>
                  <option value={8}>8-bit (1 channel per strip)</option>
                  <option value={16}>16-bit (2 channels per strip, smoother dawn)</option>
                </select>
              </label>
              <span className="dmx-muted">Addresses are DMX start addresses (1-512), as set on the decoders.</span>
            </div>
            <table className="dmx-table dmx-patch">
              <thead>
                <tr><th>Box</th><th>Visible addr</th><th>IR addr</th><th>Trim %</th><th>Test</th><th /></tr>
              </thead>
              <tbody>
                {compartments.map((r, i) => {
                  const saved = savedBoxes.includes(r.box) && !isDirty;
                  return (
                    <tr key={i} className={checks.rows[i] ? "dmx-patch__row--bad" : ""}>
                      <td><input type="text" value={r.box ?? ""} placeholder="A1" onChange={(e) => setRow(i, "box", e.target.value)} /></td>
                      <td><input type="number" min="1" max="512" value={r.visible ?? ""} onChange={(e) => setRow(i, "visible", toAddr(e.target.value))} /></td>
                      <td><input type="number" min="1" max="512" value={r.ir ?? ""} onChange={(e) => setRow(i, "ir", toAddr(e.target.value))} /></td>
                      <td><input type="number" min="0" max="100" value={r.trim ?? 100} onChange={(e) => setRow(i, "trim", toNum(e.target.value))} /></td>
                      <td className="dmx-patch__tests">
                        {["visible", "ir"].map((role) => (
                          <button key={role} type="button" className="dmx-btn dmx-btn--small"
                            disabled={!loggedIn || !saved || !Number.isInteger(r[role])}
                            title={!loggedIn ? "Login required" : !saved ? "Save first: the test uses the module's saved patch" : `Flash ${r.box} ${role} for 30 s`}
                            onClick={() => runTest(r.box, role)}>
                            {testing === `${r.box}.${role}` ? "Flashing…" : role === "ir" ? "IR" : "Visible"}
                          </button>
                        ))}
                      </td>
                      <td><button type="button" className="dmx-btn dmx-btn--small" onClick={() => removeRow(i)} aria-label={`Remove ${r.box}`}>✕</button></td>
                    </tr>
                  );
                })}
                {compartments.map((_, i) => checks.rows[i] && (
                  <tr key={`err-${i}`} className="dmx-patch__errors"><td colSpan={6}>Row {i + 1}: {checks.rows[i].join("; ")}</td></tr>
                ))}
              </tbody>
            </table>
            <div className="dmx-row">
              <button type="button" className="dmx-btn" onClick={addRow}>Add compartment</button>
              <span className="dmx-muted">{compartments.length} compartments, {chMap.length} of 512 channels</span>
            </div>
            {lastResult?.command === "test_strip" && lastResult.error && <div className="dmx-error">{lastResult.error}</div>}
            {chMap.length > 0 && (
              <details className="dmx-advanced">
                <summary>Channel map</summary>
                <table className="dmx-table dmx-chmap">
                  <thead><tr><th>Channel</th><th>Box</th><th>Strip</th><th>Byte</th></tr></thead>
                  <tbody>
                    {chMap.map((c) => (
                      <tr key={`${c.channel}-${c.box}-${c.role}`}><td>{c.channel}</td><td>{c.box}</td><td>{c.role}</td><td>{c.part}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </>
        )}

        {activeTab === "status" && <LightingStatus moduleId={module.id} boxes={savedBoxes} />}

        {activeTab === "basic" && (
          <>
            <div className="dmx-fblock">
              <label>Name:</label>
              <input type="text" value={formData?.module?.name ?? ""}
                onChange={(e) => setFormData((p) => setIn(p, ["module", "name"], e.target.value))} />
            </div>
            <div className="dmx-fblock">
              <label>Group:</label>
              <input type="text" value={formData?.module?.group ?? ""}
                onChange={(e) => setFormData((p) => setIn(p, ["module", "group"], e.target.value))} />
            </div>
            <p className="dmx-muted">This module never changes the lights because a session started; it joins sessions only to log a lighting CSV. Its settings stay editable while it records.</p>
          </>
        )}

        {activeTab === "export" && (
          <ExportConfigSection
            exportConfig={formData?.export}
            handleChange={handleChange}
            moduleId={module.id}
          />
        )}
      </div>
    </ConfigCardShell>
  );
}

export default DmxLightingConfigCard;
