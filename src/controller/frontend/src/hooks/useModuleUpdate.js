import { useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * Tracks the update status for a single module.
 * Returns { updateStatus, handleUpdate }.
 *
 * updateStatus is one of:
 *   null                       — idle
 *   "updating"                 — git pull in progress
 *   { success: bool, output }  — completed
 */
export function useModuleUpdate(moduleId) {
  const [updateStatus, setUpdateStatus] = useState(null);

  useEffect(() => {
    setUpdateStatus(null);
  }, [moduleId]);

  useEffect(() => {
    const handler = (data) => {
      if (data.module_id === moduleId) {
        setUpdateStatus({ success: data.success, output: data.output });
      }
    };
    socket.on("module_update_result", handler);
    return () => socket.off("module_update_result", handler);
  }, [moduleId]);

  const handleUpdate = () => {
    setUpdateStatus("updating");
    socket.emit("send_command", { module_id: moduleId, type: "update_saviour", params: {} });
  };

  return { updateStatus, handleUpdate };
}
