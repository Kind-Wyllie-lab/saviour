import React from "react";
import ModuleCard from "../ModuleCard/ModuleCard";
import "./ModuleGrid.css";


function ModuleGrid({ modules }) {
    const requiredModules = [ // List of modules required to run the experiment. 
        { type: "apa_camera", label: "APA Top Camera" },
        { type: "apa_arduino", label: "APA Arduino (Shocker & Table Motor)" }
    ];
    
    const filledModules = requiredModules.map((req) => {
        const found = modules.find((module) => module.type === req.type); // Find connected modules that match required types
        return found || { ...req, placeholder: true};
    });

    // Include extra (non-required) modules that are connected
    const extraModules = modules.filter(
        (m) => !requiredModules.some((req) => req.type === m.type)
    );

    const allModules = [...filledModules, ...extraModules]; // Required modules, placeholders if not connected and actual modules if connected, and any additional non-required modules that are connected.


    return (
        <div className="module-grid">
          {allModules.map((module, idx) => (
            <ModuleCard key={idx} module={module} />
          ))}
        </div>
      );
}

export default ModuleGrid;