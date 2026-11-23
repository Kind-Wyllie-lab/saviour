import React, { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import usePersistedState from "/src/hooks/usePersistedState";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { formatBytes, triggerDownload } from "../Recording/sessionFormat";
import { formatUtc } from "/src/basic/utils/time";

// NWB export (plans/nwb-export.md): one .nwb per session, written next to
// the session's data on the share by src/controller/nwb_convert.py. The NWB
// libraries are an optional install (Install NWB export package).

const SPECIES = ["Rattus norvegicus", "Mus musculus"];
const SEXES = [["U", "Unknown"], ["M", "Male"], ["F", "Female"], ["O", "Other"]];
const INCLUDE = [
  ["video", "Video", "linked, not copied: keep the .nwb in the session folder"],
  ["tracking", "Tracking", "online detections from apa / loom cameras"],
  ["audio", "Audio", "embedded"],
  ["ttl", "TTL edges", ""],
  ["events", "APA shock log", ""],
  ["markers", "Markers", ""],
];

function blankForm() {
  return {
    subject_id: "", strain: "", sex: "U", age: "", weight: "", genotype: "",
    subject_description: "", session_description: "", experimenter: "",
    include: Object.fromEntries(INCLUDE.map(([k]) => [k, true])),
  };
}

function JobView({ job, onCancel, onDownload, onUseSettings }) {
  const [showLog, setShowLog] = useState(false);
  const running = job.state === "running";
  return (
    <div className={`nwb-job nwb-job--${job.state}`}>
      <div className="nwb-job__head">
        <strong>{job.kind === "install" ? "Install" : "Export"}</strong>
        <span className="nwb-job__stage">{job.stage}</span>
        {running && <button className="btn-secondary" onClick={onCancel}>Cancel</button>}
      </div>
      {running && (
        <div className="compose-job__bar">
          <div className="compose-job__bar-fill" style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} />
        </div>
      )}
      {job.error && <div className="compose-job__error">{job.error}</div>}
      {job.state === "done" && job.kind === "export" && (
        <div className="nwb-job__done">
          Wrote <code>{job.output}</code>
          {job.size_bytes ? ` (${formatBytes(job.size_bytes)})` : ""}.
          {job.validation?.ran && (job.validation.errors?.length
            ? <span className="nwb-job__warn"> Schema check: {job.validation.errors.length} problem(s), see the log.</span>
            : <span className="nwb-job__ok"> Schema check passed.</span>)}
          <button className="btn" onClick={onDownload}>Download .nwb</button>
          {onUseSettings && job.settings && (
            <button className="btn-secondary" onClick={() => onUseSettings(job.settings)}
              title="Fill the form with the settings this export used">
              Use these settings
            </button>
          )}
        </div>
      )}
      {job.warnings?.length > 0 && (
        <ul className="ephys-panel__warnings">
          {job.warnings.slice(0, 8).map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
      {job.log?.length > 0 && (
        <>
          <button className="btn-link" onClick={() => setShowLog(!showLog)}>
            {showLog ? "Hide log" : `Show log (${job.log.length} lines)`}
          </button>
          {showLog && <pre className="nwb-job__log">{job.log.join("\n")}</pre>}
        </>
      )}
    </div>
  );
}

export default function NwbExportPanel({ sessionName, onSummary }) {
  const loggedIn = useIsLoggedIn();
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState({});
  const [error, setError] = useState("");
  const [form, setForm] = useState(blankForm);
  const [species, setSpecies] = usePersistedState("nwb_species", SPECIES[0]);
  const [otherSpecies, setOtherSpecies] = usePersistedState("nwb_species_other", "");
  const [institution, setInstitution] = usePersistedState("nwb_institution", "");
  const [lab, setLab] = usePersistedState("nwb_lab", "");
  const prefilled = useRef(null);

  const refresh = () => socket.emit("get_nwb_status", { session_name: sessionName });

  useEffect(() => {
    setStatus(null);
    setError("");
    const onStatus = (d) => {
      if (d.session_name && d.session_name !== sessionName) return;
      setStatus(d);
      setJobs(Object.fromEntries((d.jobs || []).map((j) => [j.id, j])));
      // Prefill from the session's metadata once per session, so a refresh
      // after a job doesn't wipe what was typed.
      if (!d.session || prefilled.current === sessionName) return;
      prefilled.current = sessionName;
      const md = d.metadata || {};
      setForm({
        ...blankForm(),
        subject_id: md.rat_id || "",
        strain: md.strain || "",
        experimenter: md.experimenter || "",
        session_description: md.experiment || sessionName,
      });
    };
    const onJob = (j) => {
      setJobs((prev) => ({ ...prev, [j.id]: j }));
      if (j.state !== "running") refresh();
    };
    const onJobs = (d) => setJobs(Object.fromEntries((d.jobs || []).map((j) => [j.id, j])));
    const onError = (d) => setError(d.error || "Something went wrong");
    socket.on("nwb_status", onStatus);
    socket.on("nwb_job", onJob);
    socket.on("nwb_jobs", onJobs);
    socket.on("nwb_error", onError);
    refresh();
    return () => {
      socket.off("nwb_status", onStatus);
      socket.off("nwb_job", onJob);
      socket.off("nwb_jobs", onJobs);
      socket.off("nwb_error", onError);
    };
  }, [sessionName]); // eslint-disable-line react-hooks/exhaustive-deps

  const all = useMemo(() => Object.values(jobs).sort((a, b) => b.created_at - a.created_at), [jobs]);
  const running = all.find((j) => j.state === "running");
  const installJob = all.find((j) => j.kind === "install");
  const exportJob = all.find((j) => j.kind === "export" && j.session_name === sessionName);
  const info = status?.session;
  const existing = info?.existing;

  const present = useMemo(() => ({
    video: !!info?.cameras?.length,
    tracking: !!info?.cameras?.some((c) => c.tracking),
    audio: !!info?.audio?.length,
    ttl: !!info?.events?.some((e) => e.kind === "ttl"),
    events: !!info?.events?.some((e) => e.kind === "apa_shocks"),
    markers: (info?.markers || 0) > 0,
  }), [info]);

  // Card header summary.
  let summary;
  if (!status) summary = { text: "checking…", tone: "muted" };
  else if (running?.kind === "install") summary = { text: "installing the NWB package…", tone: "active" };
  else if (!status.installed) summary = { text: "NWB package not installed", tone: "muted" };
  else if (running?.kind === "export" && running.session_name === sessionName) {
    summary = { text: `exporting · ${running.stage || "starting"}`, tone: "active" };
  } else if (exportJob?.state === "failed" || exportJob?.state === "error") {
    summary = { text: "last export failed", tone: "bad" };
  } else if (existing) {
    summary = { text: `written ${formatUtc(existing.mtime)} · ${formatBytes(existing.size_bytes)}`, tone: "ok" };
  } else if (status.session_error) summary = { text: status.session_error, tone: "warn" };
  else summary = { text: "not exported", tone: "muted" };
  useEffect(() => {
    onSummary?.(summary);
  }, [summary.text, summary.tone, onSummary]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // Fill the form from the settings an earlier export used (saved beside
  // the .nwb as <session>.nwb-settings.json, or on this run's job).
  const applySettings = (st) => {
    if (!st) return;
    const base = blankForm();
    const pick = Object.fromEntries(Object.keys(base)
      .filter((k) => k !== "include" && typeof st[k] === "string")
      .map((k) => [k, st[k]]));
    setForm({ ...base, ...pick, include: { ...base.include, ...(st.include || {}) } });
    if (st.species) {
      if (SPECIES.includes(st.species)) setSpecies(st.species);
      else { setSpecies("other"); setOtherSpecies(st.species); }
    }
    if (typeof st.institution === "string") setInstitution(st.institution);
    if (typeof st.lab === "string") setLab(st.lab);
  };
  const speciesValue = species === "other" ? otherSpecies.trim() : species;
  const ageOk = !form.age || /^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$/i.test(form.age.trim());
  const canExport = status?.installed && info && !running && speciesValue && ageOk;

  const download = () => triggerDownload(
    `/api/sessions/${encodeURIComponent(sessionName)}/download/${encodeURIComponent(`${sessionName}.nwb`)}`,
    `${sessionName}.nwb`);

  const startExport = () => {
    setError("");
    if (existing && !window.confirm(`Replace the existing ${sessionName}.nwb?`)) return;
    socket.emit("start_nwb_export", {
      session_name: sessionName,
      options: {
        ...form, species: speciesValue, institution, lab,
        include: Object.fromEntries(INCLUDE.map(([k]) => [k, present[k] && form.include[k]])),
        overwrite: !!existing,
      },
    });
  };

  return (
    <div className="ephys-panel nwb-panel">
      <p className="modal-subtext">
        Write this session as one Neurodata Without Borders (<code>.nwb</code>) file,
        next to its data on the share, for analysis tools and DANDI. Every stream
        is on the session&apos;s clock, in seconds from its UTC start.
      </p>

      {error && <div className="compose-job__error">{error}</div>}
      {!status && <div className="ephys-panel__note">Checking…</div>}

      {status && !status.installed && (
        <div className="nwb-install">
          <p>
            NWB export needs extra Python packages (pynwb, h5py, pandas) that a stock
            SAVIOUR install leaves out. They go into their own environment
            (<code>env-nwb</code>) beside SAVIOUR&apos;s, so they can&apos;t affect
            recording. The controller downloads them (some tens of MB), so it needs
            internet access.
          </p>
          <button
            className="btn"
            disabled={!loggedIn || !!running}
            title={loggedIn ? "" : "Sign in as admin to install"}
            onClick={() => { setError(""); socket.emit("install_nwb_package"); }}
          >
            Install NWB export package
          </button>
          {!loggedIn && <span className="ephys-panel__note"> Sign in as admin to install.</span>}
        </div>
      )}
      {installJob && (installJob.state !== "done" || !status?.installed) && (
        <JobView job={installJob} onCancel={() => socket.emit("cancel_nwb_job", { id: installJob.id })} />
      )}

      {status?.installed && status.session_error && (
        <div className="ephys-panel__note">{status.session_error}</div>
      )}

      {status?.installed && info && (
        <>
          <div className="ephys-panel__note">
            pynwb {status.version} · {info.cameras.length} camera(s), {info.audio.length} audio
            recording(s), {info.events.length} event log(s)
            {info.markers ? `, ${info.markers} marker(s)` : ""}
          </div>

          {existing && !running && (
            <div className="nwb-existing">
              <code>{sessionName}.nwb</code> · {formatBytes(existing.size_bytes)} · written{" "}
              {formatUtc(existing.mtime)}
              <button className="btn-secondary" onClick={download}>Download</button>
              {existing.settings && (
                <button className="btn-secondary" onClick={() => applySettings(existing.settings)}
                  title="Fill the form with the settings this file was written with">
                  Load its settings
                </button>
              )}
            </div>
          )}

          <fieldset className="nwb-form" disabled={!!running}>
            <legend>Subject</legend>
            <label>
              Species
              <select value={species} onChange={(e) => setSpecies(e.target.value)}>
                {SPECIES.map((s) => <option key={s} value={s}>{s}</option>)}
                <option value="other">Other…</option>
              </select>
            </label>
            {species === "other" && (
              <label>
                Species (Latin name)
                <input value={otherSpecies} placeholder="e.g. Meriones unguiculatus"
                  onChange={(e) => setOtherSpecies(e.target.value)} />
              </label>
            )}
            <label>
              Subject ID
              <input value={form.subject_id} placeholder="rat ID"
                onChange={(e) => set({ subject_id: e.target.value })} />
            </label>
            <label>
              Strain
              <input value={form.strain} placeholder="e.g. Lister Hooded"
                onChange={(e) => set({ strain: e.target.value })} />
            </label>
            <label>
              Sex
              <select value={form.sex} onChange={(e) => set({ sex: e.target.value })}>
                {SEXES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className={ageOk ? "" : "nwb-form--bad"}>
              Age
              <input value={form.age} placeholder="P90D = 90 days, P12W = 12 weeks"
                onChange={(e) => set({ age: e.target.value })} />
            </label>
            <label>
              Weight
              <input value={form.weight} placeholder="e.g. 0.35 kg"
                onChange={(e) => set({ weight: e.target.value })} />
            </label>
            <label>
              Genotype
              <input value={form.genotype} placeholder="optional"
                onChange={(e) => set({ genotype: e.target.value })} />
            </label>
          </fieldset>

          <fieldset className="nwb-form" disabled={!!running}>
            <legend>Session</legend>
            <label className="nwb-form__wide">
              Description
              <input value={form.session_description}
                onChange={(e) => set({ session_description: e.target.value })} />
            </label>
            <label>
              Experimenter
              <input value={form.experimenter} onChange={(e) => set({ experimenter: e.target.value })} />
            </label>
            <label>
              Institution
              <input value={institution} placeholder="remembered" onChange={(e) => setInstitution(e.target.value)} />
            </label>
            <label>
              Lab
              <input value={lab} placeholder="remembered" onChange={(e) => setLab(e.target.value)} />
            </label>
          </fieldset>

          <fieldset className="nwb-form nwb-form--include" disabled={!!running}>
            <legend>Include</legend>
            {INCLUDE.filter(([k]) => present[k]).map(([k, label, hint]) => (
              <label key={k} className="nwb-check">
                <input type="checkbox" checked={form.include[k]}
                  onChange={(e) => set({ include: { ...form.include, [k]: e.target.checked } })} />
                {label}
                {k === "audio" && info.audio_pcm_bytes > 0 && (
                  <span className="nwb-check__hint"> about {formatBytes(info.audio_pcm_bytes * 0.85)}</span>
                )}
                {hint && k !== "audio" && <span className="nwb-check__hint"> {hint}</span>}
              </label>
            ))}
            {!Object.values(present).some(Boolean) && (
              <span className="ephys-panel__note">No streams found: the file will hold the metadata only.</span>
            )}
          </fieldset>

          <div className="ephys-panel__run">
            <button className="btn" disabled={!canExport} onClick={startExport}>
              {existing ? "Export again (replaces it)" : "Export NWB"}
            </button>
            {running && running.kind === "export" && running.session_name !== sessionName && (
              <span className="ephys-panel__note">Exporting {running.session_name}…</span>
            )}
          </div>
        </>
      )}

      {exportJob && (
        <JobView
          job={exportJob}
          onCancel={() => socket.emit("cancel_nwb_job", { id: exportJob.id })}
          onDownload={download}
          onUseSettings={applySettings}
        />
      )}
    </div>
  );
}
