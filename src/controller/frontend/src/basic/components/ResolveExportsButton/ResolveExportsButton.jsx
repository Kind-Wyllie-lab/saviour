import { useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./ResolveExportsButton.css";

/**
 * Clears a stopped session's unconfirmed-export count when nothing is
 * exporting for it (a count left behind by an export lost with a module
 * reboot, or a module that has since left the fleet). Asks first: it clears
 * the number, it doesn't move or check any files. Logged on the session.
 */
export default function ResolveExportsButton({ sessionName, count }) {
  const loggedIn = useIsLoggedIn();
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <button
        className="resolve-exports__btn"
        disabled={!loggedIn}
        title={loggedIn ? "Clear this count once you've checked the files are on the share"
          : "Login required for this action"}
        onClick={() => setConfirming(true)}
      >
        Mark resolved
      </button>
    );
  }
  return (
    <span className="resolve-exports__confirm">
      <span>
        Clear the {count} unconfirmed export{count !== 1 ? "s" : ""}? Only do this
        if the session&apos;s files are on the share; it doesn&apos;t move or check files.
      </span>
      <span className="resolve-exports__btns">
        <button
          className="resolve-exports__btn resolve-exports__btn--go"
          onClick={() => {
            socket.emit("resolve_pending_exports", { session_name: sessionName });
            setConfirming(false);
          }}
        >
          Mark resolved
        </button>
        <button className="resolve-exports__btn" onClick={() => setConfirming(false)}>Cancel</button>
      </span>
    </span>
  );
}
