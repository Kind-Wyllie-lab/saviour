import React, { useEffect, useState } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import "./App.css";

import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Dashboard from "/src/loom/pages/LoomDashboard/LoomDashboard";
import Settings from "/src/basic/pages/Settings/Settings";
import LoomRecording from "/src/loom/pages/LoomRecording/LoomRecording";
import System from "/src/basic/pages/System/System";
import FaultAlertModal from "/src/basic/components/FaultAlertModal/FaultAlertModal";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";
import useSessions from "/src/hooks/useSessions";
import { usePrefersDarkMode } from "/src/hooks/usePrefersDarkMode";
import { LoomStageProvider } from "/src/loom/LoomStageContext";

document.title = "Loom";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings",  path: "/settings" },
  { label: "Recording", path: "/recording" },
  { label: "System",    path: "/system" },
];

function faultKey(session) {
  return `saviour_fault_ack::${session.session_name}::${session.error_time ?? "unknown"}`;
}

function App() {
  const darkMode = usePrefersDarkMode();
  const { sessionList } = useSessions();
  const [pendingFaults, setPendingFaults] = useState([]);
  const location = useLocation();

  useEffect(() => {
    document.body.classList.toggle("dark-mode", darkMode);
  }, [darkMode]);

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
          <Routes>
            <Route path="/"          element={<Dashboard />} />
            <Route path="/settings"  element={<Settings />} />
            <Route path="/recording" element={<LoomRecording />} />
            <Route path="/system"    element={<System />} />
          </Routes>
        </div>

        {pendingFaults.length > 0 && (
          <FaultAlertModal
            faultedSessions={pendingFaults}
            onAcknowledge={handleAcknowledge}
          />
        )}
        <ConnectionOverlay />
      </div>
    </LoomStageProvider>
  );
}

export default App;
