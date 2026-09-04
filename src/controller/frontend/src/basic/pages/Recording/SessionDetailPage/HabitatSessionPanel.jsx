import React from "react";
import socket from "/src/socket";
import { SYNC_TITLE, worstSummary } from "../syncFormat";

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
          <button className="btn btn-secondary" onClick={pause}>
            Pause
          </button>
        )}
        {isPaused && (
          <button className="btn" onClick={resume}>
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
