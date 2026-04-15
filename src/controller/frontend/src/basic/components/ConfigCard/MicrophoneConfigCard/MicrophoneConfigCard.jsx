import { useEffect, useState, useRef } from "react";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import { useExportSync } from "/src/hooks/useExportSync";

const STALL_MS     = 8000;
const RECONNECT_MS = 2500;

function MicrophoneStream({ ip, port }) {
  const [streamKey, setStreamKey] = useState(Date.now());
  const [fullscreen, setFullscreen] = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const bump = () => setStreamKey(Date.now());

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  const resetStall = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_MS);
  };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_MS);
  };

  return (
    <>
      <img
        key={streamKey}
        src={`http://${ip}:${port}/video_feed`}
        alt="Microphone monitor stream"
        style={{ width: "100%", display: "block", borderRadius: "4px", cursor: "pointer" }}
        onLoad={resetStall}
        onError={handleError}
        onClick={() => setFullscreen(true)}
      />
      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

function MicrophoneConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [hasSaved, setHasSaved]                 = useState(false);
  const { updateStatus, handleUpdate } = useModuleUpdate(module.id);
  const { syncStatus, syncExport } = useExportSync(module.id);

  const streamPort = module.config?.monitoring?.port ?? 8081;

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

  const sections   = Object.keys(filterPrivateKeys(formData) ?? {}).filter(
    k => formData[k] !== null && typeof formData[k] === "object"
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

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip      === "string" && module.ip      && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>{module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          {clipboard && (
            <div className="clipboard-bar">
              <span className="clipboard-label">Clipboard: {clipboard.label}</span>
              <button type="button" className="copy-btn" onClick={handlePaste}>Paste</button>
              <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
            </div>
          )}

          <form>
            <ConfigFields data={formData} handleChange={handleChange} />
          </form>

          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            {sections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => onCopy({ label: `${capitalize(key)} — ${module.name}`, data: { [key]: formData[key] } })}>
                {capitalize(key)}
              </button>
            ))}
            <button type="button" className="copy-btn"
              onClick={() => onCopy({ label: `All — ${module.name}`, data: filterPrivateKeys(formData) })}>
              All
            </button>
          </div>

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

          {formData?.export !== undefined && (
            <div className="config-action-buttons">
              <button type="button" className="save-button"
                onClick={syncExport}
                disabled={syncStatus === "syncing"}>
                {syncStatus === "syncing" ? "Syncing…" : "Sync Export from Controller"}
              </button>
              {syncStatus && syncStatus !== "syncing" && (
                <span className={`config-sync-badge ${syncStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
                  {syncStatus.success ? "Export synced" : `Sync failed: ${syncStatus.error}`}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="livestream-wrapper">
          <MicrophoneStream ip={module.ip} port={streamPort} />
        </div>
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
        <button className="update-button" type="button"
          onClick={() => socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} })}>
          Reboot Module
        </button>
      </div>

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reset <strong>{module.name}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and any custom configuration will be lost.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReset}>Reset</button>
              <button className="save-button"  type="button" onClick={() => setShowResetConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MicrophoneConfigCard;
