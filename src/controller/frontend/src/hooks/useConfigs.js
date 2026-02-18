import { useEffect, useState } from "react";
import socket from "../socket";

// A hook to get configs.
// Controller config is returned by a socket event controller_config_response
// Module configs are a bit different - emitted socket event sends command to modules to get their configs.
// When they respond, controller modules object updates its representation of their configs. 

export default function useConfigs({ autoRequest = true } = {}) {
  const [controllerConfig, setControllerConfig] = useState({});

  useEffect(() => {
    if (autoRequest) {
      socket.emit("get_controller_config");
    }

    socket.on("controller_config_response", (data) => {
      setControllerConfig(data);
    });


    return () => {
      socket.off("controller_config_response");
    };
  }, [autoRequest]);


  useEffect(() => {
    if (autoRequest) {
      socket.emit("get_module_configs");
    }

    // No need to return anything
  }, [autoRequest]);


  return {
    controllerConfig
    // moduleConfigs,
    // moduleConfigsList: Object.values(moduleConfigs)
  };
}
