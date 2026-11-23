import React, { useState, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import usePersistedState from "/src/hooks/usePersistedState";
import {
  formatBytes, groupSessionsByDate, dateGroupLabel,
} from "../Recording/sessionFormat";
import ComposeEditor from "./ComposeEditor";
import ComposeJobRow from "../Recording/SessionDetailPage/ComposeJobRow";
import useComposeJobs from "/src/hooks/useComposeJobs";
import EphysAlignPanel from "./EphysAlignPanel";
import NwbExportPanel from "./NwbExportPanel";
import ToolCard from "./ToolCard";
import "./PostProcess.css";

// Everything that turns a *recorded* session into a derived artefact —
// layout/compose video, aligned audio + spectrogram, and (once a dataset
// is uploaded) ephys alignment. Kept off the Recording page, which is for
// running sessions and browsing raw files.
export default function PostProcess() {
  const { sessions } = useSessions();
  const [params, setParams] = useSearchParams();
  const names = useMemo(
    () => Object.keys(sessions || {}).sort(),
    [sessions],
  );

  // Same day grouping as the session list rail, newest day first.
  const groups = useMemo(
    () => groupSessionsByDate(Object.values(sessions || {}).slice().reverse()),
    [sessions],
  );

  // Remembered so leaving the page (sidebar) and coming back reopens the
  // same session, and its render, rather than a different one.
  const [remembered, setRemembered] = usePersistedState("postprocess_session", "");
  const [selected, setSelectedRaw] = useState(params.get("session") || remembered || "");
  const setSelected = (name) => {
    setSelectedRaw(name);
    setRemembered(name);
  };
  const [fileInfo, setFileInfo] = useState(null);
  const jobs = useComposeJobs();
  const allJobs = Object.values(jobs).sort((a, b) => b.created_at - a.created_at);
  // Which tool cards are open (all closed by default); each panel reports
  // a one-line summary for its card header.
  const [openCards, setOpenCards] = usePersistedState("postprocess_open_cards", {});
  const toggle = (id) => setOpenCards((o) => ({ ...o, [id]: !o?.[id] }));
  const isOpen = (id) => !!openCards?.[id];
  const [summaries, setSummaries] = useState({});
  const summaryFor = useMemo(() => {
    const cache = {};
    return (id) => (cache[id] ||= (s) => setSummaries((cur) => (
      cur[id]?.text === s?.text && cur[id]?.tone === s?.tone ? cur : { ...cur, [id]: s })));
  }, []);
  const seededRef = useRef(false);

  // Default to the session in the URL (or the one last picked here), else
  // the most recent one -- the controller lists sessions oldest first; the
  // alphabetically last name (what this used) is usually not the newest.
  useEffect(() => {
    if (seededRef.current || !names.length) return;
    const newest = Object.keys(sessions || {}).pop();
    const want = names.includes(selected) ? selected : newest;
    seededRef.current = true;
    if (want !== selected) setSelected(want);
  }, [names]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    setParams((p) => {
      p.set("session", selected);
      return p;
    });
    setFileInfo(null);
    const onInfo = (d) => {
      if (d.session_name === selected) setFileInfo(d);
    };
    socket.on("session_file_info_response", onInfo);
    socket.emit("get_session_file_info", { session_name: selected });
    return () => socket.off("session_file_info_response", onInfo);
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="post-process">
      <label className="post-process__pick">
        Session
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">— choose a session —</option>
          {groups.map(({ key, sessions: day }) => (
            <optgroup key={key} label={dateGroupLabel(key)}>
              {day.map((s) => (
                <option key={s.session_name} value={s.session_name}>
                  {s.session_name}
                  {s.state ? `  (${s.state})` : ""}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {fileInfo && fileInfo !== "loading" && (
          <span className="post-process__size">
            {fileInfo.files?.length || 0} files ·{" "}
            {formatBytes(fileInfo.total_bytes || 0)}
          </span>
        )}
      </label>
      <p className="post-process__intro">
        Build aligned videos, spectrograms, (with an uploaded recording)
        ephys alignment and NWB files from a finished session. Jobs run in the background —
        you can leave this page.
      </p>

      {!selected && (
        <div className="post-process__empty">Pick a session to start.</div>
      )}

      {selected && fileInfo && fileInfo.files && fileInfo.files.length > 0 && (
        <>
          <ToolCard title="Compose video" summary={summaries.compose}
            open={isOpen("compose")} onToggle={() => toggle("compose")}>
            <ComposeEditor
              sessionName={selected}
              session={sessions?.[selected]}
              files={fileInfo.files}
              active={isOpen("compose")}
              onSummary={summaryFor("compose")}
              onSelectSession={(name) => names.includes(name) && setSelected(name)}
            />
          </ToolCard>
          <ToolCard title="Ephys" summary={summaries.ephys}
            open={isOpen("ephys")} onToggle={() => toggle("ephys")}>
            <EphysAlignPanel sessionName={selected} onSummary={summaryFor("ephys")} />
          </ToolCard>
          <ToolCard title="NWB export" summary={summaries.nwb}
            open={isOpen("nwb")} onToggle={() => toggle("nwb")}>
            <NwbExportPanel sessionName={selected} onSummary={summaryFor("nwb")} />
          </ToolCard>
        </>
      )}

      {selected && fileInfo && fileInfo.files && fileInfo.files.length === 0 && (
        <div className="post-process__empty">
          This session has no exported files yet.
        </div>
      )}
      {allJobs.length > 0 && (
        <ToolCard
          title="All renders"
          summary={rendersSummary(allJobs)}
          open={isOpen("renders")} onToggle={() => toggle("renders")}
        >
          <div className="compose-panel__jobs">
            {allJobs.map((job) => (
              <ComposeJobRow
                key={job.id}
                job={job}
                showSession
                onShowSession={(name) => names.includes(name) && setSelected(name)}
              />
            ))}
          </div>
        </ToolCard>
      )}
    </div>
  );
}

function rendersSummary(jobs) {
  const active = jobs.filter((j) => j.state === "running" || j.state === "queued").length;
  return {
    text: `${jobs.length} render${jobs.length === 1 ? "" : "s"}${active ? ` · ${active} running` : ""}`,
    tone: active ? "active" : "muted",
  };
}
