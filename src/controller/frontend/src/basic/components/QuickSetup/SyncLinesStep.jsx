import { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import SyncGuideModal from "/src/basic/components/ConfigCard/TTLConfigCard/SyncGuideModal";
import {
  EXTERNAL_SYNC, ROLES, CONFIRM_MAX_AGE_DAYS, syncApplication, syncModules, roleLabel, pinLabel,
  systemName,
} from "/src/basic/utils/ttlApplications";

// Quick setup's Sync lines step (plans/rig-quick-setup.md), shown when an
// external sync is chosen: each line of that kind's sync TTL modules, with
// Test line (runs it 5 s, the TTL card's test_pin) and "Seen in Open Ephys"
// (confirm_ttl_line, admin; same store as the TTL card's Lines tab,
// src/controller/ttl_lines.py). Editing the lines stays on the TTL card.

const STATE = {
  confirmed: { text: "Confirmed", cls: "ok" },
  stale: { text: `Confirmed over ${CONFIRM_MAX_AGE_DAYS} days ago`, cls: "warn" },
  changed: { text: "Wiring changed since confirmed", cls: "warn" },
  unconfirmed: { text: "Not confirmed", cls: "warn" },
};

function when(epochS) {
  return epochS ? new Date(epochS * 1000).toLocaleString([], {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}

export default function SyncLinesStep({ externalSync, ttlLines }) {
  const loggedIn = useIsLoggedIn();
  const [testing, setTesting] = useState({}); // "mid:gpio" -> "testing" | "done"
  const [guide, setGuide] = useState(false);
  const timers = useRef([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const app = syncApplication(externalSync);
  const system = systemName(app);
  const mods = syncModules(ttlLines, externalSync);

  const test = (mid, gpio) => {
    const k = `${mid}:${gpio}`;
    setTesting((t) => ({ ...t, [k]: "testing" }));
    socket.emit("send_command", { module_id: mid, type: "test_pin", params: { pin: Number(gpio), duration: 5 } });
    timers.current.push(setTimeout(() => {
      setTesting((t) => ({ ...t, [k]: "done" }));
      timers.current.push(setTimeout(() => setTesting((t) => { const n = { ...t }; delete n[k]; return n; }), 2000));
    }, 5000));
  };
  const confirm = (mid, gpio) => socket.emit("confirm_ttl_line", { module_id: mid, gpio: Number(gpio) });

  if (!ttlLines) return <p>Loading the sync lines…</p>;
  if (mods.length === 0) {
    return (
      <section>
        <p>
          No TTL module is set up for <strong>{EXTERNAL_SYNC[externalSync]?.label}</strong> sync. On a
          TTL module's settings card, set its application to “{EXTERNAL_SYNC[externalSync]?.label}” and
          add a sync line, then come back here.
        </p>
        <a className="quick-setup__btn" href="/settings" target="_blank" rel="noreferrer">Open settings ↗</a>
      </section>
    );
  }

  return (
    <section className="quick-setup__sync">
      <div className="quick-setup__guide-bar">
        <button type="button" className="quick-setup__btn quick-setup__btn--small" onClick={() => setGuide(true)}>
          What to expect ▸
        </button>
        <span className="quick-setup__hint">Photos of the wiring and an animation of the pulses arriving.</span>
      </div>
      {guide && (
        <SyncGuideModal app={app} onClose={() => setGuide(false)}
          lines={mods.flatMap((m) => (m.lines ?? []).filter((ln) => ROLES[ln.role]?.channel)
            .map((ln) => [ln.gpio, { role: ln.role, channel: ln.channel }]))} />
      )}
      {mods.map((m) => (
        <div key={m.id} className="quick-setup__sync-module">
          <div className="quick-setup__sync-head">
            <strong>{m.name}</strong>
            {m.online === false && <span className="quick-setup__badge quick-setup__badge--warn">Offline</span>}
            <a className="quick-setup__link" href={`/settings#${m.id}`} target="_blank" rel="noreferrer">
              Edit lines ↗
            </a>
          </div>
          {m.problems?.length > 0 && (
            <ul className="quick-setup__problems">{m.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          )}
          {(m.lines ?? []).length === 0 ? (
            <p className="quick-setup__hint">No lines yet: add the sync line on its settings card.</p>
          ) : (
            <table className="quick-setup__sync-table">
              <thead>
                <tr><th>Line</th><th>Role</th><th>Channel</th><th>State</th><th /></tr>
              </thead>
              <tbody>
                {m.lines.map((ln) => {
                  const st = STATE[ln.state] ?? STATE.unconfirmed;
                  const k = `${m.id}:${ln.gpio}`;
                  const output = ROLES[ln.role]?.output ?? true;
                  return (
                    <tr key={ln.gpio}>
                      <td>{pinLabel(ln.gpio)}</td>
                      <td>{roleLabel(ln.role)}</td>
                      <td>{ROLES[ln.role]?.channel ? (ln.channel || <em>not set</em>) : "–"}</td>
                      <td>
                        <span className={`quick-setup__badge quick-setup__badge--${st.cls}`}
                          title={ln.confirmed_at ? `Confirmed ${when(ln.confirmed_at)}` : undefined}>
                          {st.text}
                        </span>
                      </td>
                      <td className="quick-setup__sync-actions">
                        {output && (
                          <button type="button" className="quick-setup__btn quick-setup__btn--small"
                            disabled={!!testing[k] || m.online === false} onClick={() => test(m.id, ln.gpio)}
                            title="Run the line for 5 seconds so you can see it downstream">
                            {testing[k] === "testing" ? "Testing…" : testing[k] === "done" ? "Done" : "Test line"}
                          </button>
                        )}
                        {ROLES[ln.role]?.channel && (
                          <button type="button"
                            className={`quick-setup__btn quick-setup__btn--small${ln.state !== "confirmed" ? " quick-setup__btn--primary" : ""}`}
                            disabled={!loggedIn}
                            title={loggedIn ? `You saw this line's pulses in ${system}` : "Sign in as admin to confirm"}
                            onClick={() => confirm(m.id, ln.gpio)}>
                            {ln.state === "confirmed" ? "Seen again" : `Seen in ${system}`}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ))}
      {!loggedIn && <p className="quick-setup__hint">Sign in as admin to confirm lines.</p>}
      <p className="quick-setup__hint">
        Confirmations are kept on the controller and shared with the TTL card's Lines tab.
      </p>
    </section>
  );
}
