// React imports
import React, { useEffect, useState } from "react";
import socket from "../../../socket";
import { NavLink } from "react-router-dom";

// Style imports
import './HabitatSidebar.css';
import UoELogo from './uofe_logo_alpha.png';
import SIDBLogo from './sidb_logo_alpha.png';

function HabitatSidebar() {
    return (
        <header className="sidebar">
            <div className="header-content">
                <div className="logo-container">
                    <img src={UoELogo} alt="UoE Logo" className="logo" />
                    <img src={SIDBLogo} alt="SIDB Logo" className="logo" />
                </div>
                <h1 className="sidebar-title">Habitat</h1>
                <nav className="main-nav">
                    <NavLink to="/" className="nav-link">Dashboard</NavLink>
                    <NavLink to="/settings" className="nav-link">Settings</NavLink>
                    <NavLink to="/debug" className="nav-link">Debug</NavLink>
                </nav>
            </div>
            <div className="footer">
                <p>© SIDB 2026</p>
                <a href="https://github.com/Kind-Wyllie-lab/saviour" target="_blank">GitHub Page</a>
            </div>
        </header>
    );
}

export default HabitatSidebar;