import React, { useEffect } from "react";
import { Routes, Route, useLocation } from "react-router";
import "./App.css";

import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Dashboard from "/src/loom/pages/LoomDashboard/LoomDashboard";
import Settings from "/src/basic/pages/Settings/Settings";
import LoomRecording from "/src/loom/pages/LoomRecording/LoomRecording";
import System from "/src/basic/pages/System/System";
import Storage from "/src/basic/pages/Storage/Storage";
import PostProcess from "/src/basic/pages/PostProcess/PostProcess";
import Guide from "/src/basic/pages/Guide/Guide";
import FaultAlertModal from "/src/basic/components/FaultAlertModal/FaultAlertModal";
import FirstRunModal from "/src/basic/components/FirstRunModal/FirstRunModal";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import useFaultAlerts from "/src/hooks/useFaultAlerts";
import { LoomStageProvider } from "/src/loom/LoomStageContext";

document.title = "Loom";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings",  path: "/settings" },
  { label: "Recording", path: "/recording" },
  { label: "System",    path: "/system" },
  { label: "Storage",    path: "/storage" },
    { label: "Post-Process",    path: "/post-process" },
  { label: "Guide",     path: "/guide" },
];

function App() {
  const { pendingFaults, acknowledge } = useFaultAlerts();
  const location = useLocation();

  // Dismiss on navigation
  const prevPath = React.useRef(location.pathname);
  useEffect(() => {
    if (location.pathname === prevPath.current) return;
    prevPath.current = location.pathname;
    acknowledge();
  }, [location.pathname, acknowledge]);

  return (
    <LoomStageProvider>
      <div className="app">
        <Sidebar navItems={pages} />
        <div className="content">
          <RecordingStatusWidget />
          <div className="content-scroll">
            <Routes>
              <Route path="/"          element={<Dashboard />} />
              <Route path="/settings"  element={<Settings />} />
              <Route path="/recording" element={<LoomRecording />} />
              <Route path="/system"    element={<System />} />
              <Route path="/storage"    element={<Storage />} />
            <Route path="/post-process"    element={<PostProcess />} />
              <Route path="/guide"     element={<Guide />} />
            </Routes>
          </div>
        </div>

        {pendingFaults.length > 0 && (
          <FaultAlertModal
            faultedSessions={pendingFaults}
            onAcknowledge={acknowledge}
          />
        )}
        <FirstRunModal />
        <ConnectionOverlay />
      </div>
    </LoomStageProvider>
  );
}

export default App;
