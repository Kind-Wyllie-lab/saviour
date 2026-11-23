import { useEffect, useMemo, useState } from "react";
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
    // Ask again on every (re)connect: the server doesn't push sessions on
    // connect, so a sessions_update missed while the socket was down (e.g.
    // a timed session stopping) would otherwise leave a stale ACTIVE copy.
    const request = () => socket.emit("get_sessions");
    if (autoRequest) {
      request();
      socket.on("connect", request);
    }

    const handleUpdate = (data) => {
      setSessions(normaliseSessions(data));
    };

    socket.on("sessions_update", handleUpdate);

    return () => {
      socket.off("sessions_update", handleUpdate);
      if (autoRequest) socket.off("connect", request);
    };
  }, [autoRequest]);

  const sessionList = useMemo(() => Object.values(sessions), [sessions]);

  return {
    sessions,
    sessionList,
    hasSessions: Object.keys(sessions).length > 0,
  };
}
