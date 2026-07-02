import { useState, useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import socket from "/src/socket";

import "./Sidebar.css";
import UoELogo from "/src/assets/logos/uofe_logo_alpha.png";
import SIDBLogo from "/src/assets/logos/sidb_logo_alpha.png";

const CHUNK_SIZE = 256 * 1024; // 256 KiB

function Sidebar({ navItems }) {
  const [showPowerModal, setShowPowerModal]   = useState(false);
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [shutdownState, setShutdownState]     = useState(null); // null | "sent" | "acked"
  const [hostname, setHostname]               = useState(null);

  // Update modal state
  const [updateInfo, setUpdateInfo]           = useState(null); // { running_version, staged }
  const [uploadProgress, setUploadProgress]   = useState(null); // { received, total }
  const [uploadError, setUploadError]         = useState(null);
  const [stagedMeta, setStagedMeta]           = useState(null); // completed upload metadata
  const [deployStatus, setDeployStatus]       = useState(null); // null | "deploying" | "done" | "error"
  const [deployError, setDeployError]         = useState(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    socket.emit("get_controller_info");
    const handler = (data) => { if (data.hostname) setHostname(data.hostname); };
    socket.on("controller_info_response", handler);
    return () => socket.off("controller_info_response", handler);
  }, []);

  // Allow other pages to open the update modal via a window event
  useEffect(() => {
    const handler = () => openUpdateModal();
    window.addEventListener("saviour:open-update-modal", handler);
    return () => window.removeEventListener("saviour:open-update-modal", handler);
  }, []);

  useEffect(() => {
    const onAck = () => setShutdownState("acked");
    socket.on("shutdown_saviour_ack", onAck);
    return () => socket.off("shutdown_saviour_ack", onAck);
  }, []);

  // Fetch version info when modal opens
  useEffect(() => {
    if (!showUpdateModal) return;
    socket.emit("get_update_info");
    const handler = (data) => setUpdateInfo(data);
    socket.on("update_info", handler);
    return () => socket.off("update_info", handler);
  }, [showUpdateModal]);

  // Upload socket listeners
  useEffect(() => {
    const onProgress = ({ received, total }) => setUploadProgress({ received, total });
    const onComplete = (meta) => {
      setStagedMeta(meta);
      setUploadProgress(null);
      setUpdateInfo(prev => prev ? { ...prev, staged: meta } : { running_version: "?", staged: meta });
    };
    const onError = ({ error }) => {
      setUploadError(error);
      setUploadProgress(null);
    };
    socket.on("upload_update_progress", onProgress);
    socket.on("upload_update_complete", onComplete);
    socket.on("upload_update_error",    onError);
    return () => {
      socket.off("upload_update_progress", onProgress);
      socket.off("upload_update_complete", onComplete);
      socket.off("upload_update_error",    onError);
    };
  }, []);

  // Deploy socket listeners
  useEffect(() => {
    const onStatus = ({ stage, count }) => {
      if (stage === "modules_notified") {
        setDeployStatus(`Notified ${count} module${count !== 1 ? "s" : ""} — applying to controller…`);
      }
    };
    const onError = ({ error }) => {
      setDeployStatus("error");
      setDeployError(error);
    };
    socket.on("deploy_update_status", onStatus);
    socket.on("deploy_update_error",  onError);
    return () => {
      socket.off("deploy_update_status", onStatus);
      socket.off("deploy_update_error",  onError);
    };
  }, []);

  const handleRebootAll = () => {
    socket.emit("reboot_saviour");
    setShowPowerModal(false);
  };

  const handleShutdownAll = () => {
    socket.emit("shutdown_saviour");
    setShutdownState("sent");
  };

  const openUpdateModal = () => {
    setUpdateInfo(null);
    setStagedMeta(null);
    setUploadProgress(null);
    setUploadError(null);
    setDeployStatus(null);
    setDeployError(null);
    setShowUpdateModal(true);
  };

  const handleFileSelect = (file) => {
    if (!file || !file.name.endsWith(".zip")) {
      setUploadError("Please select a .zip file.");
      return;
    }
    setUploadError(null);
    setStagedMeta(null);
    setDeployStatus(null);
    setDeployError(null);
    setUploadProgress({ received: 0, total: 0 });

    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    socket.emit("upload_update_start", {
      filename:     file.name,
      total_chunks: totalChunks,
      total_bytes:  file.size,
    });

    const sendChunks = async () => {
      for (let i = 0; i < totalChunks; i++) {
        const blob  = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
        const bytes = await blob.arrayBuffer();
        socket.emit("upload_update_chunk", { index: i, data: bytes });
        // small yield so the browser doesn't freeze on large files
        await new Promise(r => setTimeout(r, 0));
      }
    };
    sendChunks();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    handleFileSelect(e.dataTransfer.files[0]);
  };

  const handleDeploy = () => {
    setDeployStatus("deploying");
    socket.emit("deploy_update");
  };

  const staged = stagedMeta || updateInfo?.staged;

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
              <span key={path} className="nav-link disabled">{label}</span>
            ) : (
              <NavLink key={path} to={path} className="nav-link">{label}</NavLink>
            )
          )}
        </nav>
      </div>

      <div className="footer">
        <div className="footer-actions">
          <button
            className="footer-icon-btn footer-icon-btn--update"
            title="Stage and deploy a software update"
            onClick={openUpdateModal}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="3" x2="12" y2="15" />
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
        <a href="https://github.com/Kind-Wyllie-lab/saviour" target="_blank" rel="noopener noreferrer">
          GitHub Page
        </a>
      </div>

      {showUpdateModal && (
        <div className="modal-overlay" onClick={() => setShowUpdateModal(false)}>
          <div className="modal update-modal" onClick={e => e.stopPropagation()}>
            <h3 className="modal-title">Software Update</h3>

            <div className="update-version-row">
              <span className="update-version-label">Running</span>
              <code className="update-version-value">
                {updateInfo ? updateInfo.running_version : "…"}
              </code>
            </div>
            {staged && (
              <div className="update-version-row">
                <span className="update-version-label">Staged</span>
                <code className="update-version-value update-version-staged">
                  {staged.version}
                </code>
              </div>
            )}

            {/* Drop zone — always shown unless an upload is in flight */}
            {!uploadProgress && (
              <div
                className="update-dropzone"
                onDragOver={e => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="update-dropzone-icon">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                <span>Drop a <code>.zip</code> package here or click to browse</span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip"
                  style={{ display: "none" }}
                  onChange={e => handleFileSelect(e.target.files[0])}
                />
              </div>
            )}

            {uploadProgress && !stagedMeta && (
              <div className="update-progress-wrap">
                <div className="update-progress-bar">
                  <div
                    className="update-progress-fill"
                    style={{
                      width: uploadProgress.total
                        ? `${Math.round((uploadProgress.received / uploadProgress.total) * 100)}%`
                        : "0%"
                    }}
                  />
                </div>
                <span className="update-progress-label">
                  Uploading… {uploadProgress.total
                    ? `${uploadProgress.received} / ${uploadProgress.total} chunks`
                    : "starting"}
                </span>
              </div>
            )}

            {uploadError && (
              <p className="update-error">{uploadError}</p>
            )}

            {deployStatus && deployStatus !== "error" && (
              <p className="update-deploy-status">{deployStatus === "deploying" ? "Deploying…" : deployStatus}</p>
            )}
            {deployStatus === "error" && (
              <p className="update-error">{deployError || "Deploy failed."}</p>
            )}

            <div className="modal-buttons">
              {staged && !deployStatus && (
                <button className="save-button" type="button" onClick={handleDeploy}>
                  Deploy to All
                </button>
              )}
              <button
                className="reset-button"
                type="button"
                onClick={() => setShowUpdateModal(false)}
              >
                {deployStatus ? "Close" : "Cancel"}
              </button>
            </div>
          </div>
        </div>
      )}

      {(showPowerModal || shutdownState) && (
        <div className="modal-overlay" onClick={() => { if (!shutdownState) setShowPowerModal(false); }}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            {shutdownState ? (<>
              <p>{shutdownState === "acked" ? "Powering off…" : "Shutting down…"}</p>
              <p className="modal-subtext">
                {shutdownState === "acked"
                  ? "Shutdown command acknowledged. All devices are powering off."
                  : "Sending shutdown command to all devices…"}
              </p>
            </>) : (<>
              <p>Power action for <strong>all modules and controller</strong></p>
              <p className="modal-subtext">
                Reboot will restart all devices and reconnect automatically.<br />
                Shutdown will power off all devices — manual restart required.
              </p>
              <div className="modal-buttons">
                <button className="save-button" type="button" onClick={handleRebootAll}>Reboot All</button>
                <button className="reset-button" type="button" onClick={handleShutdownAll}>Shutdown All</button>
                <button className="save-button" type="button" onClick={() => setShowPowerModal(false)}>Cancel</button>
              </div>
            </>)}
          </div>
        </div>
      )}
    </header>
  );
}

export default Sidebar;
