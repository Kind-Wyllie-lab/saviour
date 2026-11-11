import React, { useEffect, useState } from "react";
import { Link } from "react-router";
import useLightingState, { remainingS, fmtDuration } from "/src/hooks/useLightingState";
import { LightingSummary, LightingProblems } from "/src/basic/components/ConfigCard/DmxLightingConfigCard/LightingStatus";
import { PHASE_LABEL } from "/src/basic/components/ConfigCard/DmxLightingConfigCard/lightingSchedule";
import "/src/basic/components/ConfigCard/DmxLightingConfigCard/DmxLightingConfigCard.css";
import "./HabitatLightingWidget.css";

// Habitat dashboard: the lighting module's phase, next change and any
// override, with quick husbandry actions (admin). Full controls are on the
// module's Settings card (Status tab). Nothing shown without a dmx_lighting
// module.
function LightingPanel({ module }) {
  const { state, send, lastResult, loggedIn } = useLightingState(module.id);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const overrides = state?.overrides ?? [];
  const max = state?.max_override_minutes ?? 240;
  const quick = Math.min(30, max);

  return (
    <div className="habitat-lighting card">
      <div className="habitat-lighting__head">
        <h3>Lighting</h3>
        <Link className="habitat-lighting__link" to={`/settings#${module.id}/status`}>Settings</Link>
      </div>
      {!module.online && <p className="dmx-error">{module.name || module.id} is offline (lights hold their last level).</p>}
      <LightingSummary state={state} />
      <LightingProblems state={state} />
      {overrides.map((ov) => (
        <div key={ov.id} className="habitat-lighting__ov">
          <span className={`dmx-mode dmx-mode--${ov.kind}`}>{PHASE_LABEL[ov.kind] ?? ov.kind}</span>
          <span className="dmx-muted">{fmtDuration(remainingS(ov))} left{ov.note ? ` · ${ov.note}` : ""}</span>
          {loggedIn && (
            <button type="button" className="dmx-btn dmx-btn--small"
              onClick={() => send("clear_override", { override_id: ov.id })}>End</button>
          )}
        </div>
      ))}
      {loggedIn && module.online && (
        <div className="habitat-lighting__actions">
          <button type="button" className="dmx-btn dmx-btn--small"
            onClick={() => send("set_override", { visible: 100, minutes: quick, note: "dashboard" })}>
            Lights on {quick} min
          </button>
          <button type="button" className="dmx-btn dmx-btn--small"
            onClick={() => send("blackout", { minutes: quick, note: "dashboard" })}>
            Blackout {quick} min
          </button>
        </div>
      )}
      {lastResult?.error && <p className="dmx-error">{lastResult.error}</p>}
    </div>
  );
}

function HabitatLightingWidget({ modules }) {
  const lighting = Object.values(modules || {}).filter((m) => m.type === "dmx_lighting");
  if (!lighting.length) return null;
  return lighting.map((m) => <LightingPanel key={m.id} module={m} />);
}

export default HabitatLightingWidget;
