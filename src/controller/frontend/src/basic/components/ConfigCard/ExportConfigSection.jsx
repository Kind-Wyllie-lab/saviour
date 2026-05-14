import { useState } from "react";
import { useExportSync } from "/src/hooks/useExportSync";

/**
 * Renders the export section for any module config card.
 *
 * When export_target === "controller" (default):
 *   - Samba credential fields are hidden; a one-click sync button fills them
 *     from the controller's own credentials automatically.
 *
 * When export_target === "manual":
 *   - Samba fields (IP, share, username, password) are shown for direct entry.
 *   - The sync button is hidden.
 */
function ExportConfigSection({ exportConfig, handleChange, moduleId }) {
  const [showPassword, setShowPassword] = useState(false);
  const { syncStatus, syncExport } = useExportSync(moduleId);

  const cfg = exportConfig ?? {};
  const target = cfg.export_target ?? "controller";
  const isManual = target === "manual";

  const onChange = (key, e) => handleChange(["export", key], e);

  return (
    <>
      {/* ── Target ── */}
      <div className="form-field">
        <label>Target:</label>
        <select
          value={target}
          onChange={e => onChange("export_target", e)}
        >
          <option value="controller">Controller (auto)</option>
          <option value="manual">Manual (custom Samba)</option>
        </select>
      </div>

      {/* ── Samba credentials — manual mode only ── */}
      {isManual && (
        <>
          <div className="form-field">
            <label>Share IP:</label>
            <input type="text"
              value={cfg.share_ip ?? ""}
              onChange={e => onChange("share_ip", e)} />
          </div>
          <div className="form-field">
            <label>Share name:</label>
            <input type="text"
              value={cfg.share_path ?? ""}
              onChange={e => onChange("share_path", e)} />
          </div>
          <div className="form-field">
            <label>Username:</label>
            <input type="text"
              value={cfg.share_username ?? ""}
              onChange={e => onChange("share_username", e)} />
          </div>
          <div className="form-field">
            <label>Password:</label>
            <div className="exposure-control">
              <input
                type={showPassword ? "text" : "password"}
                value={cfg.share_password ?? ""}
                onChange={e => onChange("share_password", e)}
              />
              <label className="exposure-manual-label">
                <input type="checkbox"
                  checked={showPassword}
                  onChange={e => setShowPassword(e.target.checked)} />
                Show
              </label>
            </div>
          </div>
        </>
      )}

      {/* ── Sync button — controller mode only ── */}
      {!isManual && (
        <div className="config-action-buttons">
          <button type="button" className="save-button"
            onClick={syncExport}
            disabled={syncStatus === "syncing"}>
            {syncStatus === "syncing" ? "Syncing…" : "Sync credentials from controller"}
          </button>
          {syncStatus && syncStatus !== "syncing" && (
            <span className={`config-sync-badge ${syncStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
              {syncStatus.success ? "Synced" : `Sync failed: ${syncStatus.error}`}
            </span>
          )}
        </div>
      )}

      {/* ── Common fields ── */}
      <div className="form-field">
        <label>Auto export:</label>
        <input type="checkbox"
          checked={cfg.auto_export ?? true}
          onChange={e => onChange("auto_export", e)} />
      </div>
      <div className="form-field">
        <label>Delete after export:</label>
        <input type="checkbox"
          checked={cfg.delete_on_export ?? true}
          onChange={e => onChange("delete_on_export", e)} />
      </div>
      <div className="form-field">
        <label>Max export bitrate (Mbps):</label>
        <input type="number" min="1" step="1"
          value={cfg.max_bitrate_mb ?? ""}
          onChange={e => onChange("max_bitrate_mb", e)} />
      </div>
      <div className="form-field">
        <label>Max burst (KB):</label>
        <input type="number" min="1" step="1"
          value={cfg.max_burst_kb ?? ""}
          onChange={e => onChange("max_burst_kb", e)} />
      </div>
    </>
  );
}

export default ExportConfigSection;
