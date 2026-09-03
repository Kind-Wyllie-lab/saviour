import '../basic/App.css';
import React from "react";
import { Routes, Route } from "react-router";

import Sidebar from "../basic/components/Sidebar/Sidebar";
import APADashboard from "./pages/APADashboard/APADashboard";
import Recording from "../basic/pages/Recording/Recording";
import Settings from "../basic/pages/Settings/Settings";
import System from "../basic/pages/System/System";
import Storage from "../basic/pages/Storage/Storage";
import PostProcess from "../basic/pages/PostProcess/PostProcess";
import Debug from "../basic/pages/Debug/Debug";
import Guide from "../basic/pages/Guide/Guide";
import FirstRunModal from "../basic/components/FirstRunModal/FirstRunModal";
import ConnectionOverlay from "../basic/components/ConnectionOverlay/ConnectionOverlay";
import RecordingStatusWidget from "../basic/components/RecordingStatusWidget/RecordingStatusWidget";

document.title = "APA";

const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Recording", path: "/recording" },
  { label: "Settings", path: "/settings" },
  { label: "System", path: "/system" },
  { label: "Storage", path: "/storage" },
    { label: "Post-Process", path: "/post-process" },
  { label: "Guide", path: "/guide" },
];

function App() {
  return (
    <div className="app">
      <Sidebar navItems={pages} />
      <div className="content">
        <RecordingStatusWidget />
        <div className="content-scroll">
          <Routes>
            <Route path="/" element={<APADashboard />} />
            <Route path="/recording/*" element={<Recording />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/system" element={<System />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/post-process" element={<PostProcess />} />
            <Route path="/debug" element={<Debug />} />
            <Route path="/guide" element={<Guide />} />
          </Routes>
        </div>
      </div>
      <FirstRunModal />
      <ConnectionOverlay />
    </div>
  );
}

export default App;
