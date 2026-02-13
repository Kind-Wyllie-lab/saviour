import "./RecordingSessions.css";

import useSessions from "/src/hooks/useSessions";
import NewSessionForm from "../NewSessionForm/NewSessionForm";
import SessionList from "../SessionList/SessionList";

function RecordingSessions({ modules }) {
    const { sessionList } = useSessions();

    return (
        <div className="card">
            <h2>Recording Sessions</h2>
            <div className="recording-sessions">
                <div className="recording-sessions-left">
                    <SessionList sessionList={sessionList} />
                    <NewSessionForm modules={modules} />
                </div>
                <div className="recording-sessions-right">
                    {/* <CommandsPanel experimentName={experimentName} modules={modules} /> */}
                </div>
            </div>

        </div>
    )
}

export default RecordingSessions;