import { useState, useEffect, useRef } from "react";
import socket from "../../../socket";

// Styling and components
import "./APACommands.css";

function APACommands( {modules} ) {
    const [shockState, setShockState] = useState(null);
    const [arduinoState, setArduinoState] = useState(null);
    const [spacePressed, setSpacePressed] = useState(false);
    // Persist arm state across page reloads within the same browser session
    const [shockerArmed, setShockerArmed] = useState(
        () => sessionStorage.getItem("apa_shocker_armed") === "1"
    );
    // Throttle rapid command emissions — 200 ms minimum between same command type
    const lastCmdTime = useRef({});

    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    // apaModule ? console.log("APA Module Connected") : console.log("No APA module connected");
    // apaModule ? console.log(apaModule.ip) : null;

    useEffect(() => {
        function onShockStartBeingDelivered() { setShockState("Started shocking"); }
        function onShockStopBeingDelivered()  { setShockState("Stopped shocking"); }
        function onArduinoState(data)          { setArduinoState(data.state); }

        socket.on('shock_started_being_delivered', onShockStartBeingDelivered);
        socket.on('shock_stopped_being_delivered', onShockStopBeingDelivered);
        socket.on('arduino_state', onArduinoState);

        return () => {
            socket.off('shock_started_being_delivered', onShockStartBeingDelivered);
            socket.off('shock_stopped_being_delivered', onShockStopBeingDelivered);
            socket.off('arduino_state', onArduinoState);
        };
    }, []);

    const emitCommand = (type) => {
        const now = Date.now();
        if (now - (lastCmdTime.current[type] ?? 0) < 200) return;
        lastCmdTime.current[type] = now;
        socket.emit("send_command", { type, module_id: apaModule?.id, params: {} });
    };

    const activateShock   = () => emitCommand("activate_shock");
    const deactivateShock = () => emitCommand("deactivate_shock");
    const startMotor      = () => emitCommand("start_motor");
    const stopMotor       = () => emitCommand("stop_motor");
    const resetPulses     = () => emitCommand("reset_pulse_counter");

    const toggleShockerArmed = () => {
        const next = !shockerArmed;
        sessionStorage.setItem("apa_shocker_armed", next ? "1" : "0");
        setShockerArmed(next);
    };

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.code === "Space" && !spacePressed && shockerArmed) {
                setSpacePressed(true);
                activateShock();
            }
        };

        const handleKeyUp = (event) => {
            if (event.code === "Space" && shockerArmed) {
                setSpacePressed(false);
                deactivateShock();
            }
        };

        window.addEventListener("keydown", handleKeyDown);
        window.addEventListener("keyup", handleKeyUp);

        return () => {
            window.removeEventListener("keydown", handleKeyDown);
            window.removeEventListener("keyup", handleKeyUp);
        };
    }, [spacePressed, shockerArmed]); // Put apaModule in the box when leaving dummy mode

    return (
        <div className="apa-commands">
            <h2>APA Commands</h2>
            {/* {shockState ? (
                <h3>Shock state: {shockState}</h3>
            ) : (
                <></>
            )} */}
            <div className="apa-state">
                {arduinoState? (
                    <div>
                        {arduinoState.shock_activated? (
                            <p>Shock Sequence Active</p>
                        ): (
                            <p>Shock Sequence Inactive</p>
                        )}
                        {arduinoState.grid_live? (
                            <p>Grid Live</p>
                        ) : (
                            <p>Grid Not Live</p>
                        )}
                        <p>Attempted Shocks {arduinoState.attempted_shocks}</p>
                        <p>Delivered Shocks {arduinoState.delivered_shocks}</p>
                        <p>Table RPM {arduinoState.rpm}</p>
                        {arduinoState.rotating? (
                            <p>Rotating</p>
                        ) : (
                            <p>Stationary</p>
                        )}
                        {arduinoState.speed_error && (
                            <p className="apa-state-warning">⚠ Motor {arduinoState.speed_error}</p>
                        )}
                    </div>
                ) : (
                    <p>Arduino not reporting state</p>
                )}
            </div>
            <div className = "apa-command-buttons">
                <button className="toggle-shocker" onClick={toggleShockerArmed} disabled={!apaModule}>
                    {shockerArmed ? "Disarm Shocker" : "Arm Shocker"}
                </button>
                <button
                    className="hold-to-shock"
                    onMouseDown={activateShock}
                    onMouseUp={deactivateShock}
                    onMouseLeave={deactivateShock}
                    // disabled={!apaModule}
                    disabled={!shockerArmed}
                    > 
                    Spacebar<br></br>
                    Hold to Shock
                </button>
                {/* <button
                    className="activate-shock"
                    onClick={activateShock}
                    disabled={!apaModule}>
                    Activate Shock
                </button>
                <button
                    className="deactivate-shock"
                    onClick={deactivateShock}
                    disabled={!apaModule}>
                    Deactivate Shock
                </button> */}
                <button
                    className="start_motor"
                    onClick={startMotor}
                    disabled={!apaModule}>
                    Start Motor
                </button>
                <button
                    className="stop_motor"
                    onClick={stopMotor}
                    disabled={!apaModule}>
                    Stop Motor
                </button>
                <button
                    className="reset_pulse_counter"
                    onClick={resetPulses}
                    disabled={!apaModule}>
                    Reset Pulse Counter
                </button>
            </div>
        </div>
    );
}

export default APACommands;
