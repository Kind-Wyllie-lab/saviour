// Higher-level auth API built on authStorage.js's raw localStorage access.
// SAVIOUR uses a single shared admin password (no per-user accounts):
// guests get read-only access, a logged-in connection can perform
// mutating/destructive actions. The password is remembered so a dropped
// connection re-authenticates automatically on reconnect (see socket.jsx,
// which reads it synchronously at module load for the first connection).
import socket from "/src/socket";
import {
  getStoredPassword,
  setStoredPassword,
  clearStoredPassword,
  hasChosenGuest,
  setChosenGuest,
} from "/src/authStorage";

export { hasChosenGuest };

export function isLoggedIn() {
  return !!getStoredPassword();
}

export function chooseGuest() {
  setChosenGuest();
  _notify();
}

// Call after the server confirms "login_success" on the live connection --
// this doesn't itself authenticate anything, it just remembers the
// password so future reconnects carry it automatically.
export function rememberLogin(password) {
  setStoredPassword(password);
  socket.auth = { password };
  _notify();
}

export function logOut() {
  clearStoredPassword();
  socket.auth = {};
  _notify();
}

// Lightweight cross-component signal, mirroring the existing
// "saviour:open-update-modal" window-event pattern used elsewhere rather
// than introducing React Context for a single boolean.
function _notify() {
  window.dispatchEvent(new Event("saviour:auth-changed"));
}

export function onAuthChange(handler) {
  window.addEventListener("saviour:auth-changed", handler);
  return () => window.removeEventListener("saviour:auth-changed", handler);
}
