import { useEffect, useState } from "react";
import socket from "../socket";

// TTL lines confirmed on the acquisition system and the last alignment that
// proved them (src/controller/ttl_lines.py). `refreshKey`: re-ask when it
// changes (e.g. a module's saved pins), since line state depends on config.
export default function useTTLLines(refreshKey = "") {
  const [lines, setLines] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const onLines = (d) => { setLines(d); setError(null); };
    const onError = (d) => setError(d?.error ?? "Error");
    socket.on("ttl_lines", onLines);
    socket.on("ttl_lines_error", onError);
    return () => {
      socket.off("ttl_lines", onLines);
      socket.off("ttl_lines_error", onError);
    };
  }, []);
  useEffect(() => { socket.emit("get_ttl_lines"); }, [refreshKey]);
  return { lines, error, clearError: () => setError(null) };
}
