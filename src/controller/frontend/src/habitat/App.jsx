import './App.css';
import React, { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";

import Sidebar from '../basic/components/Sidebar/Sidebar';
import Settings from "../basic/pages/Settings/Settings";
import System from "../basic/pages/System/System";
import Recording from '../basic/pages/Recording/Recording';

import HabitatDashboard from "./pages/HabitatDashboard/HabitatDashboard";
import Monitor from "./pages/Monitor/Monitor";
import FaultAlertModal from "/src/basic/components/FaultAlertModal/FaultAlertModal";
import useSessions from "/src/hooks/useSessions";


document.title="Habitat";


const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings", path: "/settings" },
  { label: "Monitor", path: "/monitor" },
  { label: "Recording", path: "/recording" },
  { label: "System", path: "/system" },
];

// Key used to track which faults have been acknowledged this browser session.
// Keyed by "session_name:error_time" so a new fault on the same session re-alerts.
function faultKey(session) {
  return `saviour_fault_ack::${session.session_name}::${session.error_time ?? "unknown"}`;
}

function App() {
  const [darkMode, setDarkMode] = useState(false);
  const { sessionList } = useSessions();
  const [pendingFaults, setPendingFaults] = useState([]);

  // Detect unacknowledged faults whenever the session list changes
  useEffect(() => {
    const unacked = sessionList.filter(
      (s) => s.error_time && !sessionStorage.getItem(faultKey(s))
    );
    setPendingFaults(unacked);
  }, [sessionList]);

  const handleAcknowledge = () => {
    pendingFaults.forEach((s) => sessionStorage.setItem(faultKey(s), "1"));
    setPendingFaults([]);
  };

  useEffect(() => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDarkMode(prefersDark);
    const listener = (e) => setDarkMode(e.matches);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", listener);
    return () => window.matchMedia("(prefers-color-scheme: dark)").removeEventListener("change", listener);
  }, []);

  useEffect(() => {
    if (darkMode) document.body.classList.add("dark-mode");
    else document.body.classList.remove("dark-mode");
  }, [darkMode]);

  return (
    <div className="app">
      <Sidebar navItems={pages} />
      <div className="content">
        <Routes>
          <Route path="/" element={<HabitatDashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/system" element={<System />} />
        </Routes>
      </div>

      {pendingFaults.length > 0 && (
        <FaultAlertModal
          faultedSessions={pendingFaults}
          onAcknowledge={handleAcknowledge}
        />
      )}
    </div>
  );
}

export default App;
