import React, { useState, useEffect, useMemo, useRef } from "react";
import socket from "/src/socket";
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
    windows: strategy === "windows" ? [{ start: "20:00", end: "06:00" }] : [],
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
          className="hsf-plan__label"
          value={plan.label}
          onChange={(e) => set({ label: e.target.value })}
          placeholder="Plan name"
        />
        <select
          value={plan.strategy}
          onChange={(e) =>
            set({
              strategy: e.target.value,
              windows:
                e.target.value === "windows" && !plan.windows.length
                  ? [{ start: "20:00", end: "06:00" }]
                  : plan.windows,
            })
          }
        >
          <option value="continuous">Continuous (24/7)</option>
          <option value="windows">Time windows</option>
        </select>
        <button type="button" className="hsf-x" onClick={onRemove} title="Remove plan">
          ✕
        </button>
      </div>

      <div className="hsf-modules">
        {allModules.map((m) => {
          const taken = usedElsewhere.has(m.id);
          return (
            <label
              key={m.id}
              className={"hsf-mod" + (taken ? " hsf-mod--taken" : "")}
              title={taken ? "already in another plan" : m.type}
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
                type="time"
                value={w.start}
                onChange={(e) => setWindow(i, { start: e.target.value })}
              />
              <span>→</span>
              <input
                type="time"
                value={w.end}
                onChange={(e) => setWindow(i, { end: e.target.value })}
              />
              {plan.windows.length > 1 && (
                <button
                  type="button"
                  className="hsf-x"
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
            className="btn btn-small"
            onClick={() =>
              set({ windows: [...plan.windows, { start: "04:00", end: "06:00" }] })
            }
          >
            + window
          </button>
          <div className="hsf-days">
            {DAYS.map((d, i) => (
              <button
                type="button"
                key={d}
                className={"hsf-day" + (plan.days.includes(i) ? " hsf-day--on" : "")}
                onClick={() => toggleDay(i)}
              >
                {d}
              </button>
            ))}
            <span className="hsf-hint">
              {plan.days.length ? "" : "every day"}
            </span>
          </div>
        </div>
      )}

      <label className="hsf-seg">
        Segment length
        <input
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

export default function HabitatSessionForm({ modules, onSessionCreated }) {
  const online = useMemo(
    () => (modules || []).filter((m) => m.online !== false),
    [modules],
  );
  const cams = online.filter((m) => (m.type || "").includes("camera"));
  const mics = online.filter((m) => (m.type || "").includes("microphone"));

  const [cohort, setCohort] = useState("");
  const [researcher, setResearcher] = useState("");
  const [expectedDays, setExpectedDays] = useState(7);

  // habitat_<cohort>_<YYYYMMDD> — the whole session name comes from the
  // cohort ID + today's date; nothing else to type.
  const cleanCohort = cohort.trim().replace(/[^A-Za-z0-9-]/g, "");
  const startDate = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const sessionName = cleanCohort ? `habitat_${cleanCohort}_${startDate}` : "";
  const [autoStop, setAutoStop] = useState(false);
  const [plans, setPlans] = useState(() => {
    const p = [];
    if (cams.length) p.push(newPlan("Cameras", cams.map((m) => m.id), "continuous"));
    if (mics.length) p.push(newPlan("Night audio", mics.map((m) => m.id), "windows"));
    if (!p.length) p.push(newPlan("Plan 1", [], "continuous"));
    return p;
  });
  const [estimate, setEstimate] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const estimateTimer = useRef(null);

  const expectedMinutes = Math.max(0, Number(expectedDays) || 0) * 1440;

  useEffect(() => {
    const onEst = (d) => setEstimate(d);
    const onResult = (d) => {
      setCreating(false);
      if (d?.success) onSessionCreated?.(d.session_name);
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
  }, [onSessionCreated]);

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

  const usedByOthers = (planId) =>
    new Set(plans.filter((p) => p.plan_id !== planId).flatMap((p) => p.modules));

  const setPlan = (planId, next) =>
    setPlans((ps) => ps.map((p) => (p.plan_id === planId ? next : p)));

  const submit = () => {
    setError("");
    if (!cleanCohort) return setError("Cohort ID is required");
    if (!plans.some((p) => p.modules.length))
      return setError("Add at least one module to a plan");
    setCreating(true);
    socket.emit("create_habitat_session", {
      session_name: sessionName.trim(),
      researcher: researcher.trim() || undefined,
      duration_minutes: autoStop ? expectedMinutes : undefined,
      plans: plans.map(planToWire),
    });
  };

  const fits = estimate && estimate.share_free_bytes != null && estimate.fits;
  const overSpace =
    estimate && estimate.share_free_bytes != null && !estimate.fits;

  return (
    <div className="hsf">
      <div className="hsf-row">
        <label className="form-field">
          <span>Cohort ID</span>
          <input
            value={cohort}
            onChange={(e) => setCohort(e.target.value)}
            placeholder="e.g. CRLLT3"
            autoFocus
          />
        </label>
        <label className="form-field">
          <span>Researcher <em>(optional)</em></span>
          <input
            value={researcher}
            onChange={(e) => setResearcher(e.target.value)}
          />
        </label>
      </div>
      <div className="hsf-name-preview">
        Session: <code>{sessionName || "habitat_<cohort>_" + startDate}</code>
      </div>

      <label className="form-field hsf-len">
        <span>Expected length</span>
        <input
          type="number"
          min="1"
          value={expectedDays}
          onChange={(e) => setExpectedDays(e.target.value)}
        />
        days
        <label className="hsf-autostop">
          <input
            type="checkbox"
            checked={autoStop}
            onChange={(e) => setAutoStop(e.target.checked)}
          />
          auto-stop when it elapses
        </label>
      </label>

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
        <button
          type="button"
          className="btn btn-small"
          onClick={() => setPlans((ps) => [...ps, newPlan(`Plan ${ps.length + 1}`, [], "continuous")])}
        >
          + plan
        </button>
      </div>

      {estimate?.plans && (
        <div className={"hsf-vol" + (overSpace ? " hsf-vol--over" : "")}>
          <div className="hsf-vol__title">
            Projected over {expectedDays} days
          </div>
          {estimate.plans.map((p) => (
            <div key={p.plan_id} className="hsf-vol__row">
              <span>{p.label}</span>
              <span>{Math.round(p.duty_fraction * 100)}% duty</span>
              <span>{formatBytes(p.projected_bytes)}</span>
            </div>
          ))}
          <div className="hsf-vol__row hsf-vol__row--total">
            <span>Total</span>
            <span />
            <span>{formatBytes(estimate.projected_bytes_total)}</span>
          </div>
          {estimate.share_free_bytes != null && (
            <div className="hsf-vol__free">
              {formatBytes(estimate.share_free_bytes)} free —{" "}
              {fits ? "fits" : "will NOT fit"}
            </div>
          )}
        </div>
      )}

      {error && <div className="compose-job__error">{error}</div>}

      <button className="btn" onClick={submit} disabled={creating}>
        {creating ? "Creating…" : "Create Habitat Session"}
      </button>
    </div>
  );
}
