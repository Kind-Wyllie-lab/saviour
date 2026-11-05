import React from "react";
import "./HabitatDashboard.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import HabitatLiveMap from "../../components/HabitatLiveMap/HabitatLiveMap";
import HabitatModuleStatusList from "../../components/HabitatModuleStatusList/HabitatModuleStatusList";

function HabitatDashboard() {
  const { modules } = useModules();
  const { sessionList } = useSessions();

  return (
    <main className="habitat-dashboard">
      <div className="habitat-dashboard-body">
        <section className="habitat-dashboard-left">
          <HealthSummaryWidget />
          <HabitatModuleStatusList modules={modules} sessions={sessionList} />
        </section>

        {/* Cameras, gate reads and AudioMoth levels on one map; the full
            camera grid and spectrograms are on the Monitor page. */}
        <section className="habitat-dashboard-center">
          <HabitatLiveMap modules={modules} />
        </section>
      </div>
    </main>
  );
}

export default HabitatDashboard;
