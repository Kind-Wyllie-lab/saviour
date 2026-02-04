import React, { useState, useEffect } from "react";
import socket from "/src/socket";
import "./PlaySound.css"

function PlaySound({ modules }) {
    // Filter only sound modules
    const soundModules = (modules || []).filter((m) => m.type === "sound");

    // Selected module ID
    const [selectedId, setSelectedId] = useState("");

    // Update selectedId when modules load or change
    useEffect(() => {
        if (soundModules.length > 0 && !soundModules.find(m => m.id === selectedId)) {
        setSelectedId(soundModules[0].id);
        }
    }, [soundModules, selectedId]);


    // Update selection
    const handleSelectChange = (e) => {
        setSelectedId(e.target.value);
    };

    // Example: function to play sound on the selected module
    const handlePlay = () => {
        if (!selectedId) return;
        console.log("Playing sound on module", selectedId);
        socket.emit("play_sound", { module_id: selectedId });
    };

    if (soundModules.length === 0) {
        return <p>No sound modules available.</p>;
    }

    return (
        <div className="play-sound card">
            <h2>Sound Player</h2>
            <label htmlFor="sound-module-select">Select Sound Module:</label>
            <select
            id="sound-module-select"
            value={selectedId}
            onChange={handleSelectChange}
            >
            {soundModules.map((m) => (
                <option key={m.id} value={m.id}>
                {m.name || m.id}
                </option>
            ))}
            </select>
            <button onClick={handlePlay}>Play Sound</button>
        </div>
    );
}

export default PlaySound;
