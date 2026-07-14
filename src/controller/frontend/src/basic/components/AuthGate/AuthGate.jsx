import { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import { isLoggedIn, hasChosenGuest, chooseGuest, rememberLogin, onAuthChange } from "/src/auth";
import "./AuthGate.css";

// Shown once per browser on first load: continue read-only as a guest, or
// log in with the shared admin password to unlock mutating/destructive
// actions (config changes, code deploys, power control, etc). Also
// reopened later via the "saviour:open-login" window event (e.g. a
// "Log in" link surfaced to guests elsewhere in the UI).
function AuthGate() {
  const [visible, setVisible] = useState(() => !isLoggedIn() && !hasChosenGuest());
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const passwordRef = useRef(password);
  useEffect(() => { passwordRef.current = password; }, [password]);

  useEffect(() => {
    const onSuccess = () => {
      rememberLogin(passwordRef.current);
      setSubmitting(false);
      setError("");
      setPassword("");
      setVisible(false);
    };
    const onError = (msg) => {
      setSubmitting(false);
      setError(msg || "Wrong password");
    };
    socket.on("login_success", onSuccess);
    socket.on("login_error", onError);
    return () => {
      socket.off("login_success", onSuccess);
      socket.off("login_error", onError);
    };
  }, []);

  useEffect(() => {
    const openGate = () => setVisible(true);
    window.addEventListener("saviour:open-login", openGate);
    // Generic fallback: any handler a guest isn't allowed to call emits
    // "auth_required" (see web.py's _require_auth), so surfacing the login
    // gate here covers every gated action without wiring a check into each
    // individual button across the app.
    socket.on("auth_required", openGate);
    return () => {
      window.removeEventListener("saviour:open-login", openGate);
      socket.off("auth_required", openGate);
    };
  }, []);

  useEffect(() => onAuthChange(() => setVisible(false)), []);

  if (!visible) return null;

  const handleLogin = () => {
    if (!password) return;
    setSubmitting(true);
    setError("");
    socket.emit("login", { password });
  };

  const handleGuest = () => {
    chooseGuest();
    setVisible(false);
  };

  return (
    <div className="login-modal-backdrop">
      <div className="login-modal">
        <h2>SAVIOUR Login</h2>
        <p className="login-modal-subtext">
          Log in to change configuration, deploy code, or control power.
          Guests can view status read-only.
        </p>
        <div className="login-form">
          <label htmlFor="auth-gate-password">Password</label>
          <input
            id="auth-gate-password"
            type="password"
            value={password}
            autoFocus
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") handleLogin(); }}
          />
        </div>
        {error && <p className="login-error">{error}</p>}
        <div className="login-buttons">
          <button onClick={handleGuest} className="login-guest-btn">
            Continue as Guest
          </button>
          <button onClick={handleLogin} disabled={submitting || !password}>
            {submitting ? "Logging in…" : "Log In"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default AuthGate;
