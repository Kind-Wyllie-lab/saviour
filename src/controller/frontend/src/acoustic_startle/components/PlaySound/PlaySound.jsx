import React, { useState, useEffect } from "react";
import socket from "/src/socket";
import "./PlaySound.css"

function PlaySound({ modules }) {
    // Filter only sound modules
    const soundModules = (modules || []).filter((m) => m.type === "sound");

    // Selected module ID
    const [selectedId, setSelectedId] = useState("");
    const [selectedFile, setSelectedFile] = useState("");

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

    // Get sound files
    const [soundFiles, setSoundFiles] = useState([]);

    useEffect(() => {
        if (!selectedId) return;

        socket.emit("list_sound_files", { module_id: selectedId });

        const handleUpdate = (data) => {
            console.log(data);
            setSoundFiles(data.sound_files);
            setSelectedFile(data.selected_file ?? "");
        };

        socket.on("list_sound_files", handleUpdate);
        
        return () => {
            socket.off("list_sound_files", handleUpdate);
        };
    }, [selectedId]); // Wait until selectedId loads

    // Update selection
    const handleFileChange = (e) => {
        setSelectedFile(e.target.value);
        socket.emit("change_sound_file", { module_id: selectedId, selected_file: e.target.value });
    };

    if (soundModules.length === 0) {
        return <p>No sound modules available.</p>;
    }

    return (
        <div className="play-sound card">
            <h2>Sound Player</h2>
            <label htmlFor="sound-module-select">Select Module</label>
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
            <label htmlFor="sound-file-select">Select Sound</label>
            <select
            id="sound-file-select"
            value={selectedFile}
            onChange={handleFileChange}
            >
           {soundFiles.map((f) => (
                <option key={f} value={f}>
                    {f}
                </option>
            ))}
            </select>
            <button onClick={handlePlay}>Play Sound</button>
        </div>
    );
}

export default PlaySound;
