// import logo from './logo.svg';
import './App.css';
import React,  { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";

// SAVIOUR Imports
import Sidebar from '../basic/components/Sidebar/Sidebar';
import Settings from "../basic/pages/Settings/Settings";
import Debug from "../basic/pages/Debug/Debug";
import System from "../basic/pages/System/System";
import Recording from '../basic/pages/Recording/Recording';


// Habitat Imports
import HabitatDashboard from "./pages/HabitatDashboard/HabitatDashboard";
import Monitor from "./pages/Monitor/Monitor";


document.title="Habitat";


const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings", path: "/settings" },
  { label: "Monitor", path: "/monitor" },
  { label: "Recording", path: "/recording" },
  { label: "System", path: "/system" },
  // { label: "Debug", path: "/debug", disabled: true }
];


function App() {
  const [darkMode, setDarkMode] = useState(false);

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
          <Route path="/" element={<HabitatDashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/system" element={<System />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
