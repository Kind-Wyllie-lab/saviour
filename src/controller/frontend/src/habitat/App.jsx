// import logo from './logo.svg';
import './App.css';
import React,  { useEffect, useState } from "react";

import HabitatSidebar from "./components/HabitatSidebar/HabitatSidebar";
import { Routes, Route } from "react-router-dom";
import HabitatDashboard from "./pages/HabitatDashboard/HabitatDashboard";
import Settings from "./pages/Settings/Settings";
import Debug from "./pages/Debug/Debug";
import Monitor from "./pages/Monitor/Monitor";
import Recording from './pages/Recording/Recording';

document.title="Habitat GUI";

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
      <HabitatSidebar />
      <div className="content">
        <Routes>
          <Route path="/" element={<HabitatDashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/debug" element={<Debug />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
