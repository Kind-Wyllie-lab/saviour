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


document.title="SAVIOUR";


const pages = [
  { label: "Dashboard", path: "/" },
  { label: "Settings", path: "/settings" },
  { label: "Recording", path: "/recording" },
  // { label: "System", path: "/system" },
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
          <Route path="/" element={<System />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/recording" element={<Recording />} />
          {/* <Route path="/system" element={<System />} /> */}
        </Routes>
      </div>
    </div>
  );
}

export default App;
