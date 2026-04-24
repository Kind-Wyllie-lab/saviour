// import logo from './logo.svg';
import './App.css';
import React,  { useEffect, useState } from "react";

// SAVIOUR Imports
import Sidebar from "./components/Sidebar/Sidebar";
import { Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard/Dashboard";
import Settings from "./pages/Settings/Settings";
import Recording from "./pages/Recording/Recording";
import Debug from "./pages/Debug/Debug";
import System from "./pages/System/System";
import ClockModal from "./components/ClockModal/ClockModal";
import useClockOnce from "/src/hooks/useClockOnce";


document.title="SAVIOUR";


const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings", path: "/settings" },
  { label: "Recording", path: "/recording" },
  { label: "System", path: "/system" },
];


const CLOCK_DRIFT_THRESHOLD_MS = 2 * 60 * 1000; // 2 minutes

function App() {
  const [darkMode, setDarkMode] = useState(false);
  const clockInfo = useClockOnce();
  const [showClockModal, setShowClockModal] = useState(false);

  useEffect(() => {
    if (clockInfo == null) return;
    if (
      Math.abs(clockInfo.driftMs) > CLOCK_DRIFT_THRESHOLD_MS &&
      !sessionStorage.getItem("saviour_clock_check")
    ) {
      sessionStorage.setItem("saviour_clock_check", "1");
      setShowClockModal(true);
    }
  }, [clockInfo]);

  useEffect(() => {
    // Check if user prefers dark mode
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDarkMode(prefersDark);

    // Optional: listen for changes in system preference
    const listener = (e) => setDarkMode(e.matches);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", listener);

    return () => {
      window.matchMedia("(prefers-color-scheme: dark)").removeEventListener("change", listener);
    };
  }, []);

  useEffect(() => {
    // Apply the theme by toggling a class on body
    if (darkMode) document.body.classList.add("dark-mode");
    else document.body.classList.remove("dark-mode");
  }, [darkMode]);

  return (
    <div className="app">
      <Sidebar navItems={pages} />
      <div className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/system" element={<System />} />
        </Routes>
      </div>
      {showClockModal && (
        <ClockModal
          driftMs={clockInfo?.driftMs}
          controllerTime={clockInfo?.controllerTime}
          onClose={() => setShowClockModal(false)}
        />
      )}
    </div>
  );
}

export default App;
