import { useState, useEffect, useRef } from "react";
import { NavLink, useNavigate, useLocation } from "react-router";
import socket from "/src/socket";
import { isLoggedIn, onAuthChange, logOut } from "/src/auth";

import "./Sidebar.css";
import UoELogo from "/src/assets/logos/uofe_logo_alpha.png";
import SIDBLogo from "/src/assets/logos/sidb_logo_alpha.png";

const CHUNK_SIZE = 256 * 1024; // 256 KiB

function Sidebar({ navItems }) {
  const [showPowerModal, setShowPowerModal]   = useState(false);
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [shutdownState, setShutdownState]     = useState(null); // null | "sent" | "acked"
  const [hostname, setHostname]               = useState(null);
  const [version, setVersion]                 = useState(null);
  const [configDirty, setConfigDirty]         = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  // Update modal state
  const [updateInfo, setUpdateInfo]           = useState(null); // { running_version, staged, git }
  // null = the two steps (Update controller / Deploy to modules);
  // "controller" = pick a source; "zip" | "git" = that source's panel.
  const [updateMode, setUpdateMode]           = useState(null);
  const [uploadProgress, setUploadProgress]   = useState(null); // { received, total }
  const [uploadError, setUploadError]         = useState(null);
  const [stagedMeta, setStagedMeta]           = useState(null); // completed upload metadata
  const [deployStatus, setDeployStatus]       = useState(null); // null | "deploying" | "done" | "error"
  const [deployError, setDeployError]         = useState(null);
  const [stagingCurrent, setStagingCurrent]   = useState(false); // Deploy to modules: packaging running code
  const [gitPullStatus, setGitPullStatus]     = useState(null); // null | { stage, branch, commit }
  const [gitRemote, setGitRemote]             = useState(null); // null (checking) | { reachable, reason }
  // Controller self-update in flight: null | "installing" | "building" |
  // "restarting" | "done". "done" is set when the socket reconnects after the
  // service restart, which is the only signal the new code is running.
  const [ctrlPhase, setCtrlPhase]             = useState(null);
  const [loggedIn, setLoggedIn]               = useState(() => isLoggedIn());
  const [showAccountMenu, setShowAccountMenu] = useState(false);
  const fileInputRef = useRef(null);

  // Sidebar collapse — full-hide with a floating reopen button. Persisted so
  // it survives reloads; every localStorage access is wrapped because a
  // private window / disabled site data makes it throw.
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("saviour_sidebar_collapsed") === "1"; }
    catch { return false; }
  });
  const toggleCollapsed = () => setCollapsed((v) => {
    const next = !v;
    try { localStorage.setItem("saviour_sidebar_collapsed", next ? "1" : "0"); }
    catch { /* storage unavailable — collapse still works for this session */ }
    return next;
  });

  useEffect(() => onAuthChange(() => {
    setLoggedIn(isLoggedIn());
    setShowAccountMenu(false);
  }), []);

  useEffect(() => {
    socket.emit("get_controller_info");
    const handler = (data) => {
      if (data.hostname) setHostname(data.hostname);
      if (data.version) setVersion(data.version);
    };
    socket.on("controller_info_response", handler);
    return () => socket.off("controller_info_response", handler);
  }, []);

  // Allow other pages to open the update modal via a window event
  useEffect(() => {
    const handler = () => openUpdateModal();
    window.addEventListener("saviour:open-update-modal", handler);
    return () => window.removeEventListener("saviour:open-update-modal", handler);
    // Subscribe once on mount; openUpdateModal only sets state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The currently-mounted ConfigCard's useConfigForm broadcasts its dirty
  // state here so leaving the page entirely (not just switching modules
  // within Settings, which Settings.jsx itself guards) warns first.
  useEffect(() => {
    const handler = (e) => setConfigDirty(!!e.detail?.dirty);
    window.addEventListener("saviour:config-dirty", handler);
    return () => window.removeEventListener("saviour:config-dirty", handler);
  }, []);

  const handleNavClick = (path) => (e) => {
    if (path === location.pathname) return;
    if (configDirty && !window.confirm(
      "You have unsaved config changes that will be lost if you leave this page. Continue?"
    )) {
      e.preventDefault();
      return;
    }
    setConfigDirty(false);
  };

  useEffect(() => {
    const onAck = () => setShutdownState("acked");
    socket.on("shutdown_saviour_ack", onAck);
    return () => socket.off("shutdown_saviour_ack", onAck);
  }, []);

  // Fetch version info when modal opens
  useEffect(() => {
    if (!showUpdateModal) return;
    socket.emit("get_update_info");
    socket.emit("check_git_remote");
    const handler = (data) => setUpdateInfo(data);
    const onRemote = (data) => setGitRemote(data);
    socket.on("update_info", handler);
    socket.on("git_remote_status", onRemote);
    return () => {
      socket.off("update_info", handler);
      socket.off("git_remote_status", onRemote);
    };
  }, [showUpdateModal]);

  // The controller restart drops the socket; reconnecting is the signal the
  // update landed. Refresh the versions so step 2 shows what modules will get.
  useEffect(() => {
    if (!ctrlPhase || ctrlPhase === "done") return;
    const onConnect = () => {
      setCtrlPhase("done");
      setUpdateMode(null);
      setGitPullStatus(null);
      setStagedMeta(null);
      socket.emit("get_update_info");
    };
    socket.on("connect", onConnect);
    return () => socket.off("connect", onConnect);
  }, [ctrlPhase]);

  // Upload socket listeners
  useEffect(() => {
    const onProgress = ({ received, total }) => setUploadProgress({ received, total });
    const onComplete = (meta) => {
      setStagedMeta(meta);
      setUploadProgress(null);
      setStagingCurrent(false);
      setUpdateInfo(prev => prev ? { ...prev, staged: meta } : { running_version: "?", staged: meta });
    };
    const onError = ({ error }) => {
      setUploadError(error);
      setUploadProgress(null);
      setStagingCurrent(false);
      setGitPullStatus(null);
      setCtrlPhase(null);
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

  // Git pull socket listener
  useEffect(() => {
    const onGitStatus = (data) => {
      setGitPullStatus(data);
      if (data.stage === "applying") setCtrlPhase("building");
    };
    socket.on("git_pull_status", onGitStatus);
    return () => socket.off("git_pull_status", onGitStatus);
  }, []);

  // Deploy socket listeners
  useEffect(() => {
    const onStatus = ({ stage }) => {
      if (stage === "building_frontend") setCtrlPhase("building");
      if (stage === "modules_notified") {
        // The System page takes over from here with a per-module progress
        // modal (deviceStatuses / UpdateProgressModal) as soon as this same
        // event reaches it — close this one so the two don't stack.
        setShowUpdateModal(false);
      }
    };
    const onError = ({ error }) => {
      setDeployStatus("error");
      setDeployError(error);
      setStagingCurrent(false);
      setCtrlPhase(null);
    };
    socket.on("deploy_update_status", onStatus);
    socket.on("deploy_update_error",  onError);
    return () => {
      socket.off("deploy_update_status", onStatus);
      socket.off("deploy_update_error",  onError);
    };
  }, []);

  // Reopen-login fallback for the whole upload/stage/deploy family: those
  // handlers reject an unauthenticated connection via the shared
  // "auth_required" event (see web.py's _require_auth) so the app-wide
  // AuthGate reopens the login form, rather than a handler-specific *_error
  // name. Without this, upload/deploy state left mid-flight here would spin
  // forever, since only the handler-specific *_error events used to clear it.
  useEffect(() => {
    const onAuthRequired = () => {
      if (uploadProgress !== null || stagingCurrent) {
        setUploadProgress(null);
        setStagingCurrent(false);
        setUploadError("Login required — please log in and retry");
      }
      if ((deployStatus !== null && deployStatus !== "error")
          || (ctrlPhase && ctrlPhase !== "done") || gitPullStatus) {
        setDeployStatus("error");
        setDeployError("Login required — please log in and retry");
        setCtrlPhase(null);
        setGitPullStatus(null);
      }
    };
    socket.on("auth_required", onAuthRequired);
    return () => socket.off("auth_required", onAuthRequired);
  }, [uploadProgress, stagingCurrent, deployStatus, ctrlPhase, gitPullStatus]);

  const handleRebootAll = () => {
    socket.emit("reboot_saviour");
    setShowPowerModal(false);
  };

  const handleShutdownAll = () => {
    socket.emit("shutdown_saviour");
    setShutdownState("sent");
  };

  const openUpdateModal = () => {
    if (!loggedIn) {
      window.dispatchEvent(new Event("saviour:open-login"));
      return;
    }
    setUpdateInfo(null);
    setUpdateMode(null);
    setStagedMeta(null);
    setUploadProgress(null);
    setUploadError(null);
    setDeployStatus(null);
    setDeployError(null);
    setGitPullStatus(null);
    setGitRemote(null);
    setStagingCurrent(false);
    setCtrlPhase(null);
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

  // Step 1 (zip): apply the uploaded package to this controller — snapshot,
  // rsync over the checkout, pip + frontend rebuild, restart.
  const handleInstallZip = () => {
    setDeployStatus(null);
    setDeployError(null);
    setCtrlPhase("installing");
    socket.emit("deploy_update_to_controller");
  };

  // Step 1 (git): fetch + hard-reset to origin/<branch>, then rebuild and
  // restart. Modules are step 2, so deploy_modules stays off here.
  const handleGitPull = () => {
    setGitPullStatus({ stage: "fetching" });
    setUploadError(null);
    setDeployStatus(null);
    setDeployError(null);
    socket.emit("git_pull_update", { apply_controller: true, deploy_modules: false });
  };

  // Step 2: package exactly what the controller is running and push it to
  // every module, so modules always end up matching the controller.
  const handleDeployModules = () => {
    setStagingCurrent(true);
    setUploadError(null);
    setDeployStatus(null);
    setDeployError(null);
    socket.emit("stage_and_deploy_modules");
  };

  const gitInfo = updateInfo?.git;
  const gitUsable = !!gitInfo?.available && !!gitRemote?.reachable;
  const gitHint = !gitInfo
    ? "Checking…"
    : !gitInfo.available
      ? (gitInfo.reason || "Not available on this device")
      : gitRemote === null
        ? `Checking connection to origin/${gitInfo.branch}…`
        : gitRemote.reachable
          ? `Pull the latest origin/${gitInfo.branch}`
          : `Needs internet access: can't reach origin (${gitRemote.reason || "offline"})`;
  const ctrlBusy = (!!ctrlPhase && ctrlPhase !== "done") || !!gitPullStatus;
  const busy = ctrlBusy || stagingCurrent || !!uploadProgress;
  const GIT_STAGE_LABEL = {
    fetching:  "Fetching from origin…",
    resetting: "Resetting to latest commit…",
    staging:   "Packaging update…",
    applying:  "Rebuilding & restarting controller…",
  };
  const CTRL_PHASE_LABEL = {
    installing: "Backing up and installing package…",
    building:   "Rebuilding & restarting controller…",
  };

  return (
    <>
      {collapsed && (
        <button
          type="button"
          className="sidebar-reopen"
          title="Show sidebar"
          aria-label="Show sidebar"
          onClick={toggleCollapsed}
        >
          »
        </button>
      )}
      <header className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`}>
        <button
          type="button"
          className="sidebar-collapse-btn"
          title="Hide sidebar"
          aria-label="Hide sidebar"
          onClick={toggleCollapsed}
        >
          «
        </button>
      <div className="header-content">
        <div className="logo-container">
          <img src={UoELogo} alt="UoE Logo" className="logo" />
          <img src={SIDBLogo} alt="SIDB Logo" className="logo" />
        </div>

        {/* Wordmark rendered as themed text (no raster asset) so it inverts
            cleanly with the light/dark theme — the dot reuses the semantic
            record-red var. Swap for an <img>/inline <svg> if a proper vector
            logo is supplied. */}
        <div className="sidebar-wordmark" role="img" aria-label={document.title}>
          <span className="sidebar-wordmark__bracket" aria-hidden="true">[</span>
          <span className="sidebar-wordmark__dot" aria-hidden="true" />
          <span className="sidebar-wordmark__text">SAVIOUR</span>
          <span className="sidebar-wordmark__bracket" aria-hidden="true">]</span>
        </div>
        {version && (
          <button
            type="button"
            className="sidebar-version-btn"
            title="Go to System page to deploy updates"
            onClick={() => {
              if ("/system" === location.pathname) return;
              if (configDirty && !window.confirm(
                "You have unsaved config changes that will be lost if you leave this page. Continue?"
              )) {
                return;
              }
              setConfigDirty(false);
              navigate("/system");
            }}
          >
            {version}
          </button>
        )}
        {hostname && <p className="sidebar-hostname">{hostname}</p>}

        <nav className="main-nav">
          {navItems.map(({ label, path, disabled }) =>
            disabled ? (
              <span key={path} className="nav-link disabled">{label}</span>
            ) : (
              <NavLink key={path} to={path} className="nav-link" onClick={handleNavClick(path)}>{label}</NavLink>
            )
          )}
        </nav>
      </div>

      <div className="footer">
        {/* Access mode chip above the power button it gates. Named for what
            this browser can do, not who the user is: there's one shared
            password, not per-user accounts. */}
        <div className="footer-role-wrap">
          <button
            className={`footer-role-btn ${loggedIn ? "footer-role-btn--admin" : "footer-role-btn--guest"}`}
            title={loggedIn
              ? "Controls enabled: you can change config, record and deploy. Click for options"
              : "Controls disabled: sign in to change config, record and deploy"}
            onClick={() => {
              if (loggedIn) {
                setShowAccountMenu(v => !v);
              } else {
                window.dispatchEvent(new Event("saviour:open-login"));
              }
            }}
          >
            <span className="footer-role-text">
              {loggedIn ? "Controls enabled" : "Controls disabled"}
              {!loggedIn && <span className="footer-role-sub">Sign in</span>}
            </span>
          </button>
          {showAccountMenu && (
            <div className="footer-role-menu">
              <button onClick={() => {
                setShowAccountMenu(false);
                window.dispatchEvent(new Event("saviour:open-change-password"));
              }}>
                Change Password
              </button>
              <button onClick={() => { setShowAccountMenu(false); logOut(); }}>
                Sign out
              </button>
            </div>
          )}
        </div>
        <div className="footer-actions">
          <button
            className="footer-icon-btn footer-icon-btn--power"
            title={loggedIn ? "Reboot or shut down all devices" : "Login required for this action"}
            disabled={!loggedIn}
            onClick={() => setShowPowerModal(true)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
              <line x1="12" y1="2" x2="12" y2="12" />
            </svg>
          </button>
        </div>
        <div className="footer-links">
          <a className="footer-link" href="https://github.com/Kind-Wyllie-lab/saviour" target="_blank" rel="noopener noreferrer">
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.09 3.29 9.4 7.86 10.93.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.88-1.54-3.88-1.54-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.75 2.7 1.25 3.36.96.1-.75.4-1.25.73-1.54-2.55-.29-5.23-1.28-5.23-5.68 0-1.25.45-2.28 1.18-3.08-.12-.29-.51-1.46.11-3.05 0 0 .96-.31 3.15 1.18a10.9 10.9 0 0 1 5.74 0c2.19-1.49 3.15-1.18 3.15-1.18.62 1.59.23 2.76.11 3.05.74.8 1.18 1.83 1.18 3.08 0 4.41-2.69 5.39-5.25 5.67.42.36.78 1.08.78 2.17 0 1.57-.01 2.83-.01 3.22 0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z" />
            </svg>
            GitHub
          </a>
          <a className="footer-link" href="https://saviour.readthedocs.io" target="_blank" rel="noopener noreferrer">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
            Docs
          </a>
        </div>
        <p className="footer-copyright">© SIDB 2026</p>
      </div>
      </header>

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

            {/* ── Home: controller first, then modules ── */}
            {updateMode === null && (<>
              {ctrlPhase === "done" && (
                <p className="update-done">
                  Controller updated. Deploy to modules to bring them onto the same version.
                </p>
              )}
              <div className="update-mode-picker">
                <button
                  type="button"
                  className="update-mode-btn"
                  disabled={busy}
                  onClick={() => { setUploadError(null); setUpdateMode("controller"); }}
                >
                  <span className="update-mode-btn-title">
                    <span className="update-step-num">1</span>Update controller
                  </span>
                  <span className="update-mode-btn-hint">
                    Install new code on this controller from a zip file or git, then rebuild and restart
                  </span>
                </button>
                <button
                  type="button"
                  className="update-mode-btn"
                  disabled={busy}
                  onClick={handleDeployModules}
                >
                  <span className="update-mode-btn-title">
                    <span className="update-step-num">2</span>
                    {stagingCurrent ? "Packaging…" : "Deploy to modules"}
                  </span>
                  <span className="update-mode-btn-hint">
                    Push the code this controller is running
                    {updateInfo ? ` (${updateInfo.running_version})` : ""} to every module
                  </span>
                </button>
              </div>
            </>)}

            {/* ── Step 1: choose a source ── */}
            {updateMode === "controller" && (
              <div className="update-mode-picker">
                <button
                  type="button"
                  className="update-mode-btn"
                  onClick={() => { setUploadError(null); setStagedMeta(null); setUpdateMode("zip"); }}
                >
                  <span className="update-mode-btn-title">Zip upload</span>
                  <span className="update-mode-btn-hint">
                    Upload a release package — works on offline rigs
                  </span>
                </button>
                <button
                  type="button"
                  className="update-mode-btn"
                  disabled={!gitUsable}
                  title={gitUsable ? undefined : gitHint}
                  onClick={() => { setUploadError(null); setGitPullStatus(null); setUpdateMode("git"); }}
                >
                  <span className="update-mode-btn-title">Git pull</span>
                  <span className="update-mode-btn-hint">{gitHint}</span>
                </button>
              </div>
            )}

            {/* ── Step 1: zip ── */}
            {updateMode === "zip" && (<>
              {!uploadProgress && !stagedMeta && (
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

              {stagedMeta && (<>
                <div className="update-version-row">
                  <span className="update-version-label">Package</span>
                  <code className="update-version-value update-version-staged">{stagedMeta.version}</code>
                </div>
                <p className="update-git-warning">
                  Installs this package over the controller's code (a snapshot is
                  taken first, so it can be reverted from the System page), then
                  rebuilds the frontend and restarts. This page reconnects on its own.
                </p>
                <div className="update-git-pull-row">
                  <button className="save-button" type="button" onClick={handleInstallZip} disabled={ctrlBusy}>
                    {ctrlBusy ? (CTRL_PHASE_LABEL[ctrlPhase] || "Installing…") : "Install on controller"}
                  </button>
                </div>
              </>)}
            </>)}

            {/* ── Step 1: git ── */}
            {updateMode === "git" && (<>
              <div className="update-version-row">
                <span className="update-version-label">Branch</span>
                <code className="update-version-value">{gitInfo?.branch ?? "-"}</code>
              </div>
              <p className="update-git-warning">
                Fetches <code>origin/{gitInfo?.branch}</code> and hard-resets this
                controller's checkout to match it (any local changes on the
                controller are discarded), then rebuilds the frontend and
                restarts. This page reconnects on its own.
              </p>
              <div className="update-git-pull-row">
                <button className="save-button" type="button" onClick={handleGitPull} disabled={ctrlBusy}>
                  {ctrlBusy
                    ? (GIT_STAGE_LABEL[gitPullStatus?.stage] || CTRL_PHASE_LABEL[ctrlPhase] || "Pulling…")
                    : "Pull & update controller"}
                </button>
              </div>
              {gitPullStatus?.commit && (
                <p className="update-git-info">
                  Now at <code>{gitPullStatus.commit}</code> ({gitPullStatus.branch})
                </p>
              )}
            </>)}

            {uploadError && (
              <p className="update-error">{uploadError}</p>
            )}
            {deployStatus === "error" && (
              <p className="update-error">{deployError || "Update failed."}</p>
            )}

            <div className="modal-buttons">
              {updateMode !== null && !ctrlBusy && (
                <button className="save-button" type="button"
                  onClick={() => setUpdateMode(updateMode === "controller" ? null : "controller")}>
                  ← Back
                </button>
              )}
              <button
                className="save-button"
                type="button"
                onClick={() => setShowUpdateModal(false)}
              >
                {ctrlPhase === "done" ? "Close" : "Cancel"}
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
                Shutdown will power off all devices - manual restart required.
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
    </>
  );
}

export default Sidebar;
