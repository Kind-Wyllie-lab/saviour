import { useState, useEffect } from "react";
import socket from "../../socket";

// Styling and components
import "./APACommands.css";

function APACommands( {modules} ) {
    // const [apaModule, setApaModule] = useState(null);
    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    apaModule ? console.log("APA Module Connected") : console.log("No APA module connected");
    apaModule ? console.log(apaModule.ip) : null;

    useEffect(() => {
        console.log("Doing something in APA Commands...");
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

    return (
        <>
            <h2>APA Commands</h2>
            <div className = "apacommands">
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
            </div>
        </>
    );
}

export default APACommands;