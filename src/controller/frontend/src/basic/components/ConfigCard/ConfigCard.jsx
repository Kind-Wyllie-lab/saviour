// Simple element that builds the correct config card based on id / module object
import './ConfigCard.css';
import GenericConfigCard from "./GenericConfigCard/GenericConfigCard";
import CameraConfigCard from "./CameraConfigCard/CameraConfigCard";
import ControllerConfigCard from './ControllerConfigCard/ControllerConfigCard';

function ConfigCard({ id, module, clipboard, onCopy }) {
  if (id === "controller") {
    return <ControllerConfigCard />;
  } else if (module.type.includes("camera")) {
    return <CameraConfigCard id={id} module={module} clipboard={clipboard} onCopy={onCopy} />;
  } else {
    return <GenericConfigCard id={id} module={module} clipboard={clipboard} onCopy={onCopy} />;
  }
}

export default ConfigCard;
