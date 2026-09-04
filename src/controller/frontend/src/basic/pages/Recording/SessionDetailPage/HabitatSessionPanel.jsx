import React, { useState, useEffect, useMemo } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { SYNC_TITLE, worstSummary } from "../syncFormat";
import { formatEpochTime, humanizeMinutes } from "../sessionFormat";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function windowSummary(plan) {
  if (plan.strategy !== "windows") return "continuous — 24/7";
  const wins = (plan.windows || [])
    .map((w) => `${w.start}–${w.end}`)
    .join(", ");
  const days =
    plan.days && plan.days.length
      ? plan.days.map((d) => DAY_LABELS[d] || d).join(" ")
      : "every day";
  return `${wins}  (${days})`;
}

function PlanRow({ plan }) {
  return (
    <div className="habitat-plan">
      <div className="habitat-plan__head">
        <span
          className={
            "habitat-plan__dot " +
            (plan.recording ? "habitat-plan__dot--on" : "")
          }
          title={plan.recording ? "Recording now" : "Idle"}
        />
        <span className="habitat-plan__label">{plan.label || plan.plan_id}</span>
        <span className="habitat-plan__strategy">{plan.strategy}</span>
      </div>
      <div className="habitat-plan__meta">{windowSummary(plan)}</div>
      <div className="habitat-plan__modules">
        {(plan.modules || []).join(", ")}
        {plan.segment_minutes ? ` · ${plan.segment_minutes} min segments` : ""}
      </div>
    </div>
  );
}

function DayRow({ sessionName, day, verdict, onDownloadReport }) {
  const status = verdict.status || "skipped";
  return (
    <div className="habitat-day">
      <span
        className={`habitat-day__dot habitat-day__dot--${status}`}
        title={(verdict.reasons || []).slice(0, 3).join(" · ") || SYNC_TITLE[status]}
      />
      <span className="habitat-day__date">{day}</span>
      <span className="habitat-day__summary">{worstSummary(verdict)}</span>
      {verdict.report_rel && (
        <button
          type="button"
          className="session-log-toggle"
          onClick={() => onDownloadReport?.(verdict.report_rel)}
        >
          report
        </button>
      )}
      <button
        type="button"
        className="session-log-toggle"
        onClick={() =>
          socket.emit("recheck_framesync", {
            session_name: sessionName, scope: "day", date_dir: day,
          })
        }
      >
        re-check
      </button>
    </div>
  );
}

// GB shown to operators is decimal (/1e9) to match disk-vendor / share
// dashboards; comparisons are all done in raw bytes on the backend.
const gb = (bytes) =>
  bytes == null ? "—" : `${(bytes / 1e9).toFixed(bytes < 1e11 ? 1 : 0)} GB`;

function ExpectedRunEditor({ sessionName, session, canEdit }) {
  const startRef = session.recording_start_at || Date.now() / 1000;
  const currentEnd =
    session.expected_minutes != null
      ? startRef + session.expected_minutes * 60
      : null;

  const [value, setValue] = useState(() => {
    if (session.expected_minutes == null) return "";
    const days = session.expected_minutes / 60 / 24;
    return days >= 14
      ? String(Math.round(days / 7))
      : String(Math.round(days));
  });
  const [unit, setUnit] = useState(() =>
    session.expected_minutes != null && session.expected_minutes / 60 / 24 >= 14
      ? "weeks"
      : "days",
  );
  const [endDate, setEndDate] = useState("");

  const previewMinutes = useMemo(() => {
    if (endDate) {
      const target = new Date(`${endDate}T12:00:00`).getTime() / 1000;
      return Math.round((target - startRef) / 60);
    }
    const n = parseFloat(value);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.round(n * (unit === "weeks" ? 7 : 1) * 24 * 60);
  }, [value, unit, endDate, startRef]);

  const previewEnd =
    previewMinutes != null ? startRef + previewMinutes * 60 : null;

  const submit = () => {
    if (previewMinutes == null || previewMinutes <= 0) return;
    socket.emit("set_session_expected_duration", {
      session_name: sessionName,
      minutes: previewMinutes,
    });
    setEndDate("");
  };
  const clear = () => {
    socket.emit("set_session_expected_duration", {
      session_name: sessionName,
      minutes: null,
    });
    setValue("");
    setEndDate("");
  };

  if (!canEdit) {
    return (
      <div className="habitat-space__row habitat-space__row--muted">
        {session.expected_minutes != null
          ? `Expected run ${humanizeMinutes(session.expected_minutes)}` +
            (currentEnd ? ` — ends ~${formatEpochTime(currentEnd)}` : "")
          : "No expected run length set (log in to set one)"}
      </div>
    );
  }

  return (
    <div className="habitat-space__editor">
      <span className="habitat-space__editor-label">Expected run</span>
      <input
        type="number"
        min="1"
        className="habitat-space__num"
        value={endDate ? "" : value}
        disabled={!!endDate}
        onChange={(e) => setValue(e.target.value)}
      />
      <select
        className="habitat-space__unit"
        value={unit}
        disabled={!!endDate}
        onChange={(e) => setUnit(e.target.value)}
      >
        <option value="days">days</option>
        <option value="weeks">weeks</option>
      </select>
      <span className="habitat-space__or">or end</span>
      <input
        type="date"
        className="habitat-space__date"
        value={endDate}
        onChange={(e) => setEndDate(e.target.value)}
      />
      <button
        type="button"
        className="session-btn session-btn--start"
        onClick={submit}
        disabled={previewMinutes == null || previewMinutes <= 0}
      >
        Set
      </button>
      {session.expected_minutes != null && (
        <button
          type="button"
          className="session-btn session-btn--cancel"
          onClick={clear}
        >
          Clear
        </button>
      )}
      {previewEnd && (
        <span className="habitat-space__preview">
          ≈ {humanizeMinutes(previewMinutes)}, ends {formatEpochTime(previewEnd)}
        </span>
      )}
    </div>
  );
}

function DataSpaceBlock({ session }) {
  const loggedIn = useIsLoggedIn();
  const sessionName = session.session_name;
  const [proj, setProj] = useState(null);

  useEffect(() => {
    const onEstimate = (data) => {
      if (data && data.session_name === sessionName) setProj(data);
    };
    socket.on("session_projection_estimate", onEstimate);
    const ask = () =>
      socket.emit("estimate_session_projection", { session_name: sessionName });
    ask();
    const id = setInterval(ask, 60_000);
    return () => {
      socket.off("session_projection_estimate", onEstimate);
      clearInterval(id);
    };
  }, [
    sessionName,
    session.expected_minutes,
    session.timed_stop_at,
    session.state,
  ]);

  const ok = proj && proj.success;
  const openEnded = ok && proj.horizon_minutes == null;
  const plannedEndEpoch =
    session.timed_stop_at ||
    (ok && !openEnded
      ? (session.recording_start_at || Date.now() / 1000) +
        proj.horizon_minutes * 60
      : null);

  return (
    <div className="habitat-space">
      <div className="habitat-space__head">Data &amp; space</div>

      {!ok ? (
        <div className="habitat-space__row habitat-space__row--muted">
          Estimating data rate…
        </div>
      ) : (
        <>
          <div className="habitat-space__row">
            <span className="habitat-space__k">Data rate</span>
            <span>
              {proj.gb_per_hour.toFixed(1)} GB/hr ·{" "}
              {proj.gb_per_day.toFixed(0)} GB/day
              <span className="habitat-space__sub">
                {" "}(duty-cycle averaged)
              </span>
            </span>
          </div>

          <div className="habitat-space__row">
            <span className="habitat-space__k">Planned end</span>
            <span>
              {session.timed_stop_at
                ? `${formatEpochTime(session.timed_stop_at)} (auto-stop)`
                : openEnded
                  ? "Open-ended — no planned end"
                  : plannedEndEpoch
                    ? `~${formatEpochTime(plannedEndEpoch)} (${proj.horizon_source === "expected_minutes" ? "estimated" : proj.horizon_source})`
                    : "—"}
            </span>
          </div>

          <div className="habitat-space__row">
            <span className="habitat-space__k">Share free</span>
            <span>
              {gb(proj.free_bytes)}
              {proj.free_pct != null && ` (${proj.free_pct}%)`}
              {proj.runway_hours != null &&
                ` · runway ≈ ${humanizeMinutes(proj.runway_hours * 60)}`}
            </span>
          </div>

          {!openEnded && proj.projected_bytes_to_horizon != null && (
            <div
              className={
                "habitat-space__banner " +
                (proj.fits === false
                  ? "session-warning-text"
                  : "habitat-space__banner--ok")
              }
            >
              {proj.fits === false ? (
                <>
                  ⚠ Projected ~{gb(proj.projected_bytes_to_horizon)} before the
                  planned end — only {gb(proj.free_bytes)} free
                  {proj.shortfall_bytes
                    ? ` (short by ~${gb(proj.shortfall_bytes)})`
                    : ""}
                  . Extend the share, shorten the run, or expect an auto-pause.
                </>
              ) : (
                <>
                  ≈ {gb(proj.projected_bytes_to_horizon)} projected vs{" "}
                  {gb(proj.free_bytes)} free — fits.
                </>
              )}
            </div>
          )}
        </>
      )}

      <ExpectedRunEditor
        sessionName={sessionName}
        session={session}
        canEdit={loggedIn && !session.timed_stop_at}
      />
    </div>
  );
}

export default function HabitatSessionPanel({ session, onDownloadReport }) {
  const plans = session?.plans || [];
  if (!plans.length) return null;

  const dayVerdicts = session.day_verdicts || {};
  const days = Object.keys(dayVerdicts).sort().reverse();

  const state = session.state;
  const isActive = state === "active";
  const isPaused = state === "paused";

  const pause = () =>
    socket.emit("pause_session", { session_name: session.session_name });
  const resume = () =>
    socket.emit("resume_session", { session_name: session.session_name });

  const recordingNow = plans.filter((p) => p.recording).length;

  return (
    <section className="card habitat-session-panel">
      <div className="habitat-session-panel__head">
        <h3>Habitat Session</h3>
        <span
          className={`habitat-session-panel__state habitat-session-panel__state--${state}`}
        >
          {isPaused ? "Paused" : isActive ? "Active" : state}
        </span>
        {isActive && (
          <button
            type="button"
            className="session-btn session-btn--cancel habitat-session-panel__action"
            onClick={pause}
          >
            Pause
          </button>
        )}
        {isPaused && (
          <button
            type="button"
            className="session-btn session-btn--start habitat-session-panel__action"
            onClick={resume}
          >
            Resume
          </button>
        )}
      </div>

      <p className="modal-subtext">
        {plans.length} plan{plans.length !== 1 ? "s" : ""} ·{" "}
        {isPaused
          ? "all plans stopped — Resume re-arms whatever is in-window"
          : `${recordingNow} recording now`}
      </p>

      <div className="habitat-session-panel__plans">
        {plans.map((p) => (
          <PlanRow key={p.plan_id} plan={p} />
        ))}
      </div>

      <DataSpaceBlock session={session} />

      {days.length > 0 && (
        <div className="habitat-session-panel__days">
          <div className="habitat-session-panel__days-head">
            Recording days — sync quality
          </div>
          {days.map((d) => (
            <DayRow
              key={d}
              sessionName={session.session_name}
              day={d}
              verdict={dayVerdicts[d]}
              onDownloadReport={onDownloadReport}
            />
          ))}
        </div>
      )}
    </section>
  );
}
