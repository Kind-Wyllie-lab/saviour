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

const STREAM_KEY = "apa_dashboard_stream";

const readSaved = () => {
  try { return localStorage.getItem(STREAM_KEY); } catch { return null; }
};

/**
 * The camera on the dashboard: the operator's pick (remembered per browser)
 * while it's online, else the APA camera, else the first online camera.
 * One stream at a time, so switching doesn't add load on the Pis.
 */
function useStreamChoice(moduleList) {
  const [savedId, setSavedId] = useState(readSaved);
  const cameras = moduleList.filter(
    (m) => m.type?.includes("camera") && m.online !== false && m.status !== "OFFLINE");
  const apaCamera = cameras.find((m) => m.type === "apa_camera") ?? null;
  const chosen = cameras.find((m) => m.id === savedId) ?? apaCamera ?? cameras[0] ?? null;
  const select = (id) => {
    setSavedId(id);
    try { localStorage.setItem(STREAM_KEY, id); } catch { /* per-browser nicety only */ }
  };
  return { cameras, chosen, select, apaPresent: !!apaCamera };
}

function LivestreamOrTemplate({ moduleList }) {
  const { cameras, chosen, select, apaPresent } = useStreamChoice(moduleList);
  if (chosen) {
    return (
      <APALivestreamCard
        module={chosen} cameras={cameras} onSelect={select}
        apaPresent={apaPresent} moduleList={moduleList}
      />
    );
  }
  return (
    <div className="apa-camera-template">
      <p>APA camera not connected</p>
      <p className="apa-camera-template__sub">No other camera is online either.</p>
    </div>
  );
}

function APADashboard() {
  const { moduleList } = useModules();
  const narrow = useNarrow();

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
          <LivestreamOrTemplate moduleList={moduleList} />
          <div className="dashboard-compact-panel">{panel}</div>
        </div>
      ) : (
        /* ── Stream left, panel right ── */
        <div className="dashboard-main">
          <div className="apa-livestream-col">
            <LivestreamOrTemplate moduleList={moduleList} />
          </div>
          <div className="apa-dashboard-panel">{panel}</div>
        </div>
      )}
    </div>
  );
}

export default APADashboard;
