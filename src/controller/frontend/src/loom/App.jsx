import React, { useEffect, useState } from "react";
import { Routes, Route, useLocation } from "react-router";
import "./App.css";

import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Dashboard from "/src/loom/pages/LoomDashboard/LoomDashboard";
import Settings from "/src/basic/pages/Settings/Settings";
import LoomRecording from "/src/loom/pages/LoomRecording/LoomRecording";
import System from "/src/basic/pages/System/System";
import Storage from "/src/basic/pages/Storage/Storage";
import Guide from "/src/basic/pages/Guide/Guide";
import FaultAlertModal from "/src/basic/components/FaultAlertModal/FaultAlertModal";
import FirstRunModal from "/src/basic/components/FirstRunModal/FirstRunModal";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import useSessions from "/src/hooks/useSessions";
import { LoomStageProvider } from "/src/loom/LoomStageContext";

document.title = "Loom";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings",  path: "/settings" },
  { label: "Recording", path: "/recording" },
  { label: "System",    path: "/system" },
  { label: "Storage",    path: "/storage" },
  { label: "Guide",     path: "/guide" },
];

function faultKey(session) {
  return `saviour_fault_ack::${session.session_name}::${session.error_time ?? "unknown"}`;
}

function App() {
  const { sessionList } = useSessions();
  const [pendingFaults, setPendingFaults] = useState([]);
  const location = useLocation();

  useEffect(() => {
    const unacked = sessionList.filter(
      (s) => s.error_time && !sessionStorage.getItem(faultKey(s))
    );
    setPendingFaults(unacked);
  }, [sessionList]);

  // Dismiss on navigation
  const prevPath = React.useRef(location.pathname);
  useEffect(() => {
    if (location.pathname === prevPath.current) return;
    prevPath.current = location.pathname;
    setPendingFaults((faults) => {
      faults.forEach((s) => sessionStorage.setItem(faultKey(s), "1"));
      return [];
    });
  }, [location.pathname]);

  const handleAcknowledge = () => {
    pendingFaults.forEach((s) => sessionStorage.setItem(faultKey(s), "1"));
    setPendingFaults([]);
  };

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
              <Route path="/guide"     element={<Guide />} />
            </Routes>
          </div>
        </div>

        {pendingFaults.length > 0 && (
          <FaultAlertModal
            faultedSessions={pendingFaults}
            onAcknowledge={handleAcknowledge}
          />
        )}
        <FirstRunModal />
        <ConnectionOverlay />
      </div>
    </LoomStageProvider>
  );
}

export default App;
