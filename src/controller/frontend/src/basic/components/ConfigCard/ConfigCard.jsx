// Simple element that builds the correct config card based on id / module object
import './ConfigCard.css';
import GenericConfigCard      from "./GenericConfigCard/GenericConfigCard";
import CameraConfigCard       from "./CameraConfigCard/CameraConfigCard";
import MicrophoneConfigCard   from "./MicrophoneConfigCard/MicrophoneConfigCard";
import ControllerConfigCard   from './ControllerConfigCard/ControllerConfigCard';

function ConfigCard({ id, module, clipboard, onCopy }) {
  if (id === "controller") {
    return <ControllerConfigCard />;
  }
  if (!module) {
    return <div className="config-card"><p style={{ padding: "12px", opacity: 0.5 }}>Loading module…</p></div>;
  }
  if (module.type?.includes("camera")) {
    return <CameraConfigCard id={id} module={module} clipboard={clipboard} onCopy={onCopy} />;
  }
  if (module.type?.includes("microphone")) {
    return <MicrophoneConfigCard id={id} module={module} clipboard={clipboard} onCopy={onCopy} />;
  }
  return <GenericConfigCard id={id} module={module} clipboard={clipboard} onCopy={onCopy} />;
}

export default ConfigCard;
