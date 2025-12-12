// React imports
import React, { useEffect, useState } from "react";
import socket from "../../../socket";
import { Link } from "react-router-dom";

// Style imports
import './Header.css';
import UoELogo from './uofe_logo_alpha.png';
import SIDBLogo from './sidb_logo_alpha.png';

function APAHeader() {
    return (
        <header className="main-header">
            <div className="header-content">
                <div className="logo-container">
                    <img src={UoELogo} alt="UoE Logo" className="logo" />
                    <img src={SIDBLogo} alt="SIDB Logo" className="logo" />
                    <h1>SAVIOUR</h1>
                </div>
                <nav className="main-nav">
                    <ul>
                        <li><Link to="/" className="nav-link">Dashboard</Link></li>
                        {/* <li><Link to="/recordings" className="nav-link">Recordings</Link></li> */}
                        <li><Link to="/settings" className="nav-link">Settings</Link></li>
                        <li><Link to="/debug" className="nav-link">Debug</Link></li>
                    </ul>
                </nav>
            </div>
        </header>
    );
}

export default APAHeader;