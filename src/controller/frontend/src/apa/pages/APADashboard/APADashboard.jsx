import React, { useState, useEffect } from "react";
import "/src/basic/pages/Dashboard/Dashboard.css";
import "./APADashboard.css";

import useModules from "/src/hooks/useModules";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import APALivestreamCard from "../../components/APALivestreamCard/APALivestreamCard";
import APACommands from "../../components/APACommands/APACommands";

const COMPACT_BREAKPOINT = 1440; // 3-col → 2-col
const NARROW_BREAKPOINT  = 768;  // 2-col → stacked

function useLayoutMode() {
  const getMode = () => {
    if (window.innerWidth <= NARROW_BREAKPOINT)  return "narrow";
    if (window.innerWidth <= COMPACT_BREAKPOINT) return "compact";
    return "wide";
  };
  const [mode, setMode] = useState(getMode);
  useEffect(() => {
    const narrowMq  = window.matchMedia(`(max-width: ${NARROW_BREAKPOINT}px)`);
    const compactMq = window.matchMedia(`(max-width: ${COMPACT_BREAKPOINT}px)`);
    const handler = () => setMode(getMode());
    narrowMq.addEventListener("change", handler);
    compactMq.addEventListener("change", handler);
    return () => {
      narrowMq.removeEventListener("change", handler);
      compactMq.removeEventListener("change", handler);
    };
  }, []);
  return mode;
}

function LivestreamOrTemplate({ module, moduleList }) {
  if (module) return <APALivestreamCard module={module} moduleList={moduleList} />;
  return (
    <div className="apa-camera-template">
      <p>APA camera not connected</p>
    </div>
  );
}

function APADashboard() {
  const { moduleList } = useModules();
  const mode = useLayoutMode();
  const apaCamera = moduleList.find((m) => m.type === "apa_camera") ?? null;

  return (
    <div className="dashboard">
      <RecordingStatusWidget />

      {mode === "narrow" ? (
        /* ── Narrow (<768px): single column, stream gets full width ── */
        <div className="dashboard-compact">
          <LivestreamOrTemplate module={apaCamera} moduleList={moduleList} />
          <div className="dashboard-compact-panel">
            <APACommands modules={moduleList} />
            <HealthSummaryWidget />
            <ModuleList modules={moduleList} />
          </div>
        </div>

      ) : mode === "compact" ? (
        /* ── Compact (768–1280px): stream left, commands+panel right ── */
        <div className="dashboard-main">
          <div className="apa-livestream-col">
            <LivestreamOrTemplate module={apaCamera} moduleList={moduleList} />
          </div>
          <div className="dashboard-panel">
            <APACommands modules={moduleList} />
            <HealthSummaryWidget />
            <ModuleList modules={moduleList} />
          </div>
        </div>

      ) : (
        /* ── Wide (≥1280px): three columns ── */
        <div className="dashboard-main">
          <div className="apa-livestream-col">
            <LivestreamOrTemplate module={apaCamera} moduleList={moduleList} />
          </div>
          <APACommands modules={moduleList} />
          <div className="dashboard-panel">
            <HealthSummaryWidget />
            <ModuleList modules={moduleList} />
          </div>
        </div>
      )}
    </div>
  );
}

export default APADashboard;
