import { useState } from "react";
import socket from "../../../../socket";
import { confirmIfOtherAdmins } from "/src/clients";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./TTLConfigCard.css";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import { filterPrivateKeys, checkClipboardCompatibility } from "../configUtils";
import ExportConfigSection from "../ExportConfigSection";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import CopyActionsBar from "../CopyActionsBar";
import ConfigActionBar, { HeaderSaveButton } from "../ConfigActionBar";
import ModuleActionsMenu from "/src/basic/components/ModuleActionsMenu/ModuleActionsMenu";
import ProtocolTab from "./ProtocolTab";
import ProtocolDesigner from "../../Protocol/ProtocolDesigner";
import { useProtocolLibrary } from "../../Protocol/protocolLibrary";
import { ProtocolTestControls } from "../../Protocol/ProtocolTest";
import useProtocolTest from "../../Protocol/useProtocolTest";
import TTLLinesEditor from "./TTLLinesEditor";
import AddPinModal from "./AddPinModal";
import useTTLLines from "/src/hooks/useTTLLines";
import {
  APPLICATIONS, APPLICATION_ORDER, DEFAULT_APPLICATION, applicationOf, applicationLabel, pinLabel,
} from "/src/basic/utils/ttlApplications";

// "None" is a real output mode (a plain output pin held inactive, with no
// automatic generator) added so test_pin()/the REST /pulse endpoint have a
// pin to drive without racing experiment_clock/pseudorandom/interval_pulse.
// "experiment_start" fires a single delayed pulse once, at recording start --
// sugar over interval_pulse(repeat_count=1) with its own honest mode name and
// a simpler delay_s/pulse_duration_s-only schema (both server-driven from
// _mode_settings_schema, nothing hardcoded here beyond the label/badge).
// "scheduled" is an output driven by the session protocol (ttl.protocol,
// plans/session-protocol.md); its steps live in the protocol, not per pin.
const OUTPUT_MODES = new Set([
  "experiment_clock", "pseudorandom", "interval_pulse", "experiment_start", "None",
  "scheduled",
]);
const MODE_LABELS = {
  None: "None (manual output)",
  experiment_start: "Experiment Start (one pulse)",
  scheduled: "Scheduled (session protocol)",
};

const TAB_COPY_SECTION = {
  basic:  { key: "module",    label: "Basic"  },
  pins:   { key: "ttl",       label: "TTL"    },
  protocol: { key: "ttl",     label: "TTL"    },
  record: { key: "recording", label: "Record" },
  export: { key: "export",    label: "Export" },
};

const TABS = [
  { key: "basic",  label: "Basic"  },
  { key: "pins",   label: "Pins"   },
  { key: "protocol", label: "Protocol" },
  { key: "record", label: "Record" },
  { key: "export", label: "Export" },
];

function TTLConfigCard({ id, module, clipboard, onCopy }) {
  const loggedIn = useIsLoggedIn();
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);
  const [collapsed, setCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useHashTab("basic", TABS.map(t => t.key));
  const [addingPin, setAddingPin] = useState(false);
  const [hasSaved, setHasSaved] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [pinTestState, setPinTestState] = useState({});
  const protocolTest = useProtocolTest(id);
  const protocolLibrary = useProtocolLibrary();
  const [designer, setDesigner] = useState(null); // null = closed, else where it opens
  const isRecording = module.status === "RECORDING";

  const ttlCfg = formData?.ttl ?? {};
  // What this module is for (plans/ttl-applications-ephys-sync.md): "mixed"
  // keeps the free per-pin modes below; anything else gets role-based lines.
  const application = applicationOf(ttlCfg);
  const savedApplication = applicationOf(module.config?.ttl);
  const { lines: ttlLines } = useTTLLines(JSON.stringify(module.config?.ttl?.pins ?? {}) + savedApplication);
  const lineStatus = ttlLines?.modules?.[id] ?? null;
  const tabs = TABS.map((t) => (t.key === "pins" && application !== DEFAULT_APPLICATION
    ? { ...t, label: "Lines" } : t));
  const availablePins = module.config?.ttl?._available_pins ?? [];
  const availableModes = module.config?.ttl?._available_modes ?? [];
  const schema = module.config?.ttl?._mode_settings_schema ?? {};
  const activeLogicOptions = module.config?.ttl?._active_logic_options ?? ["active_low", "active_high"];

  const assignedPins = Object.keys(ttlCfg.pins ?? {}).map(Number);
  const unassignedPins = availablePins.filter((p) => !assignedPins.includes(p));
  const sortedPins = Object.keys(ttlCfg.pins ?? {}).sort((a, b) => Number(a) - Number(b));

  const addPin = (parsed, newMode) => {
    const defaults = schema[`_${newMode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [parsed]: {
            mode: newMode,
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
  };

  const removePin = (pin) => {
    setFormData((prev) => {
      const newPins = { ...prev.ttl.pins };
      delete newPins[pin];
      return { ...prev, ttl: { ...prev.ttl, pins: newPins } };
    });
  };

  const changeMode = (pin, mode) => {
    const defaults = schema[`_${mode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: {
            mode,
            // The label names the wire, not the mode: keep it across modes.
            ...(prev.ttl.pins[pin]?.label ? { label: prev.ttl.pins[pin].label } : {}),
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
  };

  const changeField = (pin, field, val) => {
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: { ...prev.ttl.pins[pin], [field]: val },
        },
      },
    }));
  };

  const scheduledPinList = sortedPins
    .filter((p) => ttlCfg.pins[p]?.mode === "scheduled")
    .map(Number);
  const setProtocol = (protocol) =>
    setFormData((prev) => ({ ...prev, ttl: { ...prev.ttl, protocol } }));

  // Test routine controls, for the Protocol tab and the designer. *protocol*
  // is what's shown there: the module's copy, or the designer's draft.
  const renderProtocolTest = (result, protocol) => {
    // The module checks pins against its *saved* Scheduled pins; the steps
    // themselves can be unsaved edits.
    const scheduledIn = (pins) => Object.entries(pins ?? {})
      .filter(([, c]) => c?.mode === "scheduled").map(([p]) => Number(p))
      .sort((a, b) => a - b).join(",");
    const pinsUnsaved = scheduledIn(ttlCfg.pins) !== scheduledIn(module.config?.ttl?.pins);
    const blocked = isRecording ? "Recording: the protocol is running as part of the session."
      : !module.online ? "The module is offline."
      : !loggedIn ? "Sign in as admin to run a test."
      : !result || result.edges.length === 0 ? "Add some steps first."
      : !result.ok ? "Fix the problems first."
      : pinsUnsaved ? "Save first: the module doesn't know about the Pins tab changes yet."
      : null;
    return (
      <ProtocolTestControls
        test={protocolTest.test}
        elapsed={protocolTest.elapsed}
        running={protocolTest.running}
        blocked={blocked}
        onStart={() => protocolTest.start(protocol)}
        onStop={protocolTest.stop}
      />
    );
  };

  const testPin = (pin) => {
    setPinTestState((prev) => ({ ...prev, [pin]: "testing" }));
    socket.emit("send_command", { module_id: id, type: "test_pin", params: { pin: Number(pin), duration: 5 } });
    setTimeout(() => {
      setPinTestState((prev) => ({ ...prev, [pin]: "done" }));
      setTimeout(() => setPinTestState((prev) => { const n = { ...prev }; delete n[pin]; return n; }), 2000);
    }, 5000);
  };

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData(prev => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) {
        cloned[key] = structuredClone(value);
      }
      return cloned;
    });
  };

  const saveConfig = () => {
    if (!confirmIfOtherAdmins("Save this module's config")) return;
    socket.emit("save_module_config", { id, config: filterPrivateKeys(formData) });
    setHasSaved(true);
    markSaved();
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: id });
    setShowResetConfirm(false);
    markSaved();
  };

  const [applyAllConfirm, setApplyAllConfirm] = useState(null);
  const confirmApplyToAll = () => {
    if (!applyAllConfirm) return;
    const { section, moduleType } = applyAllConfirm;
    const filtered = filterPrivateKeys(formData);
    const data = filtered?.[section];
    if (data) {
      socket.emit("apply_section_to_type", { module_type: moduleType ?? null, section, data });
    }
    setApplyAllConfirm(null);
  };

  return (
    <div className={`config-card ttl-config-card ${collapsed ? "collapsed" : ""}`}>
      <div className="card-header">
        <div className="card-header-top">
          <h3 onClick={() => setCollapsed(!collapsed)} style={{ cursor: "pointer" }}>
            {module.name || id} {collapsed ? "(+)" : "(-)"}
          </h3>
          {savedApplication !== DEFAULT_APPLICATION && (
            <span className="ttl-application-badge">{applicationLabel(savedApplication)}</span>
          )}
          <div className="card-header-actions">
            <HeaderSaveButton onSave={saveConfig} isDirty={isDirty} />
            <ModuleActionsMenu id={id} name={module.name || id} isOnline={!!module.online} />
          </div>
        </div>
        <div className="device-info">
          <span>{module.ip}</span>
          <span>{module.version}</span>
        </div>
      </div>

      {!collapsed && (
        <div className="ttl-body">
          {/* ── Left column: config ── */}
          <div className={`ttl-config-col config-form${isDirty ? " config-form--dirty" : ""}`}>

            <div className="config-tabs-layout">
            <div className="config-tabs">
              {tabs.map(t => (
                <button key={t.key} type="button"
                  className={`config-tab-btn${activeTab === t.key ? " active" : ""}`}
                  onClick={() => setActiveTab(t.key)}>
                  {t.label}
                </button>
              ))}
            </div>

            <div className="config-tab-content">

              {/* BASIC */}
              {activeTab === "basic" && (
                <>
                  <div className="form-field">
                    <label>Name:</label>
                    <input type="text"
                      value={formData?.module?.name ?? ""}
                      onChange={e => handleChange(["module", "name"], e)} />
                  </div>
                  <div className="form-field">
                    <label>Group:</label>
                    <input type="text"
                      value={formData?.module?.group ?? ""}
                      onChange={e => handleChange(["module", "group"], e)} />
                  </div>
                  <div className="form-field">
                    <label>Used for:</label>
                    <select
                      value={application}
                      onChange={(e) => setFormData((prev) => ({
                        ...prev, ttl: { ...prev.ttl, application: e.target.value } }))}
                    >
                      {APPLICATION_ORDER.map((a) => (
                        <option key={a} value={a}>{APPLICATIONS[a].label}</option>
                      ))}
                    </select>
                  </div>
                  <p className="field-hint">{APPLICATIONS[application].description}</p>
                </>
              )}

              {/* PINS */}
              {activeTab === "pins" && (
                <>
                  <div className="form-field">
                    <label>Active logic:</label>
                    <select
                      value={ttlCfg.active_logic ?? "active_low"}
                      onChange={(e) =>
                        setFormData((prev) => ({ ...prev, ttl: { ...prev.ttl, active_logic: e.target.value } }))
                      }
                    >
                      {activeLogicOptions.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  </div>

                  {application !== DEFAULT_APPLICATION ? (
                    <TTLLinesEditor
                      id={id}
                      module={module}
                      ttlCfg={{ ...ttlCfg, application }}
                      schema={schema}
                      availablePins={availablePins}
                      setFormData={setFormData}
                      loggedIn={loggedIn}
                      testPin={testPin}
                      pinTestState={pinTestState}
                      status={lineStatus}
                    />
                  ) : (<>
                  <div className="pin-list">
                    {sortedPins.length === 0 && (
                      <p className="no-pins-hint">No pins configured. Press Add pin below.</p>
                    )}
                    {sortedPins.map((pin) => {
                      const settings = ttlCfg.pins[pin];
                      const mode = settings.mode ?? "None";
                      const isOutput = OUTPUT_MODES.has(mode);
                      const modeFields = schema[`_${mode}`] ?? {};

                      return (
                        <div key={pin} className={`pin-card pin-card--${isOutput ? "output" : "input"}`}>
                          <div className="pin-card-header">
                            <span className="pin-label">{pinLabel(pin)}</span>
                            <span className={`pin-type-badge ${isOutput ? "badge--output" : "badge--input"}`}>
                              {isOutput ? "OUTPUT" : "INPUT"}
                            </span>
                            {isOutput && (
                              <button
                                className={`btn-test-pin${pinTestState[pin] === "testing" ? " btn-test-pin--active" : pinTestState[pin] === "done" ? " btn-test-pin--done" : ""}`}
                                type="button"
                                disabled={pinTestState[pin] === "testing"}
                                onClick={() => testPin(pin)}
                                title="Run a 5-second test pulse to validate the signal"
                              >
                                {pinTestState[pin] === "testing" ? "Testing…" : pinTestState[pin] === "done" ? "Done" : "Test"}
                              </button>
                            )}
                            <button className="btn-remove" onClick={() => removePin(pin)}>✕</button>
                          </div>

                          <div className="field">
                            <label>Mode</label>
                            <select value={mode} onChange={(e) => changeMode(pin, e.target.value)}>
                              {availableModes.map((m) => (
                                <option key={m} value={m}>{MODE_LABELS[m] ?? m}</option>
                              ))}
                            </select>
                          </div>

                          <div className="field">
                            <label>Label</label>
                            <input type="text" value={settings.label ?? ""}
                              placeholder={mode === "scheduled" ? "e.g. Lights (shown in protocols)" : "optional"}
                              onChange={(e) => changeField(pin, "label", e.target.value)} />
                          </div>

                          {Object.entries(modeFields).map(([k, meta]) => {
                            const fieldName = k.replace(/^_/, "");
                            return (
                              <div key={k} className="field">
                                <label>{fieldName}</label>
                                <input
                                  type={meta.type === "float" || meta.type === "int" ? "number" : "text"}
                                  min={meta.min}
                                  max={meta.max}
                                  step={meta.type === "float" ? "any" : undefined}
                                  value={settings[fieldName] ?? ""}
                                  onChange={(e) => {
                                    const raw = e.target.value;
                                    let val = raw;
                                    if (meta.type === "float") val = raw === "" ? "" : parseFloat(raw);
                                    else if (meta.type === "int") val = raw === "" ? "" : parseInt(raw, 10);
                                    changeField(pin, fieldName, Number.isNaN(val) ? raw : val);
                                  }}
                                />
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                  </div>

                  <div className="add-pin-row">
                    <button type="button" onClick={() => setAddingPin(true)}>Add pin</button>
                  </div>
                  {addingPin && (
                    <AddPinModal
                      pins={unassignedPins}
                      options={availableModes.map((m) => ({ value: m, label: MODE_LABELS[m] ?? m }))}
                      choiceLabel="Mode"
                      onAdd={addPin}
                      onClose={() => setAddingPin(false)}
                    />
                  )}
                  </>)}
                </>
              )}

              {/* PROTOCOL */}
              {activeTab === "protocol" && (
                <ProtocolTab
                  legacyProtocol={ttlCfg.protocol ?? null}
                  onClearLegacy={() => setProtocol(null)}
                  moduleId={id}
                  moduleLabel={module.name || id}
                  pinConfigs={ttlCfg.pins ?? {}}
                  scheduledPins={scheduledPinList}
                  library={protocolLibrary}
                  canEdit={loggedIn}
                  onOpenDesigner={(start, onPick) => setDesigner({ start, onPick })}
                  cursorT={protocolTest.elapsed}
                  renderTest={renderProtocolTest}
                />
              )}

              {/* RECORD */}
              {activeTab === "record" && (
                <div className="form-field">
                  <label>Segment length (mins):</label>
                  <input type="number" min="1" step="1"
                    value={formData?.recording?.segment_length_mins ?? 60}
                    onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
                </div>
              )}

              {/* EXPORT */}
              {activeTab === "export" && (
                <ExportConfigSection
                  exportConfig={formData?.export}
                  handleChange={handleChange}
                  moduleId={id}
                />
              )}
            </div>
            </div>

            <div className="config-form-actions">
              <div className="config-section-divider" />

              <CopyActionsBar
                activeTab={activeTab}
                tabSectionMap={TAB_COPY_SECTION}
                formData={formData}
                moduleType={module.type}
                moduleName={module.name || id}
                onCopy={onCopy}
                onApplyAll={setApplyAllConfirm}
              />

              {clipboard && (() => {
                const pasteError = checkClipboardCompatibility(clipboard.data, formData);
                return (
                  <div className="clipboard-bar">
                    <span className="clipboard-label">Clipboard: {clipboard.label}</span>
                    <button type="button" className="copy-btn" onClick={handlePaste} disabled={!!pasteError}>Paste</button>
                    <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
                    {pasteError && <span className="config-sync-badge config-sync-badge--failed">{pasteError}</span>}
                  </div>
                );
              })()}

              <ConfigActionBar
                onSave={saveConfig}
                onReset={() => setShowResetConfirm(true)}
                isDirty={isDirty}
                saveStatus={
                  !hasSaved ? "idle"
                  : module.config_sync_status === "PENDING" ? "saving"
                  : module.config_sync_status === "FAILED" ? "failed"
                  : module.config_sync_status === "SYNCED" ? "saved"
                  : "idle"
                }
              />
            </div>
          </div>

          {/* ── Right column: stream ── */}
          <div className="ttl-stream-col">
            <MJPEGStreamCard ip={module.ip} port={8082} />
          </div>
        </div>
      )}

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p>Reset <strong>{module.name || id}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and custom configuration will be lost.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReset}>Reset</button>
              <button className="save-button" type="button" onClick={() => setShowResetConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {applyAllConfirm && (
        <div className="modal-overlay" onClick={() => setApplyAllConfirm(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>
              Apply <strong>{applyAllConfirm.label}</strong> settings from{" "}
              <strong>{module.name || id}</strong> to all connected{" "}
              {applyAllConfirm.moduleType ? `${applyAllConfirm.moduleType} ` : ""}modules?
            </p>
            <p className="modal-subtext">
              This will overwrite the {applyAllConfirm.label.toLowerCase()} config on every{" "}
              {applyAllConfirm.moduleType ?? "module"} and save immediately - unsaved changes on other modules will be lost.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={confirmApplyToAll}
                disabled={!loggedIn} title={loggedIn ? undefined : "Login required for this action"}>Apply to All</button>
              <button className="save-button" type="button" onClick={() => setApplyAllConfirm(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {designer && (
        <ProtocolDesigner
          start={designer.start}
          library={protocolLibrary}
          moduleLabel={module.name || id}
          scheduledPins={scheduledPinList}
          pinConfigs={ttlCfg.pins ?? {}}
          moduleId={id}
          canEdit={loggedIn}
          renderTest={renderProtocolTest}
          cursorT={protocolTest.elapsed}
          useLabel="Check on this module"
          onUse={(protocol) => designer.onPick?.(protocol.name)}
          onClose={() => setDesigner(null)}
        />
      )}
    </div>
  );
}

export default TTLConfigCard;
