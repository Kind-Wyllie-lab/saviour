import { useState } from "react";
import socket from "/src/socket";
import useModules from "/src/hooks/useModules";
import "./LoomStimulusControl.css";

// Manual fire/stop for the loom stimulus — mirrors the "Fire"/"Stop" test
// buttons on the loom camera's Settings > Stimulus tab, but reachable
// straight from the dashboard without navigating away.
function LoomStimulusControl() {
  const { moduleList } = useModules();
  const loomCam = moduleList.find((m) => m.type === "loom_camera");
  const [lastAction, setLastAction] = useState(null); // null | "fired" | "stopped"

  const sendCommand = (type, action) => {
    if (!loomCam) return;
    socket.emit("send_command", { module_id: loomCam.id, type, params: {} });
    setLastAction(action);
  };

  const fire = () => sendCommand("loom_stimulus_start", "fired");
  const stop = () => sendCommand("loom_stimulus_stop", "stopped");

  return (
    <div className="card loom-stimulus-control">
      <h3>Stimulus</h3>
      {loomCam ? (
        <>
          <div className="loom-stimulus-buttons">
            <button type="button" className="save-button" onClick={fire}>Fire</button>
            <button type="button" className="reset-button" onClick={stop}>Stop</button>
          </div>
          {lastAction && (
            <p className="loom-stimulus-status">
              {lastAction === "fired" ? "Fire command sent." : "Stop command sent."}
            </p>
          )}
        </>
      ) : (
        <p className="loom-stimulus-status loom-stimulus-status--muted">No loom camera connected.</p>
      )}
    </div>
  );
}

export default LoomStimulusControl;
