import React from "react";
import "/src/basic/pages/Dashboard/Dashboard.css";
import "./APADashboard.css";

import useModules from "/src/hooks/useModules";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import APALivestreamCard from "../../components/APALivestreamCard/APALivestreamCard";
import APACommands from "../../components/APACommands/APACommands";

function APADashboard() {
  const { moduleList } = useModules();
  const apaCameraModules = moduleList.filter((m) => m.type === "apa_camera");

  return (
    <div className="dashboard">
      <RecordingStatusWidget />

      <div className="dashboard-main">
        <div className="dashboard-cameras">
          {apaCameraModules.length > 0 ? (
            <APALivestreamCard
              key={apaCameraModules[0].id}
              module={apaCameraModules[0]}
              moduleList={moduleList}
            />
          ) : (
            <div className="apa-camera-template">
              <p>APA camera not connected</p>
            </div>
          )}
          <APACommands modules={moduleList} />
        </div>

        <div className="dashboard-panel">
          <HealthSummaryWidget />
          <ModuleList modules={moduleList} />
        </div>
      </div>
    </div>
  );
}

export default APADashboard;
