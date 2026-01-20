// import logo from './logo.svg';
import './App.css';
import React,  { useEffect, useState } from "react";

import HabitatHeader from "./components/HabitatHeader/HabitatHeader";
import { Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard/Dashboard";
import Settings from "./pages/Settings/Settings";
import Debug from "./pages/Debug/Debug";

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
    <div>
      <HabitatHeader />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/debug" element={<Debug />} />
      </Routes>
    </div>
  );
}

export default App;
