import { useEffect, useState } from "react";
import socket from "../socket";

function normaliseSessions(data) {
  if (!data || typeof data !== "object") return {};

  return Object.fromEntries(
    Object.entries(data).map(([id, s]) => [
      id,
      {
        ...s,
        id,
        active: s.active ?? false,
        modules: s.modules ?? [],
        start_time: s.start_time ?? null,
        end_time: s.end_time ?? null,
      },
    ])
  );
}


export default function useSessions({ autoRequest = true } = {}) {
  const [sessions, setSessions] = useState({});

  useEffect(() => {
    if (autoRequest) {
      socket.emit("get_sessions");
    }

    const handleUpdate = (data) => {
      setSessions(normaliseSessions(data));
    };

    socket.on("sessions_update", handleUpdate);

    return () => {
      socket.off("sessions_update", handleUpdate);
    };
  }, [autoRequest]);

  return {
    sessions,
    sessionList: Object.values(sessions),
    hasSessions: Object.keys(sessions).length > 0,
  };
}
