import { useEffect, useState, useCallback } from "react";
import socket from "../socket";

export default function useHealth({ pollInterval = 30000 } = {}) {
  const [moduleHealth, setModuleHealth] = useState({});
  const [controllerHealth, setControllerHealth] = useState(null);

  const refresh = useCallback(() => {
    socket.emit("get_module_health");
    socket.emit("get_controller_health");
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, pollInterval);

    socket.on("module_health_update", (data) => {
      setModuleHealth(data.module_health || {});
    });

    socket.on("controller_health_response", (data) => {
      setControllerHealth(data);
    });

    return () => {
      clearInterval(interval);
      socket.off("module_health_update");
      socket.off("controller_health_response");
    };
  }, [pollInterval, refresh]);

  return { moduleHealth, controllerHealth, refresh };
}
