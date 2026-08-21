import React, { useState } from "react";
import "./Recording.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import usePersistedState from "/src/hooks/usePersistedState";

import Drawer from "/src/basic/components/Drawer/Drawer";
import ReadinessSummary from "/src/basic/components/ReadinessSummary/ReadinessSummary";
import NewSessionForm from "./NewSessionForm/NewSessionForm";
import SessionList from "./SessionList/SessionList";


function RecordingOverview() {
    const { moduleList } = useModules();
    const { sessionList } = useSessions();
    const [drawerOpen, setDrawerOpen] = useState(false);

    // Lifted out of NewSessionForm so ReadinessSummary can scope itself to
    // the same target the operator is about to start, rather than always
    // showing the whole fleet.
    const [target, setTarget] = usePersistedState("saviour_session_form_target", "all");

    return (
        <div className="recording-page">
            <div className="recording-layout">
                <SessionList
                    sessionList={sessionList}
                    modules={moduleList}
                    onNewSession={() => setDrawerOpen(true)}
                />
            </div>

            {/* The start-session form and its readiness check live here
                rather than as permanent page furniture -- starting a
                session is a rare, deliberate action (especially for a
                long-running habitat deployment), not something worth a
                permanent slot competing with the session list for space
                at every other visit. */}
            <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="New Session">
                <NewSessionForm
                    modules={moduleList}
                    sessionList={sessionList}
                    target={target}
                    setTarget={setTarget}
                />
                <ReadinessSummary modules={moduleList} target={target} />
            </Drawer>
        </div>
    );
}

export default RecordingOverview;
