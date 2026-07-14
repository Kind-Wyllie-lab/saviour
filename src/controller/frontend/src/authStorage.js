// Raw localStorage access for the shared admin password, kept dependency-free
// (no socket import) so socket.jsx can read it synchronously at module load
// time, before the very first connection attempt.
const PASSWORD_KEY = "saviour_admin_password";
const GUEST_KEY = "saviour_guest_choice";

export function getStoredPassword() {
  return localStorage.getItem(PASSWORD_KEY) || "";
}

export function setStoredPassword(password) {
  localStorage.setItem(PASSWORD_KEY, password);
  localStorage.removeItem(GUEST_KEY);
}

export function clearStoredPassword() {
  localStorage.removeItem(PASSWORD_KEY);
}

export function hasChosenGuest() {
  return localStorage.getItem(GUEST_KEY) === "true";
}

export function setChosenGuest() {
  localStorage.setItem(GUEST_KEY, "true");
}
