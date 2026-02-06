import React, { useEffect, useState } from "react";
import "./LivestreamSelector.css";

import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";

function LivestreamSelector({ modules }) {
    // Filter camera modules
    const cameraModules = (modules || []).filter(
        (m) => m.type === "camera"
    );

    const [selectedId, setSelectedId] = useState("");

    // Default to first camera when list loads/changes
    useEffect(() => {
        if (
            cameraModules.length > 0 &&
            !cameraModules.find((m) => m.id === selectedId)
        ) {
            setSelectedId(cameraModules[0].id);
        }
    }, [cameraModules, selectedId]);

    if (cameraModules.length === 0) {
        return <p>No camera modules available.</p>;
    }

    const selectedCamera = cameraModules.find(
        (m) => m.id === selectedId
    );

    return (
        <div className="livestream-selector card">
            <div className="livestream-selector-top">
                <h2>Livestream</h2>
                <select
                    id="camera-select"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                >
                    {cameraModules.map((m) => (
                        <option key={m.id} value={m.id}>
                            {m.name || m.id}
                        </option>
                    ))}
                </select>
            </div>
            {selectedCamera && (
                <LivestreamCard module={selectedCamera} />
            )}
        </div>
    );
}

export default LivestreamSelector;
