import React from "react";
import { NavLink } from "react-router-dom";

import "./Sidebar.css";
import UoELogo from "/src/assets/logos/uofe_logo_alpha.png";
import SIDBLogo from "/src/assets/logos/sidb_logo_alpha.png";

function Sidebar({ navItems }) {
  return (
    <header className="sidebar">
      <div className="header-content">
        <div className="logo-container">
          <img src={UoELogo} alt="UoE Logo" className="logo" />
          <img src={SIDBLogo} alt="SIDB Logo" className="logo" />
        </div>

        <h1 className="sidebar-title">{document.title}</h1>

        <nav className="main-nav">
          {navItems.map(({ label, path, disabled }) =>
            disabled ? (
              <span key={path} className="nav-link disabled">
                {label}
              </span>
            ) : (
              <NavLink key={path} to={path} className="nav-link">
                {label}
              </NavLink>
            )
          )}
        </nav>
      </div>

      <div className="footer">
        <p>© SIDB 2026</p>
        <a
          href="https://github.com/Kind-Wyllie-lab/saviour"
          target="_blank"
          rel="noopener noreferrer"
        >
          GitHub Page
        </a>
      </div>
    </header>
  );
}

export default Sidebar;
