import React, { useState, useEffect } from "react";
import "./Recording.css";


import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";

import ExperimentMetadata from "/src/habitat/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/habitat/components/CommandsPanel/CommandsPanel";


function Recording() {
    const { experimentName } = useExperimentTitle();
    const { moduleList } = useModules();

    return (
        <div className="recording-page">
            <ExperimentMetadata experimentName={experimentName} />
            <CommandsPanel experimentName={experimentName} modules={moduleList} />
        </div>
    )
}

export default Recording;