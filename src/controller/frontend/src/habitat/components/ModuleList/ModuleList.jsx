import React from "react";
import "./ModuleList.css";


function ModuleList({ modules }) {

    for (var i in modules) {
        module = modules[i];
        console.log(module);
    }

    return (
        <div className="module-list-container">
            <h2>Module List</h2>
            <div className="module-list">
            {modules.map((module, idx) => (
                // <div className={`module-list-item ${module.status.toLowerCase()}`}>
                <div className="module-list-item">
                    <div className={`status-icon ${module.status.toLowerCase()}`}></div>
                    <p>{module.name} ({module.type}) - {module.status}</p>
                </div>
            ))}
            </div>
        </div>

    )
}

export default ModuleList;