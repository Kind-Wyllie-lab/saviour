import React, { useEffect } from "react";
import { Routes, Route } from "react-router-dom";
import "./App.css";
import { usePrefersDarkMode } from "/src/hooks/usePrefersDarkMode";


import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Recording from "/src/basic/pages/Recording/Recording";
import Settings from "/src/basic/pages/Settings/Settings";
import System from "/src/basic/pages/System/System";
import Guide from "/src/basic/pages/Guide/Guide";

import Dashboard from "/src/acoustic_startle/pages/AcousticStartleDashboard/AcousticStartleDashboard";
import ConnectionOverlay from "/src/basic/components/ConnectionOverlay/ConnectionOverlay";

document.title="Acoustic Startle";


const pages = [
    { label: "Dashboard", path: "/" },
    { label: "Settings", path: "/settings" },
    { label: "Recording", path: "/recording" },
    { label: "System", path: "/system" },
    { label: "Guide", path: "/guide" },
];


function App() {
    const darkMode = usePrefersDarkMode();

    useEffect(() => {
        document.body.classList.toggle("dark-mode", darkMode);
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
                <Route path="/guide" element={<Guide />} />
            </Routes>
            </div>
        </div>
      <ConnectionOverlay />
    )
}

export default App;