import { useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * The controller's export queue: which modules are exporting now (with
 * files / bytes done), which are waiting and in what order. Asked for on
 * mount and on every reconnect; the controller pushes `export_queue` on each
 * change, including the modules' ~1 s progress reports.
 */
export default function useExportQueue() {
  const [queue, setQueue] = useState(null);
  useEffect(() => {
    const onQueue = (q) => setQueue(q);
    const ask = () => socket.emit("get_export_queue");
    socket.on("export_queue", onQueue);
    socket.on("connect", ask);
    ask();
    return () => {
      socket.off("export_queue", onQueue);
      socket.off("connect", ask);
    };
  }, []);
  return queue;
}
