// Singleton socket connection to the Flask backend.
//
// When VITE_MOCK=1 (npm run dev:mock) this is a fake in-memory socket so the
// frontend runs with no controller -- see src/mock/. `import.meta.env.VITE_MOCK`
// is statically replaced at build time, so a real `npm run build` folds this
// to the `io(...)` branch and tree-shakes src/mock/* out entirely
// (socketMock.js has no module-level side effects).
import { io } from "socket.io-client";
import { getStoredPassword } from "/src/authStorage";
import { createMockSocket } from "/src/mock/socketMock";

const socket =
  import.meta.env.VITE_MOCK === "1"
    ? createMockSocket()
    : io(
        // Carry a remembered admin password on the very first connection
        // attempt (not just later reconnects) so a returning logged-in
        // browser doesn't briefly connect as a guest before re-authenticating.
        `${window.location.protocol}//${window.location.hostname}:5000`,
        { auth: { password: getStoredPassword() } }
      );

export default socket;
