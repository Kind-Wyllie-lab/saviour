import { useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * The controller's render (compose) jobs, keyed by id: the saved history
 * plus anything queued or running. Asked for on mount and on reconnect;
 * `compose_job_update` / `compose_job_accepted` keep it current and a
 * `compose_jobs` push replaces it (e.g. after a delete).
 */
export default function useComposeJobs() {
  const [jobs, setJobs] = useState({});
  useEffect(() => {
    const upsert = (summary) =>
      setJobs((prev) => ({ ...prev, [summary.id]: summary }));
    const onList = (data) => {
      const map = {};
      for (const j of data.jobs || []) map[j.id] = j;
      setJobs(map);
    };
    const ask = () => socket.emit("get_compose_jobs");
    socket.on("compose_jobs", onList);
    socket.on("compose_job_update", upsert);
    socket.on("compose_job_accepted", upsert);
    socket.on("connect", ask);
    ask();
    return () => {
      socket.off("compose_jobs", onList);
      socket.off("compose_job_update", upsert);
      socket.off("compose_job_accepted", upsert);
      socket.off("connect", ask);
    };
  }, []);
  return jobs;
}
