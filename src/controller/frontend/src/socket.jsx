// Helper class to give access to websocket connection to flask backend (Singleton socket connection)
import { io } from "socket.io-client";
import { getStoredPassword } from "/src/authStorage";

// Carry a remembered admin password on the very first connection attempt
// (not just later reconnects) so a returning logged-in browser doesn't
// briefly connect as a guest before re-authenticating.
const socket = io(
    `${window.location.protocol}//${window.location.hostname}:5000`,
    { auth: { password: getStoredPassword() } }
);

export default socket;