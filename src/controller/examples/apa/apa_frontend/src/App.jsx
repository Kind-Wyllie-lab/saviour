// import logo from './logo.svg';
import './App.css';
import React,  { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";

// SAVIOUR Imports
import Settings from "../../../../frontend/src/pages/Settings/Settings"
import Debug from "../../../../frontend/src/pages/Debug/Debug"

// APA Imports
import APAHeader from "./components/APAHeader/APAHeader";
import APADashboard from "./pages/APADashboard/APADashboard";

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
      <APAHeader />
      <Routes>
        <Route path="/" element={<APADashboard />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/debug" element={<Debug />} />
      </Routes>
    </div>
  );
}

export default App;
