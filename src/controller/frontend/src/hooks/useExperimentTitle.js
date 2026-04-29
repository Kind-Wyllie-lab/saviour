import { useEffect, useState } from "react";
import socket from "/src/socket";

export default function useExperimentTitle({ autoRequest = true } = {}) {
    const [experimentName, setExperimentName] = useState("");
    const [experimenter, setExperimenter] = useState("");

    useEffect(() => {
        socket.emit("get_experiment_metadata");

        const handle = (data) => {
            if (data.experiment_name) setExperimentName(data.experiment_name);
            if (data.metadata?.experimenter !== undefined) setExperimenter(data.metadata.experimenter);
        };

        socket.on("experiment_metadata_response", handle);
        socket.on("experiment_metadata_updated", handle);

        return () => {
            socket.off("experiment_metadata_response", handle);
            socket.off("experiment_metadata_updated", handle);
        };
    }, []);

    return { experimentName, experimenter };
}