import React, { useState, useEffect } from "react";
import "/src/basic/pages/Dashboard/Dashboard.css";
import "./APADashboard.css";

import useModules from "/src/hooks/useModules";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import APALivestreamCard from "../../components/APALivestreamCard/APALivestreamCard";
import APACommands from "../../components/APACommands/APACommands";

const NARROW_BREAKPOINT = 768; // side panel -> stacked

function useNarrow() {
  const query = `(max-width: ${NARROW_BREAKPOINT}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const handler = () => setNarrow(mq.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);
  return narrow;
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
  const narrow = useNarrow();
  const apaCamera = moduleList.find((m) => m.type === "apa_camera") ?? null;

  // Health and modules first, the rig's commands below them; one side panel
  // at every width above narrow (a third column pushed the panel off-screen).
  const panel = (
    <>
      <HealthSummaryWidget />
      <ModuleList modules={moduleList} />
      <APACommands modules={moduleList} />
    </>
  );

  return (
    <div className="dashboard">
      {narrow ? (
        /* ── Narrow (<768px): single column, stream gets full width ── */
        <div className="dashboard-compact">
          <LivestreamOrTemplate module={apaCamera} moduleList={moduleList} />
          <div className="dashboard-compact-panel">{panel}</div>
        </div>
      ) : (
        /* ── Stream left, panel right ── */
        <div className="dashboard-main">
          <div className="apa-livestream-col">
            <LivestreamOrTemplate module={apaCamera} moduleList={moduleList} />
          </div>
          <div className="apa-dashboard-panel">{panel}</div>
        </div>
      )}
    </div>
  );
}

export default APADashboard;
