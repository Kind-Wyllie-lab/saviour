import { useState, useEffect, useRef } from "react";
import socket from "/src/socket";
import "./ConnectionOverlay.css";

function ConnectionOverlay() {
  const [visible, setVisible] = useState(!socket.connected);
  const [attempt, setAttempt] = useState(0);
  const timerRef = useRef(null);

  useEffect(() => {
    const onDisconnect = () => {
      timerRef.current = setTimeout(() => setVisible(true), 2000);
    };
    const onConnect = () => {
      clearTimeout(timerRef.current);
      setVisible(false);
      setAttempt(0);
    };
    const onAttempt = (n) => setAttempt(n);

    socket.on("disconnect", onDisconnect);
    socket.on("connect", onConnect);
    socket.on("reconnect_attempt", onAttempt);

    return () => {
      socket.off("disconnect", onDisconnect);
      socket.off("connect", onConnect);
      socket.off("reconnect_attempt", onAttempt);
      clearTimeout(timerRef.current);
    };
  }, []);

  if (!visible) return null;

  return (
    <div className="conn-overlay">
      <div className="conn-overlay__card">
        <div className="conn-overlay__spinner" />
        <p className="conn-overlay__title">Connection lost</p>
        <p className="conn-overlay__sub">
          {attempt > 0 ? `Reconnecting… (attempt ${attempt})` : "Reconnecting…"}
        </p>
      </div>
    </div>
  );
}

export default ConnectionOverlay;
