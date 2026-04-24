import { useExportSync } from "/src/hooks/useExportSync";

function ExportSyncButton({ moduleId }) {
  const { syncStatus, syncExport } = useExportSync(moduleId);

  return (
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
  );
}

export default ExportSyncButton;
