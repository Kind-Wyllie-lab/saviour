import { useEffect, useState } from "react";
import socket from "/src/socket";
import { rememberLogin } from "/src/auth";
import "../AuthGate/AuthGate.css";

// Opened via the "saviour:open-change-password" window event (see the
// Sidebar's account menu). Requires the current password, not just an
// already-authenticated connection -- otherwise a session left logged in
// on a shared screen could be used to lock everyone else out.
function ChangePasswordModal() {
  const [visible, setVisible] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const open = () => {
      setVisible(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setError("");
      setSuccess(false);
    };
    window.addEventListener("saviour:open-change-password", open);
    return () => window.removeEventListener("saviour:open-change-password", open);
  }, []);

  useEffect(() => {
    const onSuccess = () => {
      rememberLogin(newPassword);
      setSubmitting(false);
      setSuccess(true);
      setError("");
    };
    const onError = ({ error }) => {
      setSubmitting(false);
      setError(error || "Could not change password");
    };
    socket.on("change_password_success", onSuccess);
    socket.on("change_password_error", onError);
    return () => {
      socket.off("change_password_success", onSuccess);
      socket.off("change_password_error", onError);
    };
  }, [newPassword]);

  if (!visible) return null;

  const handleSubmit = () => {
    if (!currentPassword || !newPassword) return;
    if (newPassword !== confirmPassword) {
      setError("New passwords don't match");
      return;
    }
    setSubmitting(true);
    setError("");
    socket.emit("change_admin_password", {
      current_password: currentPassword,
      new_password: newPassword,
    });
  };

  return (
    <div className="login-modal-backdrop" onClick={() => setVisible(false)}>
      <div className="login-modal" onClick={e => e.stopPropagation()}>
        <h2>Change Password</h2>
        {success ? (
          <>
            <p className="login-modal-subtext">Password changed.</p>
            <div className="login-buttons">
              <button onClick={() => setVisible(false)}>Close</button>
            </div>
          </>
        ) : (
          <>
            <div className="login-form">
              <label htmlFor="cp-current">Current password</label>
              <input
                id="cp-current"
                type="password"
                value={currentPassword}
                autoFocus
                onChange={e => setCurrentPassword(e.target.value)}
              />
              <label htmlFor="cp-new">New password</label>
              <input
                id="cp-new"
                type="password"
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
              />
              <label htmlFor="cp-confirm">Confirm new password</label>
              <input
                id="cp-confirm"
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") handleSubmit(); }}
              />
            </div>
            {error && <p className="login-error">{error}</p>}
            <div className="login-buttons">
              <button className="login-guest-btn" onClick={() => setVisible(false)} disabled={submitting}>
                Cancel
              </button>
              <button onClick={handleSubmit} disabled={submitting || !currentPassword || !newPassword}>
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default ChangePasswordModal;
