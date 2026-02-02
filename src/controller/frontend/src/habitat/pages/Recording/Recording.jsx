import React from "react";
import "./Recording.css";


import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";


import ExperimentMetadata from "/src/basic/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/basic/components/CommandsPanel/CommandsPanel";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";


function Recording() {
    const { experimentName } = useExperimentTitle();
    const { moduleList } = useModules();

    return (
        <div className="recording-page">
            <div className="recording-left">
                <ModuleList modules={moduleList} />
            </div>
            <div className="recording-right">
                <ExperimentMetadata experimentName={experimentName} />
                <CommandsPanel experimentName={experimentName} modules={moduleList} />
            </div>
        </div>
    )
}

export default Recording;