import { useExportSync } from "/src/hooks/useExportSync";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";

/**
 * Export section for a module config card.
 *
 * The export destination (Samba share IP / path / credentials) is set once on
 * the Controller (Settings → Controller → Export) and pushed to every module —
 * the per-module "manual / custom Samba" override was removed 2026-08-28. This
 * section shows the module's current destination read-only, a button to
 * re-pull it from the controller on demand, and the module-local export
 * behaviour (auto-export, delete-after-export, bandwidth cap).
 */
function ExportConfigSection({ exportConfig, handleChange, moduleId }) {
  const { syncStatus, syncExport } = useExportSync(moduleId);
  const loggedIn = useIsLoggedIn();

  const cfg = exportConfig ?? {};
  const onChange = (key, e) => handleChange(["export", key], e);

  const dest = cfg.share_ip
    ? `//${cfg.share_ip}/${cfg.share_path || "controller_share"}`
    : null;

  return (
    <>
      {/* ── Destination (read-only — set on the Controller) ── */}
      <div className="form-field">
        <label>Exports to:</label>
        <span className="form-field-computed">
          {dest ?? "not set — configure on the Controller"}
          {cfg.share_username ? ` (${cfg.share_username})` : dest ? " (guest)" : ""}
        </span>
      </div>
      <div className="config-action-buttons">
        <button type="button" className="save-button"
          onClick={syncExport}
          disabled={!loggedIn || syncStatus === "syncing"}
          title={loggedIn ? undefined : "Login required for this action"}>
          {syncStatus === "syncing" ? "Syncing…" : "Re-sync destination from controller"}
        </button>
        {syncStatus && syncStatus !== "syncing" && (
          <span className={`config-sync-badge ${syncStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
            {syncStatus.success ? "Synced" : `Sync failed: ${syncStatus.error}`}
          </span>
        )}
      </div>

      {/* ── Module-local export behaviour ── */}
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
