import React, { useState } from "react";
import "./HabitatDashboard.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import HabitatLiveMap from "../../components/HabitatLiveMap/HabitatLiveMap";
import HabitatModuleStatusList from "../../components/HabitatModuleStatusList/HabitatModuleStatusList";
import PlacementPanel from "../../components/PlacementPanel/PlacementPanel";
import HabitatLightingWidget from "../../components/HabitatLightingWidget/HabitatLightingWidget";

function HabitatDashboard() {
  const { modules } = useModules();
  const { sessionList } = useSessions();
  // Placement panel (which camera / AudioMoth is in which box) takes the
  // left column while open, so the map stays in view to check against.
  const [placing, setPlacing] = useState(false);

  return (
    <main className="habitat-dashboard">
      <div className="habitat-dashboard-body">
        <section className={`habitat-dashboard-left${placing ? " habitat-dashboard-left--placing" : ""}`}>
          {placing ? (
            <PlacementPanel modules={modules} onClose={() => setPlacing(false)} />
          ) : (
            <>
              <HealthSummaryWidget />
              <HabitatLightingWidget modules={modules} />
              <HabitatModuleStatusList modules={modules} sessions={sessionList} />
            </>
          )}
        </section>

        {/* Cameras, gate reads and AudioMoth levels on one map; the full
            camera grid and spectrograms are on the Monitor page. */}
        <section className="habitat-dashboard-center">
          <HabitatLiveMap modules={modules} placing={placing}
                          onPlacement={() => setPlacing((p) => !p)} />
        </section>
      </div>
    </main>
  );
}

export default HabitatDashboard;
