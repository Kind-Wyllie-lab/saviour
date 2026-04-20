import React from "react";
import "./Recording.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";

import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import NewSessionForm from "./NewSessionForm/NewSessionForm";
import SessionList from "./SessionList/SessionList";


function Recording() {
    const { moduleList } = useModules();
    const { sessionList } = useSessions();

    return (
        <div className="recording-page">
            <div className="recording-layout">
                <div className="recording-layout__form">
                    <NewSessionForm modules={moduleList} sessionList={sessionList} />
                </div>
                <div className="recording-layout__sessions">
                    <SessionList sessionList={sessionList} modules={moduleList} />
                </div>
                <div className="recording-layout__modules">
                    <ModuleList modules={moduleList} />
                </div>
            </div>
        </div>
    );
}

export default Recording;
