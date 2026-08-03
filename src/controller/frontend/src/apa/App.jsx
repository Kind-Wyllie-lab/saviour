import '../basic/App.css';
import React from "react";
import { Routes, Route } from "react-router";

import Sidebar from "../basic/components/Sidebar/Sidebar";
import APADashboard from "./pages/APADashboard/APADashboard";
import Recording from "../basic/pages/Recording/Recording";
import Settings from "../basic/pages/Settings/Settings";
import System from "../basic/pages/System/System";
import Debug from "../basic/pages/Debug/Debug";
import Guide from "../basic/pages/Guide/Guide";
import ConnectionOverlay from "../basic/components/ConnectionOverlay/ConnectionOverlay";

document.title = "APA";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Recording", path: "/recording" },
  { label: "Settings", path: "/settings" },
  { label: "System", path: "/system" },
  { label: "Guide", path: "/guide" },
];

function App() {
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
          <Route path="/guide" element={<Guide />} />
        </Routes>
      </div>
      <ConnectionOverlay />
    </div>
  );
}

export default App;
