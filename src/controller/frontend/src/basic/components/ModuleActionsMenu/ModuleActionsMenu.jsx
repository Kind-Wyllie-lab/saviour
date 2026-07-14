import { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./ModuleActionsMenu.css";

/**
 * ModuleActionsMenu — the module lifecycle actions (Update / Restart /
 * Reboot / Shutdown / Remove) shared between the System page's per-module
 * row and each module's config card header, so both places drive the same
 * commands through the same UI instead of maintaining separate copies.
 */
function ModuleActionsMenu({ id, name, isOnline }) {
  const loggedIn = useIsLoggedIn();
  const [showActions, setShowActions] = useState(false);
  const [stagedMeta, setStagedMeta] = useState(null);
  const [updateStatus, setUpdateStatus] = useState(null); // null | "updating" | { success, output }
  const [restartTarget, setRestartTarget] = useState(false);
  const [rebootTarget, setRebootTarget] = useState(false);
  const [shutdownTarget, setShutdownTarget] = useState(false);
  const [shutdownState, setShutdownState] = useState(null); // null | "sent" | "acked"
  const [removeTarget, setRemoveTarget] = useState(false);
  const shutdownTimerRef = useRef(null);

  useEffect(() => {
    socket.emit("get_update_info");
    const onInfo = (data) => setStagedMeta(data?.staged ?? null);
    socket.on("update_info", onInfo);
    return () => socket.off("update_info", onInfo);
  }, []);

  useEffect(() => {
    const onResult = (data) => {
      if (data.module_id === id) setUpdateStatus({ success: data.success, output: data.output });
    };
    socket.on("module_update_result", onResult);
    return () => socket.off("module_update_result", onResult);
  }, [id]);

  useEffect(() => {
    const onAck = ({ module_id }) => {
      if (module_id === id) setShutdownState("acked");
    };
    socket.on("module_shutdown_ack", onAck);
    return () => socket.off("module_shutdown_ack", onAck);
  }, [id]);

  // Clear shutdown state once the module actually drops offline; the 90s
  // fallback timer below covers the case where that never happens in this
  // browser session.
  useEffect(() => {
    if (!isOnline) {
      setShutdownState(null);
      clearTimeout(shutdownTimerRef.current);
    }
  }, [isOnline]);

  useEffect(() => () => clearTimeout(shutdownTimerRef.current), []);

  const handleUpdate = () => {
    setUpdateStatus("updating");
    setShowActions(false);
    socket.emit("deploy_update_to_module", { module_id: id });
  };

  const handleRestartConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "restart_service", params: {} });
    setRestartTarget(false);
  };

  const handleRebootConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "reboot", params: {} });
    setRebootTarget(false);
  };

  const handleShutdownConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "shutdown", params: {} });
    setShutdownState("sent");
    setShutdownTarget(false);
    clearTimeout(shutdownTimerRef.current);
    shutdownTimerRef.current = setTimeout(() => setShutdownState(null), 90000);
  };

  const handleRemoveConfirm = () => {
    socket.emit("remove_module", { id });
    setRemoveTarget(false);
  };

  return (
    <>
      {shutdownState ? (
        <span className="module-actions-shutdown-status">
          {shutdownState === "acked" ? "Powering off…" : "Shutting down…"}
        </span>
      ) : (
        <button type="button" className="action-menu-btn" onClick={() => setShowActions(true)}
          disabled={!loggedIn} title={loggedIn ? undefined : "Login required for this action"}>
          Actions ▾
        </button>
      )}
      {updateStatus && updateStatus !== "updating" && (
        <span className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
          {updateStatus.success ? "Updated" : `Failed: ${updateStatus.output}`}
        </span>
      )}

      {showActions && (
        <div className="modal-overlay" onClick={() => setShowActions(false)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">{name}</p>
            <div className="actions-modal__list">
              {isOnline ? (<>
                {stagedMeta && (
                  <button type="button" className="actions-modal__item" onClick={handleUpdate}>
                    <span>Update</span>
                    <span className="actions-modal__hint">Deploy staged package {stagedMeta.version ?? ""} to this module only</span>
                  </button>
                )}
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRestartTarget(true); setShowActions(false); }}>
                  <span>Restart service</span>
                  <span className="actions-modal__hint">Restarts the SAVIOUR program — module does not reboot, reconnects automatically</span>
                </button>
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRebootTarget(true); setShowActions(false); }}>
                  <span>Reboot</span>
                  <span className="actions-modal__hint">Reboots the module — reconnects automatically</span>
                </button>
                <div className="actions-modal__divider" />
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setShutdownTarget(true); setShowActions(false); }}>
                  <span>Shutdown</span>
                  <span className="actions-modal__hint">Powers off — reconnects when switched back on</span>
                </button>
              </>) : (
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setRemoveTarget(true); setShowActions(false); }}>
                  <span>Remove</span>
                  <span className="actions-modal__hint">Remove offline module from tracking</span>
                </button>
              )}
            </div>
            <div className="modal-buttons" style={{ marginTop: "8px" }}>
              <button className="save-button" type="button" onClick={() => setShowActions(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {restartTarget && (
        <div className="modal-overlay" onClick={() => setRestartTarget(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Restart service on <strong>{name}</strong>?</p>
            <p className="modal-subtext">The saviour service will restart. The module will briefly go offline then reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRestartConfirm}>Restart</button>
              <button className="save-button" type="button" onClick={() => setRestartTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {rebootTarget && (
        <div className="modal-overlay" onClick={() => setRebootTarget(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot <strong>{name}</strong>?</p>
            <p className="modal-subtext">The module will reboot and reconnect automatically. Any active recording will be interrupted.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRebootConfirm}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setRebootTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {shutdownTarget && (
        <div className="modal-overlay" onClick={() => setShutdownTarget(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Shut down <strong>{name}</strong>?</p>
            <p className="modal-subtext">
              The module will power off. It will be re-added automatically when it comes back online.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleShutdownConfirm}>Shutdown</button>
              <button className="save-button" type="button" onClick={() => setShutdownTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {removeTarget && (
        <div className="modal-overlay" onClick={() => setRemoveTarget(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Remove <strong>{name}</strong> from tracking?</p>
            <p className="modal-subtext">
              This module is offline and will be removed from the system. If it comes back online it will be re-added automatically.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRemoveConfirm}>Remove</button>
              <button className="save-button" type="button" onClick={() => setRemoveTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default ModuleActionsMenu;
