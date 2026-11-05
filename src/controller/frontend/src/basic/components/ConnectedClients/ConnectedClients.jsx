import { useState, useEffect, useRef } from "react";
import { useClients, groupByAddress } from "/src/clients";

import "./ConnectedClients.css";

function connectedFor(since, now) {
  const mins = Math.max(0, Math.floor((now - since) / 60));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  return hours < 48 ? `${hours} h` : `${Math.floor(hours / 24)} d`;
}

/**
 * "N connected" under the hostname in the Sidebar: how many browsers have
 * the UI open, with a list (address, guest/admin, tabs, how long) on click.
 * Highlighted when another admin is connected, the case the risky-action
 * warning (confirmIfOtherAdmins) is for.
 */
function ConnectedClients() {
  const clients = useClients();
  const groups = groupByAddress(clients);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (groups.length === 0) return null;
  const othersAdmin = groups.some((g) => g.admin && !g.isSelf);
  const now = Date.now() / 1000;

  return (
    <div className="connected-clients" ref={wrapRef}>
      <button
        type="button"
        className={`connected-clients-btn${othersAdmin ? " connected-clients-btn--admin" : ""}`}
        title={othersAdmin
          ? "Another admin is connected: click to see who"
          : "Browsers with this page open: click to see who"}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="connected-clients-dot" aria-hidden="true" />
        {groups.length} connected
      </button>
      {open && (
        <div className="connected-clients-menu" role="dialog" aria-label="Connected browsers">
          {groups.map((g) => (
            <div key={g.ip} className="connected-clients-row">
              <span className="connected-clients-ip">
                {g.ip}
                {g.isSelf && <span className="connected-clients-self"> (this browser)</span>}
                {g.viaProxy && !g.isSelf && <span className="connected-clients-self"> (via proxy)</span>}
              </span>
              <span className="connected-clients-meta">
                <span className={g.admin ? "connected-clients-role--admin" : undefined}>
                  {g.admin ? "admin" : "guest"}
                </span>
                {g.tabs > 1 && ` · ${g.tabs} tabs`}
                {` · ${connectedFor(g.since, now)}`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default ConnectedClients;
