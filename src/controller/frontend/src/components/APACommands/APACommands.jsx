import { useState, useEffect } from "react";
import socket from "../../socket";

// Styling and components
import "./APACommands.css";

function APACommands( {modules} ) {
    const [shockState, setShockState] = useState(null); // Will be updated by socketio event - indicates whether grid live, shock being delivered etc.
    const [arduinoState, setArduinoState] = useState(null); // State object from the apa rig

    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    // apaModule ? console.log("APA Module Connected") : console.log("No APA module connected");
    // apaModule ? console.log(apaModule.ip) : null;

    useEffect(() => {
        // Handle shock state changes
        function onShockStartBeingDelivered() {
            setShockState("Started shocking");
        }

        function onShockStopBeingDelivered() {
            setShockState("Stopped shocking");
        }

        function onArduinoState(data) {
            setArduinoState(data.state);
        }

        socket.on('shock_started_being_delivered', onShockStartBeingDelivered);
        socket.on('shock_stopped_being_delivered', onShockStopBeingDelivered);
        socket.on('arduino_state', (data) => {
            // console.log(data);
            onArduinoState(data);
        });

        return () => {
            socket.off('shock_started_being_delivered', onShockStartBeingDelivered);
            socket.off('shock_stopped_being_delivered', onShockStopBeingDelivered);
        }
    }, []);

    const activateShock = () => {
        socket.emit("send_command", {
            type: "activate_shock",
            module_id: apaModule.id,
            params: {},
        });
    };

    const deactivateShock = () => {
        socket.emit("send_command", {
            type: "deactivate_shock",
            module_id: apaModule.id,
            params: {},
        });
    };

    const startMotor = () => {
        socket.emit("send_command", {
            type: "start_motor",
            module_id: apaModule.id,
            params: {},
        });
    };

    const stopMotor = () => {
        socket.emit("send_command", {
            type: "stop_motor",
            module_id: apaModule.id,
            params: {},
        });
    };

    const resetPulses = () => {
        socket.emit("send_command", {
            type: "reset_pulse_counter",
            module_id: apaModule.id,
            params: {},
        })
    }

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
                    </div>
                ) : (
                    <p>Arduino not reporting state</p>
                )}
            </div>
            <div className = "apa-command-buttons">
                <button
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
                </button>
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
