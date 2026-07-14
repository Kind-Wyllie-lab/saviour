import { useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "../ModuleActionsMenu/ModuleActionsMenu.css";

/**
 * ControllerActionsMenu — Restart service / Reboot / Shutdown for the
 * controller itself. Shared between the System page's controller row and
 * the Controller Config card header on the Settings page.
 *
 * onAction(actionType) fires after a confirmed action is sent, so callers
 * that want to track it (e.g. System page's update-results table) can —
 * it's optional, ControllerConfigCard doesn't need it.
 */
function ControllerActionsMenu({ onAction }) {
  const loggedIn = useIsLoggedIn();
  const [showActions, setShowActions] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState(null); // "restart_service" | "reboot" | "shutdown"

  const handleConfirm = () => {
    if (!confirmTarget) return;
    if (confirmTarget === "restart_service") {
      socket.emit("restart_saviour_controller_service");
    } else if (confirmTarget === "reboot") {
      socket.emit("reboot_controller");
    } else if (confirmTarget === "shutdown") {
      socket.emit("shutdown_controller");
    }
    onAction?.(confirmTarget);
    setConfirmTarget(null);
  };

  return (
    <>
      <button type="button" className="action-menu-btn" onClick={() => setShowActions(true)}
        disabled={!loggedIn} title={loggedIn ? undefined : "Login required for this action"}>
        Actions ▾
      </button>

      {showActions && (
        <div className="modal-overlay" onClick={() => setShowActions(false)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">Controller</p>
            <div className="actions-modal__list">
              <button type="button" className="actions-modal__item"
                onClick={() => { setConfirmTarget("restart_service"); setShowActions(false); }}>
                <span>Restart service</span>
                <span className="actions-modal__hint">Restarts the SAVIOUR program — controller does not reboot, reconnects automatically</span>
              </button>
              <button type="button" className="actions-modal__item"
                onClick={() => { setConfirmTarget("reboot"); setShowActions(false); }}>
                <span>Reboot</span>
                <span className="actions-modal__hint">Reboots the controller Pi — reconnects automatically</span>
              </button>
              <div className="actions-modal__divider" />
              <button type="button" className="actions-modal__item actions-modal__item--danger"
                onClick={() => { setConfirmTarget("shutdown"); setShowActions(false); }}>
                <span>Shutdown</span>
                <span className="actions-modal__hint">Powers off the controller — requires manual power cycle to restart</span>
              </button>
            </div>
            <div className="modal-buttons" style={{ marginTop: "8px" }}>
              <button className="save-button" type="button" onClick={() => setShowActions(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {confirmTarget && (
        <div className="modal-overlay" onClick={() => setConfirmTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            {confirmTarget === "restart_service" && <>
              <p>Restart the controller service?</p>
              <p className="modal-subtext">The SAVIOUR program will restart. The controller will briefly disconnect then reconnect automatically.</p>
            </>}
            {confirmTarget === "reboot" && <>
              <p>Reboot the controller?</p>
              <p className="modal-subtext">The controller Pi will reboot. It will reconnect automatically after restart. Any active recording sessions will be interrupted.</p>
            </>}
            {confirmTarget === "shutdown" && <>
              <p>Shut down the controller?</p>
              <p className="modal-subtext modal-subtext--warn">The controller will power off. A manual power cycle is required to bring it back online. Any active recording sessions will be interrupted.</p>
            </>}
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleConfirm}>
                {confirmTarget === "restart_service" ? "Restart" : confirmTarget === "reboot" ? "Reboot" : "Shutdown"}
              </button>
              <button className="save-button" type="button" onClick={() => setConfirmTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default ControllerActionsMenu;
