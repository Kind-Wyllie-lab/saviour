import React, { useEffect } from "react";

import "./AcousticStartleDashboard.css";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import PlaySound from "/src/acoustic_startle/components/PlaySound/PlaySound";

import useModules from "/src/hooks/useModules";
import socket from "/src/socket";

const STREAM_PORTS = { camera: 8080, ttl: 8082 };

function Dashboard() {
  const { moduleList } = useModules();

  useEffect(() => {
    socket.emit("get_module_configs");
  }, []);

  const cameraModules = (moduleList || []).filter((m) => m.type?.includes("camera"));
  const ttlModules    = (moduleList || []).filter((m) => m.type === "ttl");

  return (
    <main className="dashboard">
      <div className="dashboard-left">
        {cameraModules.map((m) => (
          <MJPEGStreamCard
            key={m.id}
            ip={m.ip}
            port={STREAM_PORTS.camera}
            label={m.name}
            isRecording={m.status === "RECORDING"}
          />
        ))}
      </div>
      <div className="dashboard-right">
        <ModuleList modules={moduleList} />
        <PlaySound modules={moduleList} />
        {ttlModules.map((m) => (
          <MJPEGStreamCard
            key={m.id}
            ip={m.ip}
            port={STREAM_PORTS.ttl}
            label={`${m.name} — TTL`}
            isRecording={m.status === "RECORDING"}
          />
        ))}
      </div>
    </main>
  );
}

export default Dashboard;
