// LoginModal.js
import React, { useState, useEffect } from "react";
import socket from "../../socket";
import "./LoginModal.css";

function LoginModal({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    socket.on("login_success", () => {
      setError("");
      onSuccess(); // lift up success to parent
    });

    socket.on("login_error", (msg) => {
      setError(msg || "Invalid credentials");
    });

    return () => {
      socket.off("login_success");
      socket.off("login_error");
    };
  }, [onSuccess]);

  const handleLogin = () => {
    socket.emit("login", { username, password });
  };

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Login</h2>
        <input
          type="text"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="error">{error}</p>}
        <div className="modal-buttons">
          <button onClick={handleLogin}>Login</button>
        </div>
      </div>
    </div>
  );
}

export default LoginModal;
