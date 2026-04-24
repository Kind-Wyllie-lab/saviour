import { useEffect, useState } from "react";
import socket from "../socket";

// Fires one get_controller_health request on mount, resolves drift once, then cleans up.
export default function useClockOnce() {
  const [result, setResult] = useState(null); // { driftMs, controllerTime }

  useEffect(() => {
    let resolved = false;
    const handler = (data) => {
      if (resolved || !data?.controller_time) return;
      resolved = true;
      const controllerMs = new Date(data.controller_time).getTime();
      setResult({ driftMs: controllerMs - Date.now(), controllerTime: data.controller_time });
      socket.off("controller_health_response", handler);
    };
    socket.on("controller_health_response", handler);
    socket.emit("get_controller_health");
    return () => socket.off("controller_health_response", handler);
  }, []);

  return result;
}
