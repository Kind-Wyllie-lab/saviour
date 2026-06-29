import { useState, useEffect } from "react";
import { NavLink } from "react-router-dom";
import socket from "/src/socket";

import "./Sidebar.css";
import UoELogo from "/src/assets/logos/uofe_logo_alpha.png";
import SIDBLogo from "/src/assets/logos/sidb_logo_alpha.png";

function Sidebar({ navItems }) {
  const [showPowerModal, setShowPowerModal] = useState(false);
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [updateState, setUpdateState] = useState(null); // null | "updating" | "done"
  const [hostname, setHostname] = useState(null);

  useEffect(() => {
    socket.emit("get_controller_info");
    const handler = (data) => { if (data.hostname) setHostname(data.hostname); };
    socket.on("controller_info_response", handler);
    return () => socket.off("controller_info_response", handler);
  }, []);

  const handleRebootAll = () => {
    socket.emit("reboot_saviour");
    setShowPowerModal(false);
  };

  const handleShutdownAll = () => {
    socket.emit("shutdown_saviour");
    setShowPowerModal(false);
  };

  const handleUpdateAll = () => {
    if (updateState === "updating") return;
    setUpdateState("updating");
    setShowUpdateModal(false);
    socket.emit("update_saviour_controller");
    socket.emit("send_command", { module_id: "all", type: "update_saviour", params: {} });
    setTimeout(() => setUpdateState("done"), 30000);
  };

  return (
    <header className="sidebar">
      <div className="header-content">
        <div className="logo-container">
          <img src={UoELogo} alt="UoE Logo" className="logo" />
          <img src={SIDBLogo} alt="SIDB Logo" className="logo" />
        </div>

        <h1 className="sidebar-title">{document.title}</h1>
        {hostname && <p className="sidebar-hostname">{hostname}</p>}

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
        <div className="footer-actions">
          <button
            className="footer-icon-btn footer-icon-btn--update"
            title={updateState === "updating" ? "Updating…" : updateState === "done" ? "Updated" : "Update all modules and controller"}
            onClick={() => setShowUpdateModal(true)}
            disabled={updateState === "updating"}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          </button>
          <button
            className="footer-icon-btn footer-icon-btn--power"
            title="Reboot or shut down all devices"
            onClick={() => setShowPowerModal(true)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
              <line x1="12" y1="2" x2="12" y2="12" />
            </svg>
          </button>
        </div>
        <p>© SIDB 2026</p>
        <a
          href="https://github.com/Kind-Wyllie-lab/saviour"
          target="_blank"
          rel="noopener noreferrer"
        >
          GitHub Page
        </a>
      </div>

      {showUpdateModal && (
        <div className="modal-overlay" onClick={() => setShowUpdateModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Update <strong>all modules and controller</strong>?</p>
            <p className="modal-subtext">
              Each device will pull the latest version from main and restart.
              Any active recording will be interrupted.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={handleUpdateAll}>
                Update All
              </button>
              <button className="reset-button" type="button" onClick={() => setShowUpdateModal(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showPowerModal && (
        <div className="modal-overlay" onClick={() => setShowPowerModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Power action for <strong>all modules and controller</strong></p>
            <p className="modal-subtext">
              Reboot will restart all devices and reconnect automatically.<br />
              Shutdown will power off all devices — manual restart required.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={handleRebootAll}>
                Reboot All
              </button>
              <button className="reset-button" type="button" onClick={handleShutdownAll}>
                Shutdown All
              </button>
              <button className="save-button" type="button" onClick={() => setShowPowerModal(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}

export default Sidebar;
