import { useEffect, useState } from "react";
import socket from "/src/socket";

export default function useExperimentTitle({ autoRequest = true } = {}) {
    const [experimentName, setExperimentName] = useState(""); // The experiment name 

    useEffect(() => {
        socket.emit("get_experiment_metadata");
    
        socket.on("experiment_metadata_response", (data) => {
          setExperimentName(data.experiment_name);
        });
    
        return () => {
          socket.off("modules_update"); // Unregister listener to prevent multiple listeners on component re-render or remount
          socket.off("experiment_metadata_response");
          // socket.off("update_module_readiness"); // As above
        };
      }, []);

    useEffect(() => {
    socket.on("experiment_metadata_updated", (data) => {
        if (data.experiment_name) {
        setExperimentName(data.experiment_name);
        }
    });

    // On mount, request latest metadata
    socket.emit("get_experiment_metadata");

    return () => socket.off("experiment_metadata_updated");
    }, []);
    
    return {
        experimentName
    };
}