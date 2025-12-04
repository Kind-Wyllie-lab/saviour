import { useState, useEffect } from "react";
import socket from "../../socket";

// Styling and components
import "./APACommands.css";

function APACommands( {modules} ) {
    const [shockState, setShockState] = useState(null); // Will be updated by socketio event - indicates whether grid live, shock being delivered etc.
    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    apaModule ? console.log("APA Module Connected") : console.log("No APA module connected");
    apaModule ? console.log(apaModule.ip) : null;

    useEffect(() => {
        // Handle shock state changes
        function onShockStartBeingDelivered() {
            setShockState("Shock being delivered");
        }

        function onShockStopBeingDelivered() {
            setShockState("Shock no longer being delivered");
        }

        socket.on('shock_started_being_delivered', onShockStartBeingDelivered);
        socket.on('shock_stopped_being_delivered', onShockStopBeingDelivered);

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
            {shockState ? (
                <h3>Shock state: {shockState}</h3>
            ) : (
                <></>
            )}
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