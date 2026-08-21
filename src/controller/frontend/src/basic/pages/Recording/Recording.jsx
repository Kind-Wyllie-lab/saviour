import React, { useState } from "react";
import "./Recording.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";

import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import Drawer from "/src/basic/components/Drawer/Drawer";
import NewSessionForm from "./NewSessionForm/NewSessionForm";
import SessionList from "./SessionList/SessionList";


function Recording() {
    const { moduleList } = useModules();
    const { sessionList } = useSessions();
    const [drawerOpen, setDrawerOpen] = useState(false);

    return (
        <div className="recording-page">
            <div className="recording-layout">
                <SessionList
                    sessionList={sessionList}
                    modules={moduleList}
                    onNewSession={() => setDrawerOpen(true)}
                />
            </div>

            {/* Both the start-session form and the module readiness list live
                here rather than as permanent page furniture -- starting a
                session is a rare, deliberate action (especially for a
                long-running habitat deployment), and module readiness is
                only something an operator needs to check right around that
                moment, not something worth a permanent slot competing with
                the session list for space at every other visit. */}
            <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="New Session">
                <NewSessionForm modules={moduleList} sessionList={sessionList} />
                <ModuleList modules={moduleList} />
            </Drawer>
        </div>
    );
}

export default Recording;
