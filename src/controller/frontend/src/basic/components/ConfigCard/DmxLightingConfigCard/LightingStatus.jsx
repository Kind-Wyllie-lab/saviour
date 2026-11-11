import { useEffect, useState } from "react";
import useLightingState, { remainingS, fmtDuration } from "/src/hooks/useLightingState";
import { PHASE_LABEL } from "./lightingSchedule";

const SOURCE_LABEL = { schedule: "schedule", override: "override", blackout: "blackout", test: "test" };

function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => { const t = setInterval(() => set((n) => n + 1), ms); return () => clearInterval(t); }, [ms]);
}

function fmtClock(epochS) {
  return new Date(epochS * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function LightingSummary({ state }) {
  useTick();
  if (!state) return <p className="dmx-muted">Waiting for the module to report…</p>;
  const sch = state.schedule;
  const nextIn = sch ? sch.next_change_at - Date.now() / 1000 : null;
  return (
    <div className="dmx-summary">
      <span className={`dmx-mode dmx-mode--${state.mode}`}>{PHASE_LABEL[state.mode] ?? state.mode}</span>
      {sch && (
        <span>
          {sch.phase !== state.mode && <>{PHASE_LABEL[sch.phase]} on schedule · </>}
          visible {sch.visible.toFixed(0)}%, IR {sch.ir.toFixed(0)}%
          {" · "}{PHASE_LABEL[sch.next_change] ?? sch.next_change} at {fmtClock(sch.next_change_at)}
          {nextIn != null && nextIn > 0 && <> (in {fmtDuration(nextIn)})</>}
        </span>
      )}
      {state.mock && <span className="dmx-badge">MOCK widget</span>}
    </div>
  );
}

export function LightingProblems({ state }) {
  if (!state) return null;
  const items = [];
  if (state.config_error) items.push(`Config not applied (previous schedule still running): ${state.config_error}`);
  if (state.fault) items.push(`${state.fault}. The decoders are on their own DMX-loss behaviour.`);
  if (state.clock && !state.clock.trusted) {
    items.push(`Waiting for a trusted clock (PTP or NTP) before driving the lights: ${fmtDuration(state.clock.waiting_s ?? 0)} so far.`);
  }
  if (!items.length) return null;
  return <div className="dmx-problems">{items.map((t) => <p key={t}>{t}</p>)}</div>;
}

function OverrideForm({ boxes, maxMinutes, send }) {
  const [target, setTarget] = useState("all");
  const [picked, setPicked] = useState([]);
  const [visible, setVisible] = useState("100");
  const [ir, setIr] = useState("");
  const [minutes, setMinutes] = useState("30");
  const [note, setNote] = useState("");
  const [blackoutMin, setBlackoutMin] = useState("30");

  const togglePick = (b) => setPicked((p) => (p.includes(b) ? p.filter((x) => x !== b) : [...p, b]));
  const mins = Number(minutes);
  const okMinutes = mins > 0 && mins <= maxMinutes;
  const okLevel = (v) => v === "" || (Number(v) >= 0 && Number(v) <= 100);
  const canApply = okMinutes && okLevel(visible) && okLevel(ir) && (visible !== "" || ir !== "")
    && (target === "all" || picked.length > 0);

  const apply = () => send("set_override", {
    boxes: target === "all" ? null : picked,
    visible: visible === "" ? null : Number(visible),
    ir: ir === "" ? null : Number(ir),
    minutes: mins, note,
  });

  return (
    <div className="dmx-override-form">
      <h4>Manual override</h4>
      <p className="dmx-muted">Sets the lights for a fixed time, then returns to the schedule (at most {maxMinutes} min). Leave a level empty to keep that strip on its schedule.</p>
      <div className="dmx-row">
        <label><input type="radio" checked={target === "all"} onChange={() => setTarget("all")} /> All compartments</label>
        <label><input type="radio" checked={target === "some"} onChange={() => setTarget("some")} /> Selected</label>
      </div>
      {target === "some" && (
        <div className="dmx-box-picks">
          {boxes.length === 0 && <span className="dmx-muted">No compartments in the patch yet.</span>}
          {boxes.map((b) => (
            <label key={b} className={`dmx-box-pick${picked.includes(b) ? " dmx-box-pick--on" : ""}`}>
              <input type="checkbox" checked={picked.includes(b)} onChange={() => togglePick(b)} />{b}
            </label>
          ))}
        </div>
      )}
      <div className="dmx-row">
        <label className="dmx-field">Visible %<input type="number" min="0" max="100" value={visible} onChange={(e) => setVisible(e.target.value)} /></label>
        <label className="dmx-field">IR %<input type="number" min="0" max="100" value={ir} placeholder="schedule" onChange={(e) => setIr(e.target.value)} /></label>
        <label className="dmx-field">For (min)<input type="number" min="1" max={maxMinutes} value={minutes} onChange={(e) => setMinutes(e.target.value)} /></label>
        <label className="dmx-field dmx-field--wide">Note<input type="text" value={note} placeholder="e.g. cage check" onChange={(e) => setNote(e.target.value)} /></label>
      </div>
      <div className="dmx-row">
        <button type="button" className="dmx-btn dmx-btn--primary" disabled={!canApply} onClick={apply}>Apply override</button>
        {!okMinutes && <span className="dmx-error">Duration must be 1-{maxMinutes} min</span>}
      </div>
      <div className="dmx-row dmx-row--sep">
        <button type="button" className="dmx-btn" onClick={() => send("blackout", { minutes: Number(blackoutMin) })}
          disabled={!(Number(blackoutMin) > 0 && Number(blackoutMin) <= maxMinutes)}>Blackout (visible off)</button>
        <label className="dmx-field dmx-field--inline">for<input type="number" min="1" max={maxMinutes} value={blackoutMin} onChange={(e) => setBlackoutMin(e.target.value)} />min</label>
        <span className="dmx-muted">IR stays on its schedule.</span>
      </div>
    </div>
  );
}

export default function LightingStatus({ moduleId, boxes }) {
  const { state, send, lastResult, loggedIn } = useLightingState(moduleId);
  useTick();
  const overrides = state?.overrides ?? [];
  const levelRows = Object.entries(state?.boxes ?? {});

  return (
    <div className="dmx-status">
      <LightingSummary state={state} />
      <LightingProblems state={state} />
      {lastResult?.error && <div className="dmx-error">{lastResult.command}: {lastResult.error}</div>}

      <h4>Active overrides</h4>
      {overrides.length === 0 ? <p className="dmx-muted">None: every strip follows the schedule.</p> : (
        <ul className="dmx-overrides">
          {overrides.map((ov) => (
            <li key={ov.id}>
              <span className={`dmx-mode dmx-mode--${ov.kind}`}>{PHASE_LABEL[ov.kind] ?? ov.kind}</span>
              <span>
                {ov.kind === "test" ? `${ov.boxes?.[0]} ${ov.role} flashing`
                  : `${ov.boxes ? ov.boxes.join(", ") : "all"}: ${[
                    ov.visible != null ? `visible ${ov.visible}%` : null,
                    ov.ir != null ? `IR ${ov.ir}%` : null].filter(Boolean).join(", ")}`}
                {ov.note && ` (${ov.note})`}
              </span>
              <span className="dmx-muted">{fmtDuration(remainingS(ov))} left</span>
              {loggedIn && (
                <button type="button" className="dmx-btn dmx-btn--small" onClick={() => send("clear_override", { override_id: ov.id })}>
                  End now
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {loggedIn
        ? <OverrideForm boxes={boxes} maxMinutes={state?.max_override_minutes ?? 240} send={send} />
        : <p className="dmx-muted">Sign in as admin to override the lights.</p>}

      <h4>Levels now</h4>
      {levelRows.length === 0 ? <p className="dmx-muted">No compartments patched.</p> : (
        <table className="dmx-table">
          <thead><tr><th>Box</th><th>Visible</th><th>IR</th><th>Driven by</th></tr></thead>
          <tbody>
            {levelRows.map(([box, lv]) => (
              <tr key={box}>
                <td>{box}</td>
                <td><span className="dmx-bar"><i style={{ width: `${lv.visible}%` }} /></span>{lv.visible.toFixed(0)}%</td>
                <td><span className="dmx-bar dmx-bar--ir"><i style={{ width: `${lv.ir}%` }} /></span>{lv.ir.toFixed(0)}%</td>
                <td className="dmx-muted">
                  {lv.source_visible === lv.source_ir ? SOURCE_LABEL[lv.source_visible]
                    : `${SOURCE_LABEL[lv.source_visible]} / ${SOURCE_LABEL[lv.source_ir]}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Widget</h4>
      {state?.widget?.connected ? (
        <dl className="dmx-facts">
          <dt>Port</dt><dd>{state.widget.port}</dd>
          <dt>Serial</dt><dd>{state.widget.serial}</dd>
          <dt>Firmware</dt><dd>{state.widget.firmware}</dd>
          <dt>DMX refresh</dt><dd>{state.widget.refresh_hz} Hz</dd>
          <dt>Clock</dt><dd>{state.clock?.source ?? "–"}</dd>
        </dl>
      ) : <p className="dmx-muted">{state?.widget?.error ?? "Not connected."}</p>}
    </div>
  );
}
