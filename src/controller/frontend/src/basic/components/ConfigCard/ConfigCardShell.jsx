import { useState } from "react";
import socket from "/src/socket";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import { filterPrivateKeys, checkClipboardCompatibility } from "./configUtils";
import CopyActionsBar from "./CopyActionsBar";

/**
 * ConfigCardShell — shared boilerplate wrapper for all module config cards.
 *
 * Props:
 *   id              string        module id
 *   module          object        full module object
 *   formData        object        from useConfigForm in the parent
 *   clipboard       object|null   clipboard state
 *   onCopy          function      copy handler
 *   onPaste         function      paste handler (called with no args)
 *   tabs            array         [{key, label}]
 *   activeTab       string        currently active tab key
 *   onTabChange     function      called with new tab key
 *   tabSectionMap   object        passed to CopyActionsBar
 *   saveDisabled    bool          optional, default false
 *   saveTransform   function      optional — (formData) => config, replaces filterPrivateKeys(formData)
 *   deviceInfoExtras array        optional, extra strings/nodes for device-info row
 *   tabBadges       object        optional {tabKey: string} — text appended after tab label
 *   sidebar         node          optional — rendered beside config-form
 *   children        node          the tab content
 */
function ConfigCardShell({
  id,
  module,
  formData,
  clipboard,
  onCopy,
  onPaste,
  tabs,
  activeTab,
  onTabChange,
  tabSectionMap,
  saveDisabled = false,
  saveTransform,
  deviceInfoExtras = [],
  tabBadges = {},
  sidebar,
  children,
}) {
  const { updateStatus, handleUpdate } = useModuleUpdate(id);
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [applyAllConfirm, setApplyAllConfirm] = useState(null);
  const [hasSaved, setHasSaved] = useState(false);

  const handleSave = () => {
    setHasSaved(true);
    const config = saveTransform ? saveTransform(formData) : filterPrivateKeys(formData);
    socket.emit("save_module_config", { id, config });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: id });
    setShowResetConfirm(false);
  };

  const handleReboot = () => {
    socket.emit("send_command", { module_id: id, type: "reboot", params: {} });
    setShowRebootConfirm(false);
  };

  const confirmApplyToAll = () => {
    if (!applyAllConfirm) return;
    const { section, moduleType } = applyAllConfirm;
    const filtered = filterPrivateKeys(formData);
    const data = filtered?.[section];
    if (data) socket.emit("apply_section_to_type", { module_type: moduleType ?? null, section, data });
    setApplyAllConfirm(null);
  };

  const pasteError = clipboard ? checkClipboardCompatibility(clipboard.data, formData) : null;

  return (
    <div className="config-card">
      <div className="card-header">
        <div className="card-header-top">
          <h3>{module.name} ({module.id})</h3>
          <div className="card-header-actions">
            {typeof module.ip === "string" && module.ip && (
              <span className="device-info-chip">IP: {module.ip}</span>
            )}
            {typeof module.version === "string" && module.version && (
              <span className="device-info-chip">{module.version}</span>
            )}
            {deviceInfoExtras.map((extra, i) =>
              extra ? <span key={i} className="device-info-chip">{extra}</span> : null
            )}
            <button className="header-action-btn" type="button"
              onClick={handleUpdate} disabled={updateStatus === "updating"}>
              {updateStatus === "updating" ? "Updating…" : "Update"}
            </button>
            {updateStatus && updateStatus !== "updating" && (
              <span className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
                {updateStatus.success ? `Updated: ${updateStatus.output}` : `Failed: ${updateStatus.output}`}
              </span>
            )}
            <button className="header-action-btn header-action-btn--danger" type="button"
              onClick={() => setShowRebootConfirm(true)}>
              Reboot
            </button>
          </div>
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">

          <div className="config-tabs">
            {tabs.map(t => (
              <button key={t.key} type="button"
                className={`config-tab-btn${activeTab === t.key ? " active" : ""}`}
                onClick={() => onTabChange(t.key)}>
                {t.label}{tabBadges[t.key] ? ` ${tabBadges[t.key]}` : ""}
              </button>
            ))}
          </div>

          <div className="config-tab-content">
            {children}
          </div>

          <div className="config-section-divider" />

          <CopyActionsBar
            activeTab={activeTab}
            tabSectionMap={tabSectionMap}
            formData={formData}
            moduleType={module.type}
            moduleName={module.name}
            onCopy={onCopy}
            onApplyAll={setApplyAllConfirm}
          />

          {clipboard && (
            <div className="clipboard-bar">
              <span className="clipboard-label">Clipboard: {clipboard.label}</span>
              <button type="button" className="copy-btn" onClick={onPaste} disabled={!!pasteError}>Paste</button>
              <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
              {pasteError && <span className="config-sync-badge config-sync-badge--failed">{pasteError}</span>}
            </div>
          )}

          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave} disabled={saveDisabled}>
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

        {sidebar && (
          <div className="livestream-wrapper">
            {sidebar}
          </div>
        )}
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

export default ConfigCardShell;
