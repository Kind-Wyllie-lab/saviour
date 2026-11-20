import React, { useState, useEffect, useMemo, useRef } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import useRigCheck, { confirmRigShortfall } from "/src/hooks/useRigCheck";
import { formatBytes } from "../sessionFormat";
import "./HabitatSessionForm.css";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let planSeq = 0;
const nextPlanId = () => `plan${++planSeq}`;

function newPlan(label, modules, strategy) {
  return {
    plan_id: nextPlanId(),
    label,
    modules,
    strategy,
    windows: strategy === "windows" ? [{ start: "20:00", end: "00:00" }] : [],
    days: [],
    segment_minutes: "",
  };
}

// Shape a plan for the wire (drop UI-only empty strings).
function planToWire(p) {
  return {
    plan_id: p.plan_id,
    label: p.label || p.plan_id,
    modules: p.modules,
    strategy: p.strategy,
    windows: p.strategy === "windows" ? p.windows : [],
    days: p.days,
    segment_minutes:
      p.segment_minutes === "" ? undefined : Number(p.segment_minutes),
  };
}

function PlanEditor({ plan, allModules, usedElsewhere, onChange, onRemove }) {
  const set = (patch) => onChange({ ...plan, ...patch });
  const toggleModule = (id) =>
    set({
      modules: plan.modules.includes(id)
        ? plan.modules.filter((m) => m !== id)
        : [...plan.modules, id],
    });
  const toggleDay = (d) =>
    set({
      days: plan.days.includes(d)
        ? plan.days.filter((x) => x !== d)
        : [...plan.days, d].sort((a, b) => a - b),
    });
  const setWindow = (i, patch) =>
    set({ windows: plan.windows.map((w, j) => (j === i ? { ...w, ...patch } : w)) });

  return (
    <div className="hsf-plan">
      <div className="hsf-plan__head">
        <input
          className="hsf-input hsf-plan__label"
          value={plan.label}
          onChange={(e) => set({ label: e.target.value })}
          placeholder="Schedule name"
        />
        <select
          className="hsf-input"
          value={plan.strategy}
          onChange={(e) =>
            set({
              strategy: e.target.value,
              windows:
                e.target.value === "windows" && !plan.windows.length
                  ? [{ start: "20:00", end: "00:00" }]
                  : plan.windows,
            })
          }
        >
          <option value="continuous">Continuous (24/7)</option>
          <option value="windows">Time windows</option>
        </select>
        <button type="button" className="hsf-x" onClick={onRemove} title="Remove schedule"
          aria-label="Remove schedule">
          ✕
        </button>
      </div>

      <div className="hsf-modules" role="group" aria-label="Modules in this schedule">
        {allModules.map((m) => {
          const taken = usedElsewhere.has(m.id);
          return (
            <label
              key={m.id}
              className={"hsf-mod"
                + (plan.modules.includes(m.id) ? " hsf-mod--on" : "")
                + (taken ? " hsf-mod--taken" : "")}
              title={taken ? "Already in another schedule" : m.type}
            >
              <input
                type="checkbox"
                checked={plan.modules.includes(m.id)}
                disabled={taken}
                onChange={() => toggleModule(m.id)}
              />
              {m.name || m.id}
            </label>
          );
        })}
      </div>

      {plan.strategy === "windows" && (
        <div className="hsf-windows">
          {plan.windows.map((w, i) => (
            <div key={i} className="hsf-window">
              <input
                className="hsf-input"
                type="time"
                value={w.start}
                onChange={(e) => setWindow(i, { start: e.target.value })}
              />
              <span className="hsf-window__arrow">→</span>
              <input
                className="hsf-input"
                type="time"
                value={w.end}
                onChange={(e) => setWindow(i, { end: e.target.value })}
              />
              {plan.windows.length > 1 && (
                <button
                  type="button"
                  className="hsf-x"
                  title="Remove window"
                  aria-label="Remove window"
                  onClick={() =>
                    set({ windows: plan.windows.filter((_, j) => j !== i) })
                  }
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            className="secondary-button hsf-small-btn"
            onClick={() =>
              set({ windows: [...plan.windows, { start: "04:00", end: "06:00" }] })
            }
          >
            + Window
          </button>
          <div className="hsf-days day-picker">
            {DAYS.map((d, i) => (
              <button
                type="button"
                key={d}
                className={"day-btn" + (plan.days.includes(i) ? " day-btn--active" : "")}
                onClick={() => toggleDay(i)}
              >
                {d}
              </button>
            ))}
            <span className="hsf-hint">
              {plan.days.length ? "" : "Every day"}
            </span>
          </div>
        </div>
      )}

      <label className="hsf-seg">
        Segment length
        <input
          className="hsf-input"
          type="number"
          min="1"
          max="1440"
          placeholder="default"
          value={plan.segment_minutes}
          onChange={(e) => set({ segment_minutes: e.target.value })}
        />
        min
      </label>
    </div>
  );
}

const STEPS = ["Overview", "Cohort", "Length", "Schedules", "Review"];
const REVIEW = STEPS.length - 1;

// "9 weeks", "9 weeks 3 days", or "" for under a week / no value.
function weeksLabel(days) {
  const d = Math.floor(Number(days) || 0);
  if (d < 7) return "";
  const w = Math.floor(d / 7), r = d % 7;
  return `${w} week${w === 1 ? "" : "s"}` + (r ? ` ${r} day${r === 1 ? "" : "s"}` : "");
}

function windowsText(plan) {
  const w = plan.windows.map((x) => `${x.start}–${x.end}`).join(", ");
  const days = plan.days.length ? plan.days.map((d) => DAYS[d]).join(" ") : "every day";
  return `${w}, ${days}`;
}

// Seed the wizard from an existing Habitat Session (Copy): cohort from the
// habitat_<cohort>_<YYYYMMDD> name, its researcher, length and plans (fresh
// plan ids, runtime fields dropped). Modules that aren't online now are left
// out and listed, since the controller refuses offline modules.
function fromSession(session, onlineIds) {
  const m = /^habitat_(.+)_\d{8}$/.exec(session.session_name || "");
  const minutes = session.expected_minutes ?? session.duration_minutes ?? null;
  const dropped = new Set();
  const plans = (session.plans || []).map((p) => {
    const modules = (p.modules || []).filter((id) => {
      if (onlineIds.has(id)) return true;
      dropped.add(id);
      return false;
    });
    return {
      plan_id: nextPlanId(),
      label: p.label || "",
      modules,
      strategy: p.strategy || "continuous",
      windows: (p.windows || []).map((w) => ({ start: w.start, end: w.end })),
      days: [...(p.days || [])],
      segment_minutes: p.segment_minutes ?? "",
    };
  });
  return {
    cohort: m ? m[1] : "",
    researcher: session.researcher || "",
    expectedDays: minutes ? Math.round(minutes / 1440) : 63,
    autoStop: session.duration_minutes != null,
    plans,
    dropped: [...dropped],
    source: session.session_name,
  };
}

/**
 * Habitat Session setup: a step-by-step modal (Overview, Cohort, Length,
 * Recording schedules) ending
 * on a Review page that must be passed through to create the session. Each
 * review section has Edit, which opens that step with "Back to review".
 * `copyFrom` (a session) opens straight on Review, pre-filled.
 */
export default function HabitatSessionForm({
  modules, copyFrom, onClose, onSessionCreated, onUseStandard,
}) {
  const rig = useRigCheck();
  const online = useMemo(
    () => (modules || []).filter((m) => m.online !== false),
    [modules],
  );
  const nameOf = (id) => {
    const mod = (modules || []).find((m) => m.id === id);
    return mod?.name || id;
  };

  const seed = useMemo(
    () => (copyFrom ? fromSession(copyFrom, new Set(online.map((m) => m.id))) : null),
    [], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const [step, setStep] = useState(seed ? REVIEW : 0);
  // Set after visiting Review: an edited step returns there.
  const [reviewed, setReviewed] = useState(!!seed);

  const [cohort, setCohort] = useState(seed?.cohort ?? "");
  const [researcher, setResearcher] = useState(seed?.researcher ?? "");
  const [expectedDays, setExpectedDays] = useState(seed?.expectedDays ?? 63); // a 9-week cohort
  const [autoStop, setAutoStop] = useState(seed?.autoStop ?? false);
  const [plans, setPlans] = useState(() => {
    if (seed?.plans?.length) return seed.plans;
    const cams = online.filter((m) => (m.type || "").includes("camera"));
    const mics = online.filter((m) => (m.type || "").includes("microphone"));
    const p = [];
    if (cams.length) p.push(newPlan("Cameras", cams.map((m) => m.id), "continuous"));
    if (mics.length) p.push(newPlan("Night audio", mics.map((m) => m.id), "windows"));
    if (!p.length) p.push(newPlan("Schedule 1", [], "continuous"));
    return p;
  });

  // habitat_<cohort>_<YYYYMMDD> -- the session name is the cohort ID plus
  // today's date; nothing else to type.
  const cleanCohort = cohort.trim().replace(/[^A-Za-z0-9-]/g, "");
  const startDate = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const sessionName = cleanCohort ? `habitat_${cleanCohort}_${startDate}` : "";

  const [estimate, setEstimate] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const estimateTimer = useRef(null);

  const expectedMinutes = Math.max(0, Number(expectedDays) || 0) * 1440;
  const weeks = weeksLabel(expectedDays);

  useEffect(() => {
    const onEst = (d) => setEstimate(d);
    const onResult = (d) => {
      setCreating(false);
      if (!d?.success) return;
      // Without auto-stop the length is still the operator's estimate:
      // store it (display + share-space projection only, never stops).
      if (!autoStop && expectedMinutes > 0) {
        socket.emit("set_session_expected_duration", {
          session_name: d.session_name, minutes: expectedMinutes,
        });
      }
      onSessionCreated?.(d.session_name);
    };
    const onErr = (d) => {
      setCreating(false);
      setError(d?.error || "Could not create session");
    };
    socket.on("habitat_volume_estimate", onEst);
    socket.on("create_session_result", onResult);
    socket.on("session_error", onErr);
    return () => {
      socket.off("habitat_volume_estimate", onEst);
      socket.off("create_session_result", onResult);
      socket.off("session_error", onErr);
    };
  }, [onSessionCreated, autoStop, expectedMinutes]);

  // Debounced volume estimate whenever the plan shape or length changes.
  useEffect(() => {
    clearTimeout(estimateTimer.current);
    estimateTimer.current = setTimeout(() => {
      socket.emit("estimate_habitat_volume", {
        plans: plans.map(planToWire),
        expected_minutes: expectedMinutes,
      });
    }, 400);
    return () => clearTimeout(estimateTimer.current);
  }, [plans, expectedMinutes]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const usedByOthers = (planId) =>
    new Set(plans.filter((p) => p.plan_id !== planId).flatMap((p) => p.modules));
  const setPlan = (planId, next) =>
    setPlans((ps) => ps.map((p) => (p.plan_id === planId ? next : p)));

  // Why the given step can't be left yet ("" = fine).
  const stepProblem = (i) => {
    if (i === 1 && !cleanCohort) return "Enter a cohort ID";
    if (i === 2 && !(Number(expectedDays) > 0)) return "Enter the expected length in days";
    if (i === 3) {
      if (!plans.some((p) => p.modules.length)) return "Add at least one module to a schedule";
      const bad = plans.find((p) => p.strategy === "windows" && !p.windows.length);
      if (bad) return `"${bad.label || "Schedule"}" needs at least one time window`;
    }
    return "";
  };
  const firstProblemStep = [1, 2, 3].find((i) => stepProblem(i));
  const problem = step < REVIEW ? stepProblem(step) : "";

  const goNext = () => {
    if (problem) return;
    setError("");
    if (reviewed || step + 1 === REVIEW) {
      setReviewed(true);
      setStep(REVIEW);
    } else {
      setStep(step + 1);
    }
  };

  const submit = () => {
    setError("");
    if (firstProblemStep !== undefined) {
      setStep(firstProblemStep);
      return;
    }
    if (!confirmRigShortfall(rig, "Create this session")) return;
    setCreating(true);
    socket.emit("create_habitat_session", {
      session_name: sessionName.trim(),
      researcher: researcher.trim() || undefined,
      duration_minutes: autoStop ? expectedMinutes : undefined,
      plans: plans.map(planToWire),
    });
  };

  const fits = estimate && estimate.share_free_bytes != null && estimate.fits;
  const overSpace = estimate && estimate.share_free_bytes != null && !estimate.fits;

  const storage = estimate?.plans && (
    <div className={"hsf-vol" + (overSpace ? " hsf-vol--over" : "")}>
      <h3 className="hsf-section__title">Storage over {expectedDays} days</h3>
      {estimate.plans.map((p) => (
        <div key={p.plan_id} className="hsf-vol__row">
          <span>{p.label}</span>
          <span className="hsf-vol__duty">{Math.round(p.duty_fraction * 100)}% of the time</span>
          <span className="hsf-vol__bytes">{formatBytes(p.projected_bytes)}</span>
        </div>
      ))}
      <div className="hsf-vol__row hsf-vol__row--total">
        <span>Total</span>
        <span />
        <span className="hsf-vol__bytes">{formatBytes(estimate.projected_bytes_total)}</span>
      </div>
      {estimate.share_free_bytes != null && (
        <div className="hsf-vol__free">
          {formatBytes(estimate.share_free_bytes)} free on the share:{" "}
          {fits ? "fits" : "will NOT fit"}
        </div>
      )}
    </div>
  );

  // A plain render helper (not a component), so it isn't remounted each render.
  const reviewSection = (title, at, children) => (
    <div className="hsf-review__section" key={title}>
      <div className="hsf-review__head">
        <h3 className="hsf-section__title">{title}</h3>
        <button type="button" className="hsf-review__edit" onClick={() => setStep(at)}>
          Edit
        </button>
      </div>
      {children}
      {stepProblem(at) && <p className="form-warning">{stepProblem(at)}</p>}
    </div>
  );

  return (
    <div className="modal-overlay hsf-overlay" onClick={() => onClose?.()}>
      <div
        className="modal hsf-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hsf-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="hsf-modal__head">
          <h2 id="hsf-title">
            {seed ? "Copy Habitat Session" : "New Habitat Session"}
          </h2>
          <button type="button" className="hsf-x" onClick={() => onClose?.()}
            title="Close" aria-label="Close">✕</button>
        </div>

        <ol className="hsf-steps">
          {STEPS.map((label, i) => {
            const reachable = reviewed || i <= step;
            return (
              <li key={label}
                className={"hsf-steps__item"
                  + (i === step ? " is-current" : "")
                  + (i < step || (reviewed && i !== step) ? " is-done" : "")}>
                <button type="button" disabled={!reachable || i === step}
                  onClick={() => setStep(i)}>
                  <span className="hsf-steps__num">{i + 1}</span>
                  {label}
                </button>
              </li>
            );
          })}
        </ol>

        <div className="hsf-modal__body">
          {step === 0 && (
            <section className="hsf-section hsf-intro">
              <p>
                Habitat uses special recording sessions that are intended to
                run for the duration of an entire cohort, e.g. 9 weeks. They
                can be paused and resumed for maintenance. Habitat sessions
                allow for scheduling logic: for example, all cameras may record
                24/7 for the whole session while microphones only record for a
                fixed window each day (e.g. 20:00 to 02:00 the following day).
              </p>
              <p>
                As Habitat sessions run for a long time autonomously, you may
                wish to{" "}
                <Link to="/settings#controller/alerts" onClick={() => onClose?.()}>
                  set up alerts
                </Link>{" "}
                (Teams/NTFY) to stay updated.
              </p>
            </section>
          )}

          {step === 1 && (
            <section className="hsf-section">
              <div className="hsf-row">
                <label className="hsf-field">
                  <span>Cohort ID</span>
                  <input className="hsf-input" value={cohort}
                    onChange={(e) => setCohort(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") goNext(); }}
                    placeholder="e.g. CRLLT3" autoFocus />
                </label>
                <label className="hsf-field">
                  <span>Researcher <em>(optional)</em></span>
                  <input className="hsf-input" value={researcher}
                    onChange={(e) => setResearcher(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") goNext(); }} />
                </label>
              </div>
              <div className="hsf-name-preview">
                Session name
                <code>{sessionName || "habitat_<cohort>_" + startDate}</code>
              </div>
            </section>
          )}

          {step === 2 && (
            <section className="hsf-section">
              <div className="hsf-len">
                <input className="hsf-input" type="number" min="1" autoFocus
                  value={expectedDays}
                  onChange={(e) => setExpectedDays(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") goNext(); }}
                  aria-label="Expected length in days" />
                <span>days expected</span>
                {weeks && <span className="hsf-weeks">({weeks})</span>}
              </div>
              <label className="hsf-autostop">
                <input type="checkbox" checked={autoStop}
                  onChange={(e) => setAutoStop(e.target.checked)} />
                Stop automatically when it elapses
              </label>
              <p className="hsf-section__hint">
                {autoStop
                  ? "The session stops itself at the end of this period."
                  : "Used for the storage estimate and the session's expected end; the session keeps running until you stop it."}
              </p>
              {storage}
            </section>
          )}

          {step === 3 && (
            <section className="hsf-section">
              <p className="hsf-section__hint">
                Each module belongs to one schedule: record continuously, or only
                in daily time windows.
              </p>
              <div className="hsf-plans">
                {plans.map((p) => (
                  <PlanEditor
                    key={p.plan_id}
                    plan={p}
                    allModules={online}
                    usedElsewhere={usedByOthers(p.plan_id)}
                    onChange={(next) => setPlan(p.plan_id, next)}
                    onRemove={() =>
                      setPlans((ps) =>
                        ps.length > 1 ? ps.filter((x) => x.plan_id !== p.plan_id) : ps,
                      )
                    }
                  />
                ))}
                <button type="button" className="secondary-button hsf-small-btn hsf-add-plan"
                  onClick={() => setPlans((ps) => [...ps, newPlan(`Schedule ${ps.length + 1}`, [], "continuous")])}>
                  + Schedule
                </button>
              </div>
              {storage}
            </section>
          )}

          {step === REVIEW && (
            <section className="hsf-review">
              {seed && (
                <p className="form-hint">
                  Copied from <code>{seed.source}</code>. Check each part, then create.
                </p>
              )}
              {seed?.dropped.length > 0 && (
                <p className="form-warning">
                  Not online, left out: {seed.dropped.map(nameOf).join(", ")}
                </p>
              )}
              {reviewSection("Cohort", 1, (<>
                <dl className="hsf-review__dl">
                  <dt>Session</dt><dd><code>{sessionName || "—"}</code></dd>
                  <dt>Researcher</dt><dd>{researcher.trim() || "—"}</dd>
                </dl>
              </>))}
              {reviewSection("Length", 2, (<>
                <dl className="hsf-review__dl">
                  <dt>Expected</dt>
                  <dd>{expectedDays} days{weeks ? ` (${weeks})` : ""}</dd>
                  <dt>Stop</dt>
                  <dd>{autoStop ? "Automatically when it elapses" : "Manually"}</dd>
                </dl>
              </>))}
              {reviewSection("Recording schedules", 3, (<>
                <ul className="hsf-review__plans">
                  {plans.map((p) => (
                    <li key={p.plan_id}>
                      <strong>{p.label || "Schedule"}</strong>
                      <span className="hsf-review__strategy">
                        {p.strategy === "continuous" ? "24/7" : windowsText(p)}
                        {p.segment_minutes !== "" && p.segment_minutes != null
                          ? `, ${p.segment_minutes} min segments` : ""}
                      </span>
                      <span className="hsf-review__mods">
                        {p.modules.length ? p.modules.map(nameOf).join(", ") : "No modules"}
                      </span>
                    </li>
                  ))}
                </ul>
              </>))}
              {storage}
              {error && <p className="form-warning">{error}</p>}
            </section>
          )}
        </div>

        <div className="hsf-modal__foot">
          {step === 0 && !seed && onUseStandard ? (
            <button type="button" className="hsf-link-btn" onClick={onUseStandard}>
              Standard session instead
            </button>
          ) : step > 0 && step < REVIEW && !reviewed ? (
            <button type="button" className="secondary-button" onClick={() => setStep(step - 1)}>
              Back
            </button>
          ) : <span />}
          <div className="hsf-modal__foot-right">
            {problem && <span className="hsf-problem">{problem}</span>}
            {step < REVIEW ? (
              <button type="button" className="primary-button" onClick={goNext} disabled={!!problem}>
                {reviewed ? "Back to review" : step + 1 === REVIEW ? "Review" : "Next"}
              </button>
            ) : (
              <button type="button" className="primary-button" onClick={submit}
                disabled={creating || firstProblemStep !== undefined}>
                {creating ? "Creating…" : "Create Habitat Session"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
