// import React, { useState, useEffect } from "react";
import './ConfigCard.css';
import TTLConfigCard from "../TTLConfigCard/TTLConfigCard";
import GenericConfigCard from "../GenericConfigCard/GenericConfigCard";

function ConfigCard({ id, module }) {
  const config = module.config || {};

  if (module.type === "ttl") {
    return <TTLConfigCard id={id} config={config} />;
  }

  return <GenericConfigCard id={id} config={config} />;
}

export default ConfigCard;
