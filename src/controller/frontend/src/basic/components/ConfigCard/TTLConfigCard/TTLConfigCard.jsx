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
import useTTLLines from "/src/hooks/useTTLLines";
import {
  DEFAULT_APPLICATION, applicationOf, applicationLabel,
} from "/src/basic/utils/ttlApplications";

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
  const [hasSaved, setHasSaved] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [pinTestState, setPinTestState] = useState({});
  const protocolTest = useProtocolTest(id);
  const protocolLibrary = useProtocolLibrary();
  const [designer, setDesigner] = useState(null); // null = closed, else where it opens
  const isRecording = module.status === "RECORDING";

  const ttlCfg = formData?.ttl ?? {};
  // "Ephys sync" etc., from the sync line's system
  // (plans/ttl-applications-ephys-sync.md).
  const savedApplication = applicationOf(module.config?.ttl);
  const { lines: ttlLines } = useTTLLines(JSON.stringify(module.config?.ttl?.pins ?? {}) + savedApplication);
  const lineStatus = ttlLines?.modules?.[id] ?? null;
  const tabs = TABS;
  const availablePins = module.config?.ttl?._available_pins ?? [];
  const schema = module.config?.ttl?._mode_settings_schema ?? {};
  const activeLogicOptions = module.config?.ttl?._active_logic_options ?? ["active_low", "active_high"];

  const scheduledPinList = Object.keys(ttlCfg.pins ?? {})
    .filter((p) => ttlCfg.pins[p]?.mode === "scheduled")
    .map(Number).sort((a, b) => a - b);
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
      : result.pins.some((p) => !scheduledPinList.includes(p)) ? "Set the lines it uses to Protocol output first (Pins tab)."
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

                  <TTLLinesEditor
                    id={id}
                    module={module}
                    ttlCfg={ttlCfg}
                    schema={schema}
                    availablePins={availablePins}
                    setFormData={setFormData}
                    loggedIn={loggedIn}
                    testPin={testPin}
                    pinTestState={pinTestState}
                    status={lineStatus}
                  />
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
