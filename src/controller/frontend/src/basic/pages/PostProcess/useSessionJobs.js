import { useCallback, useEffect, useMemo, useState } from "react";
import socket from "/src/socket";
import useComposeJobs from "/src/hooks/useComposeJobs";

const ACTIVE = new Set(["queued", "running"]);

/**
 * Everything Post-Process has done (or is doing) for one session, from the
 * three job sources: compose renders (saved history), the NWB export (the
 * file on the share plus this run's jobs) and ephys (the clock model and
 * accelerometer results on the share plus live job updates).
 *
 * Returns the raw pieces plus `items`: one list, newest first, each
 * `{ key, kind, t, active, ... }` for the page's job list.
 */
export default function useSessionJobs(sessionName) {
  const composeJobs = useComposeJobs();
  const [nwb, setNwb] = useState(null);           // nwb_status payload
  const [nwbJobs, setNwbJobs] = useState({});
  const [ephys, setEphys] = useState(null);       // ephys_info payload
  const [alignJob, setAlignJob] = useState(null);
  const [accelJob, setAccelJob] = useState(null);
  const [checkError, setCheckError] = useState("");

  const refreshNwb = useCallback(
    () => sessionName && socket.emit("get_nwb_status", { session_name: sessionName }),
    [sessionName]);
  const refreshEphys = useCallback(
    () => sessionName && socket.emit("get_ephys_info", { session_name: sessionName }),
    [sessionName]);

  useEffect(() => {
    setNwb(null);
    setEphys(null);
    setAlignJob(null);
    setAccelJob(null);
    setCheckError("");
    if (!sessionName) return undefined;
    const onNwbError = (d) => {
      if (d.event === "inspect_nwb_file") setCheckError(d.error || "The check couldn't start");
    };
    const onNwbStatus = (d) => {
      if (d.session_name && d.session_name !== sessionName) return;
      setNwb(d);
      setNwbJobs(Object.fromEntries((d.jobs || []).map((j) => [j.id, j])));
    };
    const onNwbJob = (j) => {
      setNwbJobs((prev) => ({ ...prev, [j.id]: j }));
      if (j.state !== "running") refreshNwb();
    };
    const onNwbJobs = (d) => setNwbJobs(Object.fromEntries((d.jobs || []).map((j) => [j.id, j])));
    const onEphysInfo = (d) => { if (d.session_name === sessionName) setEphys(d); };
    const onAlign = (d) => {
      if (d.session_name !== sessionName) return;
      setAlignJob(d);
      if (d.state === "done") refreshEphys();
    };
    const onAccel = (d) => {
      if (d.session_name !== sessionName) return;
      setAccelJob(d);
      if (d.state === "done") refreshEphys();
    };
    socket.on("nwb_error", onNwbError);
    socket.on("nwb_status", onNwbStatus);
    socket.on("nwb_job", onNwbJob);
    socket.on("nwb_jobs", onNwbJobs);
    socket.on("ephys_info", onEphysInfo);
    socket.on("ephys_align_update", onAlign);
    socket.on("ephys_accel_update", onAccel);
    refreshNwb();
    refreshEphys();
    return () => {
      socket.off("nwb_error", onNwbError);
      socket.off("nwb_status", onNwbStatus);
      socket.off("nwb_job", onNwbJob);
      socket.off("nwb_jobs", onNwbJobs);
      socket.off("ephys_info", onEphysInfo);
      socket.off("ephys_align_update", onAlign);
      socket.off("ephys_accel_update", onAccel);
    };
  }, [sessionName, refreshNwb, refreshEphys]);

  const allCompose = useMemo(
    () => Object.values(composeJobs).sort((a, b) => b.created_at - a.created_at),
    [composeJobs]);
  const compose = allCompose.filter((j) => j.spec?.session_name === sessionName);
  const otherCompose = allCompose.filter((j) => j.spec?.session_name !== sessionName);

  const nwbList = Object.values(nwbJobs);
  const nwbRunning = nwbList.find((j) => j.state === "running") || null;
  const nwbFailed = nwbList
    .filter((j) => ["export", "inspect"].includes(j.kind) && j.session_name === sessionName
      && j.state === "error")
    .sort((a, b) => b.created_at - a.created_at)[0] || null;
  const existing = nwb?.session?.existing || null;

  const items = [];
  for (const job of compose) {
    items.push({ key: `c-${job.id}`, kind: "compose", t: job.created_at, active: ACTIVE.has(job.state), job });
  }
  if (nwbRunning && (nwbRunning.kind === "install" || nwbRunning.session_name === sessionName)) {
    items.push({ key: `n-${nwbRunning.id}`, kind: "nwb-running", t: nwbRunning.created_at, active: true, job: nwbRunning });
  }
  if (existing) {
    items.push({ key: "n-file", kind: "nwb-file", t: existing.mtime, active: false, file: existing });
  }
  if (nwbFailed && (!existing || nwbFailed.created_at > existing.mtime)) {
    items.push({ key: `n-${nwbFailed.id}`, kind: "nwb-failed", t: nwbFailed.created_at, active: false, job: nwbFailed });
  }
  for (const job of [alignJob, accelJob]) {
    if (job && ACTIVE.has(job.state)) {
      items.push({ key: `e-${job === alignJob ? "align" : "accel"}-run`, kind: "ephys-running", t: Date.now() / 1000, active: true, job,
        label: job === alignJob ? "Ephys alignment" : "Headstage accelerometer" });
    }
  }
  if (ephys?.model) {
    items.push({ key: "e-model", kind: "ephys-model", t: ephys.model.fitted_at || 0, active: false, model: ephys.model });
  }
  for (const [port, result] of Object.entries(ephys?.accel || {})) {
    items.push({ key: `e-accel-${port}`, kind: "ephys-accel", t: result.processed_at || 0, active: false, port, result });
  }
  items.sort((a, b) => (b.active - a.active) || (b.t - a.t));

  return {
    items, compose, otherCompose, nwb, nwbRunning, existing, ephys,
    checkError, clearCheckError: () => setCheckError(""),
    ephysUploaded: (ephys?.recordings || []).length > 0,
  };
}
