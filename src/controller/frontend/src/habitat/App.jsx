import './App.css';
import React from "react";
import { Routes, Route } from "react-router";

import Sidebar from '../basic/components/Sidebar/Sidebar';
import Settings from "../basic/pages/Settings/Settings";
import System from "../basic/pages/System/System";
import Storage from "../basic/pages/Storage/Storage";
import PostProcess from "../basic/pages/PostProcess/PostProcess";
import Recording from '../basic/pages/Recording/Recording';
import Guide from '../basic/pages/Guide/Guide';

import HabitatDashboard from "./pages/HabitatDashboard/HabitatDashboard";
import Monitor from "./pages/Monitor/Monitor";
import HabitatRecordingControl from "./components/HabitatRecordingControl/HabitatRecordingControl";
import FaultAlertModal from "/src/basic/components/FaultAlertModal/FaultAlertModal";
import FirstRunModal from "/src/basic/components/FirstRunModal/FirstRunModal";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";
import useFaultAlerts from "/src/hooks/useFaultAlerts";


document.title="Habitat";


const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings", path: "/settings" },
  { label: "Monitor", path: "/monitor" },
  { label: "Recording", path: "/recording" },
  { label: "System", path: "/system" },
  { label: "Storage", path: "/storage" },
    { label: "Post-Process", path: "/post-process" },
  { label: "Guide", path: "/guide" },
];

function App() {
  const { pendingFaults, acknowledge } = useFaultAlerts();

  return (
    <div className="app">
      <Sidebar navItems={pages} />
      <div className="content">
        <HabitatRecordingControl />
        <div className="content-scroll">
          <Routes>
            <Route path="/" element={<HabitatDashboard />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/monitor" element={<Monitor />} />
            <Route path="/recording/*" element={<Recording />} />
            <Route path="/system" element={<System />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/post-process" element={<PostProcess />} />
            <Route path="/guide" element={<Guide />} />
          </Routes>
        </div>
      </div>

      <FirstRunModal />
      <ConnectionOverlay />
      {pendingFaults.length > 0 && (
        <FaultAlertModal
          faultedSessions={pendingFaults}
          onAcknowledge={acknowledge}
        />
      )}
    </div>
  );
}

export default App;
