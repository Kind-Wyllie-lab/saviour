import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys, checkClipboardCompatibility } from "../configUtils";
import ConfigFields from "../ConfigFields";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import ExportConfigSection from "../ExportConfigSection";

const TABS = [
  { key: "basic",    label: "Basic"    },
  { key: "settings", label: "Settings" },
  { key: "export",   label: "Export"   },
];

function GenericConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [hasSaved, setHasSaved] = useState(false);
  const [applyAllConfirm, setApplyAllConfirm] = useState(null);
  const [activeTab, setActiveTab] = useState("basic");
  const { updateStatus, handleUpdate } = useModuleUpdate(module.id);

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

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

  // Settings tab: all sections except module, export, recording (rendered in their own tabs)
  const settingsData = (() => {
    if (!formData) return formData;
    const { module: _m, export: _e, recording: _r, ...rest } = filterPrivateKeys(formData) ?? {};
    return rest;
  })();

  const settingsSections = Object.keys(settingsData ?? {}).filter(
    k => settingsData[k] !== null && typeof settingsData[k] === "object"
  );

  const capitalize = s => s.charAt(0).toUpperCase() + s.slice(1);

  const handleSave = () => {
    setHasSaved(true);
    socket.emit("save_module_config", { id, config: filterPrivateKeys(formData) });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: module.id });
    setShowResetConfirm(false);
  };

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

  const handleReboot = () => {
    socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} });
    setShowRebootConfirm(false);
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip === "string" && module.ip && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>SAVIOUR {module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">
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

          <div className="config-tabs">
            {TABS.map(t => (
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
                <div className="config-section-divider" />
                <div className="form-field">
                  <label>Segment length (mins):</label>
                  <input type="number" min="1" step="1"
                    value={formData?.recording?.segment_length_mins ?? 60}
                    onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
                </div>
              </>
            )}

            {/* SETTINGS */}
            {activeTab === "settings" && (
              <form>
                <ConfigFields data={settingsData} handleChange={handleChange} />
              </form>
            )}

            {/* EXPORT */}
            {activeTab === "export" && (
              <ExportConfigSection
                exportConfig={formData?.export}
                handleChange={handleChange}
                moduleId={module.id}
              />
            )}
          </div>

          <div className="config-section-divider" />

          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            {settingsSections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => onCopy({ label: `${capitalize(key)} — ${module.name}`, data: { [key]: formData[key] } })}>
                {capitalize(key)}
              </button>
            ))}
            {formData?.export && (
              <button type="button" className="copy-btn"
                onClick={() => onCopy({ label: `Export — ${module.name}`, data: { export: formData.export } })}>
                Export
              </button>
            )}
            <button type="button" className="copy-btn"
              onClick={() => onCopy({ label: `All — ${module.name}`, data: filterPrivateKeys(formData) })}>
              All
            </button>
          </div>

          {settingsSections.length > 0 && (
            <>
              <div className="copy-bar">
                <span className="copy-bar-label">Apply to all {module.type}s:</span>
                {settingsSections.map(key => (
                  <button key={key} type="button" className="copy-btn"
                    onClick={() => setApplyAllConfirm({ section: key, label: capitalize(key), moduleType: module.type })}>
                    {capitalize(key)}
                  </button>
                ))}
                {formData?.export && (
                  <button type="button" className="copy-btn"
                    onClick={() => setApplyAllConfirm({ section: "export", label: "Export", moduleType: module.type })}>
                    Export
                  </button>
                )}
              </div>
              <div className="copy-bar">
                <span className="copy-bar-label">Apply to all modules:</span>
                {formData?.export && (
                  <button type="button" className="copy-btn"
                    onClick={() => setApplyAllConfirm({ section: "export", label: "Export", moduleType: null })}>
                    Export
                  </button>
                )}
              </div>
            </>
          )}

          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave}>
              Save Config
            </button>
            <button className="reset-button" type="button" onClick={() => setShowResetConfirm(true)}>
              Reset to Default
            </button>
          </div>
          {hasSaved && module.config_sync_status === "PENDING" && (
            <span className="config-sync-badge config-sync-badge--pending">Saving...</span>
          )}
          {hasSaved && module.config_sync_status === "SYNCED" && (
            <span className="config-sync-badge config-sync-badge--synced">Saved</span>
          )}
          {hasSaved && module.config_sync_status === "FAILED" && (
            <span className="config-sync-badge config-sync-badge--failed">Save failed</span>
          )}
        </div>

        {module.type.includes("camera") && (
          <div className="livestream-wrapper">
            <LivestreamCard module={module} />
          </div>
        )}
      </div>

      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleUpdate} disabled={updateStatus === "updating"}>
          {updateStatus === "updating" ? "Updating…" : "Update Saviour Version"}
        </button>
        {updateStatus && updateStatus !== "updating" && (
          <span className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
            {updateStatus.success ? `Updated: ${updateStatus.output}` : `Update failed: ${updateStatus.output}`}
          </span>
        )}
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={() => setShowRebootConfirm(true)}>
          Reboot Module
        </button>
      </div>

      {showRebootConfirm && (
        <div className="modal-overlay" onClick={() => setShowRebootConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot <strong>{module.name}</strong>?</p>
            <p className="modal-subtext">The module will restart and reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReboot}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setShowRebootConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reset <strong>{module.name}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and any custom configuration will be lost.</p>
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
              <strong>{module.name}</strong> to all connected{" "}
              {applyAllConfirm.moduleType ? `${applyAllConfirm.moduleType} ` : ""}modules?
            </p>
            <p className="modal-subtext">
              This will overwrite the {applyAllConfirm.label.toLowerCase()} config on every{" "}
              {applyAllConfirm.moduleType ?? "module"} and save immediately — unsaved changes on other modules will be lost.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={confirmApplyToAll}>Apply to All</button>
              <button className="reset-button" type="button" onClick={() => setApplyAllConfirm(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default GenericConfigCard;
