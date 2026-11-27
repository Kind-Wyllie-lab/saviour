import { useEffect, useMemo, useState } from "react";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import useClockOnce from "/src/hooks/useClockOnce";
import ProtocolTimeline from "/src/basic/components/ProtocolTimeline/ProtocolTimeline";
import useProtocolExpansion from "/src/basic/components/Protocol/useProtocolExpansion";
import { soundModulesOf } from "/src/basic/components/Protocol/protocolModules";
import { fmtTime } from "/src/basic/utils/sessionProtocol";
import "../MJPEGStreamCard/MJPEGStreamCard.css";
import "./ProtocolPlaybackWidget.css";

// Dashboard widget: the protocol of the session that's recording, played back
// live. The timeline is the session's own copy (its random times included),
// with a "now" line at the session's elapsed time, worked out from the
// session's recording_start_at (the protocol's t = 0, the same start every
// module runs it from) on the controller's clock. Shows what was planned, not
// what the modules report doing.

const isTtl = (m) => String(m?.type ?? "").includes("ttl");

/** The recording sessions with a protocol, most recently started first. */
function runningProtocolSessions(sessionList) {
  return sessionList
    .filter((s) => s.protocol && s.state === "active" && s.recording_start_at)
    .sort((a, b) => b.recording_start_at - a.recording_start_at);
}

/** Next thing the protocol does after *t*: { t, text }, or null. */
function nextEvent(result, rows, t) {
  if (!result) return null;
  const label = (key) => rows.find((r) => r.key === key)?.label ?? String(key);
  const cands = [];
  for (const e of result.edges ?? []) {
    if (e.t_s > t && e.level === "high") cands.push({ t: e.t_s, text: `${label(e.pin)} on` });
  }
  for (const c of result.cues ?? []) {
    if (c.t_s > t) cands.push({ t: c.t_s, text: `${label(`snd:${c.output}`)}: ${c.file}` });
  }
  if (!cands.length) return null;
  cands.sort((a, b) => a.t - b.t);
  const first = cands[0];
  const same = cands.filter((c) => Math.abs(c.t - first.t) < 1e-6).map((c) => c.text);
  return { t: first.t, text: [...new Set(same)].join(", ") };
}

export default function ProtocolPlaybackWidget() {
  const { sessionList } = useSessions();
  const { moduleList } = useModules();
  const clock = useClockOnce();
  const running = useMemo(() => runningProtocolSessions(sessionList), [sessionList]);
  const [pick, setPick] = useState(null);
  const session = running.find((s) => s.id === pick) ?? running[0] ?? null;
  const protocol = session?.protocol ?? null;

  // A TTL module in the session names the lines; sound modules the outputs.
  const ttl = useMemo(() => {
    const ids = new Set(session?.modules ?? []);
    return moduleList.find((m) => isTtl(m) && ids.has(m.id)) ?? moduleList.find(isTtl) ?? null;
  }, [session, moduleList]);
  const soundModules = useMemo(() => soundModulesOf(moduleList), [moduleList]);
  const pinConfigs = ttl?.config?.ttl?.pins ?? {};
  const { result, rows, spans, bands } = useProtocolExpansion(
    protocol, null, pinConfigs, ttl?.id ?? null, soundModules, protocol?.outputs?.ttl ?? []);

  // The session clock, ticking 4 times a second while a protocol runs.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!session) return undefined;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [session]);
  const elapsed = session ? (now + (clock?.driftMs ?? 0)) / 1000 - session.recording_start_at : null;
  const total = Math.max(result?.durationS ?? 0, Number(protocol?.duration_s) || 0);
  const done = elapsed !== null && total > 0 && elapsed > total;
  const next = elapsed !== null ? nextEvent(result, rows, Math.max(0, elapsed)) : null;

  return (
    <div className="ppw card">
      <div className="mjpeg-stream-header ppw-header">
        <span className="mjpeg-stream-label">
          {protocol ? `Protocol: ${protocol.name || "unnamed"}` : "Protocol"}
        </span>
        {running.length > 1 && (
          <select className="ppw-pick" value={session?.id ?? ""} onChange={(e) => setPick(e.target.value)}
            title="Several recording sessions have a protocol">
            {running.map((s) => <option key={s.id} value={s.id}>{s.session_name || s.id}</option>)}
          </select>
        )}
      </div>
      {!session ? (
        <p className="ppw-empty">
          No protocol running. When a session with a protocol is recording, its timeline plays here.
        </p>
      ) : (
        <>
          <div className="ppw-status">
            <span className="ppw-clock" title="Time since the session's protocol started (t = 0)">
              {elapsed < 0 ? `starts in ${fmtTime(-elapsed)}` : fmtTime(elapsed)}
              {total > 0 && <span className="ppw-total"> / {fmtTime(total)}</span>}
            </span>
            <span className="ppw-next">
              {done ? "Protocol finished; still recording."
                : next ? <>Next: <b>{next.text}</b> in {fmtTime(next.t - Math.max(0, elapsed))}</>
                : "No more steps."}
            </span>
            <span className="ppw-session">{session.session_name || session.id}</span>
          </div>
          <ProtocolTimeline
            rows={rows}
            spans={spans}
            bands={bands}
            duration={total}
            endT={Number(protocol?.duration_s) || null}
            cursorT={elapsed !== null && elapsed >= 0 ? elapsed : null}
            alwaysShow
            emptyText="This protocol has no steps."
          />
        </>
      )}
    </div>
  );
}
