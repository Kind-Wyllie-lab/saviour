import { useEffect, useState } from "react";
import socket from "/src/socket";

export default function useExperimentTitle({ autoRequest = true } = {}) {
    const [experimentName, setExperimentName] = useState("");

    useEffect(() => {
        socket.emit("get_experiment_metadata");

        const handleResponse = (data) => {
            if (data.experiment_name) setExperimentName(data.experiment_name);
        };
        const handleUpdated = (data) => {
            if (data.experiment_name) setExperimentName(data.experiment_name);
        };

        socket.on("experiment_metadata_response", handleResponse);
        socket.on("experiment_metadata_updated", handleUpdated);

        return () => {
            socket.off("experiment_metadata_response", handleResponse);
            socket.off("experiment_metadata_updated", handleUpdated);
        };
    }, []);

    return { experimentName };
}