import { lazy, Suspense, useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import "./OfflineRunner.css";

// The game is a separate chunk, so it costs the main bundle nothing. It is
// fetched a few seconds after the page loads, while the controller (which
// serves it) is still reachable; opening it offline then needs no network.
const loadGame = () => import("./RunnerGame");
const RunnerGame = lazy(loadGame);
const PREFETCH_MS = 5000;

const OFFLINE_AFTER_MS = 4000; // brief blips (a restart) don't show the card
const SECRET = "squeak";

/**
 * A small easter egg. When this browser has lost the controller for a few
 * seconds, a corner card says so and offers a game while it reconnects;
 * typing "squeak" anywhere outside a text field opens the game too. It
 * doesn't block the page, and it can't touch the controller: it's purely
 * local. Mounted once from main.jsx.
 */
export default function OfflineRunner() {
  const [offline, setOffline] = useState(false);
  const [backOnline, setBackOnline] = useState(false);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const timer = useRef(null);
  const offlineRef = useRef(false);
  offlineRef.current = offline;

  useEffect(() => {
    const t = setTimeout(() => { loadGame().catch(() => {}); }, PREFETCH_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    let everConnected = socket.connected;
    const onDisconnect = () => {
      if (!everConnected) return;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => { setOffline(true); setBackOnline(false); }, OFFLINE_AFTER_MS);
    };
    const onConnect = () => {
      everConnected = true;
      clearTimeout(timer.current);
      if (offlineRef.current) {
        setBackOnline(true);
        setTimeout(() => setBackOnline(false), 4000);
      }
      setOffline(false);
      setDismissed(false);
    };
    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    return () => {
      clearTimeout(timer.current);
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
    };
  }, []);

  useEffect(() => {
    let typed = "";
    const onKey = (e) => {
      const el = e.target;
      if (el?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName)) return;
      if (e.key.length !== 1) return;
      typed = (typed + e.key.toLowerCase()).slice(-SECRET.length);
      if (typed === SECRET) { typed = ""; setOpen(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const showCard = (offline && !dismissed) || backOnline;
  if (!showCard && !open) return null;

  return (
    <div className="offline-runner" role="dialog" aria-label={open ? "Runner game" : "Connection status"}>
      {showCard && (
        <div className={`offline-runner__status${backOnline ? " is-back" : ""}`}>
          <span className="offline-runner__dot" />
          <span>
            {backOnline ? "Back in touch with the controller." : "Lost touch with the controller. Reconnecting…"}
          </span>
          {!open && !backOnline && (
            <button type="button" className="offline-runner__btn" onClick={() => setOpen(true)}>
              Play while you wait
            </button>
          )}
          {!open && !backOnline && (
            <button type="button" className="offline-runner__close" aria-label="Hide"
              onClick={() => setDismissed(true)}>×</button>
          )}
        </div>
      )}
      {open && (
        <div className="offline-runner__game">
          <button type="button" className="offline-runner__close offline-runner__close--game"
            aria-label="Close the game" onClick={() => setOpen(false)}>×</button>
          <Suspense fallback={<p className="runner-game__hint">Loading…</p>}>
            <RunnerGame onClose={() => setOpen(false)} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
