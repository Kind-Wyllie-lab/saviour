import React from "react";
import { Routes, Route } from "react-router";
import "./App.css";


import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Recording from "/src/basic/pages/Recording/Recording";
import Settings from "/src/basic/pages/Settings/Settings";
import System from "/src/basic/pages/System/System";
import Storage from "/src/basic/pages/Storage/Storage";
import PostProcess from "/src/basic/pages/PostProcess/PostProcess";
import Guide from "/src/basic/pages/Guide/Guide";

import Dashboard from "/src/acoustic_startle/pages/AcousticStartleDashboard/AcousticStartleDashboard";
import FirstRunModal from "/src/basic/components/FirstRunModal/FirstRunModal";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import RigCheckBanner from "/src/basic/components/RigCheck/RigCheckBanner";
import Protocols from "/src/basic/pages/Protocols/Protocols";

document.title="Acoustic Startle";


const pages = [
    { label: "Dashboard", path: "/" },
    { label: "Settings", path: "/settings" },
    { label: "Protocols", path: "/protocols" },
    { label: "Recording", path: "/recording" },
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
            <RigCheckBanner />
            <div className="content-scroll">
            <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/recording/*" element={<Recording />} />
                <Route path="/protocols" element={<Protocols />} />
                <Route path="/system" element={<System />} />
                <Route path="/storage" element={<Storage />} />
            <Route path="/post-process" element={<PostProcess />} />
                <Route path="/guide" element={<Guide />} />
            </Routes>
            </div>
            </div>
            <FirstRunModal />
            <ConnectionOverlay />
        </div>
    )
}

export default App;