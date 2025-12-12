// import React, { useState, useEffect } from "react";
import './ConfigCard.css';
import TTLConfigCard from "./TTLConfigCard/TTLConfigCard";
import GenericConfigCard from "./GenericConfigCard/GenericConfigCard";

function ConfigCard({ id, module }) {
  const config = module.config || {};

  // if (module.type === "ttl") {
  //   return <TTLConfigCard id={id} module={module} />;
  // }

  return <GenericConfigCard id={id} module={module} />;
}

export default ConfigCard;