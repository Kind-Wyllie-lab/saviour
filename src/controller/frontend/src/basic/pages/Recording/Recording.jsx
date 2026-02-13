import React from "react";
import "./Recording.css";

import useModules from "/src/hooks/useModules";


import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import RecordingSessions from "./RecordingSessions/RecordingSessions";


function Recording() {
    const { moduleList } = useModules();

    return (
        <div className="recording-page">
            <div className="recording-container">
                <RecordingSessions modules={moduleList} />
                <ModuleList modules={moduleList} />
            </div>
        </div>
    )
}

export default Recording;