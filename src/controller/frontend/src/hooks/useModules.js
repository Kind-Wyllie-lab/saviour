import { useEffect, useState } from "react";
import socket from "../socket";

function normaliseModules(data) {
  if (!data || typeof data !== "object") return {};

  return Object.fromEntries(
    Object.entries(data).map(([id, m]) => [
      id,
      {
        ...m,
        id,
        ready: m.ready ?? false,
        checks: m.checks ?? {},
        error: m.error ?? null,
      },
    ])
  );
}

export default function useModules({ autoRequest = true } = {}) {
  const [modules, setModules] = useState({});

  useEffect(() => {
    if (autoRequest) {
      socket.emit("get_modules");
    }

    const handleUpdate = (data) => {
      console.log("Received modules:", data);
      setModules(normaliseModules(data));
    };

    socket.on("modules_update", handleUpdate);

    return () => {
      socket.off("modules_update", handleUpdate);
    };
  }, [autoRequest]);

  return {
    modules,
    moduleList: Object.values(modules),
    hasModules: Object.keys(modules).length > 0,
  };
}
