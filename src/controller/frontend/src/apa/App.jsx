import '../basic/App.css';
import React, { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";

import Sidebar from "../basic/components/Sidebar/Sidebar";
import APADashboard from "./pages/APADashboard/APADashboard";
import Recording from "../basic/pages/Recording/Recording";
import Settings from "../basic/pages/Settings/Settings";
import System from "../basic/pages/System/System";
import Debug from "../basic/pages/Debug/Debug";

document.title = "APA";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Recording", path: "/recording" },
  { label: "Settings", path: "/settings" },
  { label: "System", path: "/system" },
];

function App() {
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDarkMode(prefersDark);
    const listener = (e) => setDarkMode(e.matches);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", listener);
    return () => {
      window.matchMedia("(prefers-color-scheme: dark)").removeEventListener("change", listener);
    };
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
          <Route path="/" element={<APADashboard />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/system" element={<System />} />
          <Route path="/debug" element={<Debug />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
