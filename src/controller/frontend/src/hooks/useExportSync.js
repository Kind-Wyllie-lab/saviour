import { useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * Sync this controller's Samba credentials (share_ip, share_username,
 * share_password) into a module's export config with one click.
 *
 * Returns { syncStatus, syncExport }:
 *   syncStatus  null | "syncing" | { success: bool, error?: string }
 *   syncExport  () => void
 */
export function useExportSync(moduleId) {
  const [syncStatus, setSyncStatus] = useState(null);

  useEffect(() => {
    const handler = (data) => {
      if (data.module_id === moduleId) {
        setSyncStatus({ success: data.success, error: data.error });
        if (data.success) {
          socket.emit("get_module_config", { module_id: moduleId });
        }
      }
    };
    socket.on("export_sync_result", handler);
    return () => socket.off("export_sync_result", handler);
  }, [moduleId]);

  const syncExport = () => {
    setSyncStatus("syncing");
    socket.emit("sync_export_credentials", { module_id: moduleId });
  };

  return { syncStatus, syncExport };
}
