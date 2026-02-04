import React, { useEffect } from "react";
import { Routes, Route } from "react-router-dom";
import "./App.css";
import { usePrefersDarkMode } from "/src/hooks/usePrefersDarkMode";


import Sidebar from "/src/basic/components/Sidebar/Sidebar";
import Dashboard from "/src/basic/pages/Dashboard/Dashboard";
import Recording from "/src/basic/pages/Recording/Recording";
import Settings from "/src/basic/pages/Settings/Settings";


document.title="Acoustic Startle";


const pages = [
    { label: "Dashboard", path: "/" },
    { label: "Settings", path: "/settings" },
    { label: "Recording", path: "/recording" },
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
            </Routes>
            </div>
        </div>
    )
}

export default App;