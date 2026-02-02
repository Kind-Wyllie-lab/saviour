// Simple element that builds the correct config card based on id / module object
import './ConfigCard.css';
import TTLConfigCard from "./TTLConfigCard/TTLConfigCard";
import GenericConfigCard from "./GenericConfigCard/GenericConfigCard";
import ControllerConfigCard from './ControllerConfigCard/ControllerConfigCard';

function ConfigCard({ id, module }) {
  if (id === "controller") {
    return <ControllerConfigCard />;
  } else {
    return <GenericConfigCard id={id} module={module} />;
  }
}

export default ConfigCard;