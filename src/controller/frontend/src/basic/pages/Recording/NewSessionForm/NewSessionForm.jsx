import { useState } from "react";
import socket from "/src/socket";
import ExperimentMetadata from "../ExperimentMetadata/ExperimentMetadata";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import "./NewSessionForm.css";

function NewSessionForm({ modules }) {
  const [target, setTarget] = useState("all");
  const { experimentName } = useExperimentTitle();
  
  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return; // safety check

    console.log("Creating session for " + experimentName + " with target " + target);

    socket.emit("create_session", { target, session_name: experimentName });
    setTarget("all");
  };

  return (
    <div className="new-session-form card">
      <h2>New Recording Session</h2>

      {/* Experiment metadata */}
      <ExperimentMetadata experimentName={experimentName} />

      {/* Target selection */}
      <form onSubmit={handleSubmit}>
        <label>
          Target:
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="all">All Modules</option>
            {modules.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name || m.id}
              </option>
            ))}
          </select>
        </label>

        <button type="submit">Start Session</button>
      </form>
    </div>
  );
}

export default NewSessionForm;
