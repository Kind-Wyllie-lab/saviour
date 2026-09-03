import React, { useState, useEffect, useRef } from "react";
import { Outlet, useParams, useNavigate } from "react-router";
import "./Recording.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import usePersistedState from "/src/hooks/usePersistedState";

import Drawer from "/src/basic/components/Drawer/Drawer";
import ReadinessSummary from "/src/basic/components/ReadinessSummary/ReadinessSummary";
import NewSessionForm from "./NewSessionForm/NewSessionForm";
import HabitatSessionForm from "./HabitatSessionForm/HabitatSessionForm";
import SessionList from "./SessionList/SessionList";

const BUILT_VARIANT = import.meta.env.VITE_VARIANT || "basic";

// Persistent shell for both /recording routes -- a narrow session-list
// rail on the left (always visible, like a secondary nav), and the routed
// child (SessionDetailPage, or the "nothing selected" placeholder) filling
// the rest of the width on the right via <Outlet/>. Replaces the earlier
// full-page-takeover version of the detail view: the list and the detail
// are both visible together now, not one-or-the-other.
function RecordingLayout() {
    const { moduleList } = useModules();
    const { sessionList } = useSessions();
    const navigate = useNavigate();
    const [drawerOpen, setDrawerOpen] = useState(false);
    // Habitat builds open the drawer straight into the plan-based
    // "Habitat Session" form (the common case for that rig); a link at the
    // top switches to the standard single-strategy form. Other builds only
    // ever see the standard form.
    const [formMode, setFormMode] = useState(
        BUILT_VARIANT === "habitat" ? "habitat" : "standard",
    );
    const habitatForm = BUILT_VARIANT === "habitat" && formMode === "habitat";

    // Set by SessionDetailPage's "Copy" button (via the outlet context
    // below) to seed the New Session form with an existing session's
    // target/mode/duration/schedule -- null the rest of the time, so the
    // form's own persisted fields (last-used mode/duration/etc.) are what
    // show up when opening the drawer normally via "+ New Session".
    const [copyPrefill, setCopyPrefill] = useState(null);

    // useParams() here reflects the matched child route too (react-router
    // merges params up the whole matched tree), so this is populated with
    // the open session's name even though this component sits above the
    // route that actually declares :sessionName -- used to highlight the
    // corresponding row in the rail.
    const { sessionName: selectedSessionName } = useParams();

    // Land on the most recently created session (sessionList's insertion
    // order from the backend -- new sessions are only ever appended, so the
    // last entry is the newest regardless of state) rather than the blank
    // placeholder, so opening the Recording page shows something useful
    // immediately. One-shot (the ref, not a dependency on sessionList) --
    // this should only fire on initial load, not yank the operator to a
    // different session every time a new one is created while they're
    // deliberately sitting on the index route.
    const autoSelected = useRef(false);
    useEffect(() => {
        if (autoSelected.current || selectedSessionName) return;
        const names = Object.keys(sessionList);
        if (names.length === 0) return;
        autoSelected.current = true;
        navigate(`/recording/sessions/${encodeURIComponent(names[names.length - 1])}`, { replace: true });
    }, [sessionList, selectedSessionName, navigate]);

    // Lifted out of NewSessionForm so ReadinessSummary can scope itself to
    // the same target the operator is about to start, rather than always
    // showing the whole fleet.
    const [target, setTarget] = usePersistedState("saviour_session_form_target", "all");

    // "Copy" on the detail page: reopen the New Session drawer targeting
    // the same modules/group and mode/duration/schedule as an existing
    // session, so recreating a similar session doesn't mean reconfiguring
    // everything from scratch. Deliberately doesn't touch the session-name
    // fields (experiment/rat ID/etc, in SessionName) -- those already
    // persist globally across sessions on their own, and every session
    // name gets a fresh timestamp suffix regardless.
    const openCopyDrawer = (session) => {
        setTarget(session.target || "all");
        setCopyPrefill({
            mode: session.scheduled ? "scheduled" : session.duration_minutes ? "timed" : "immediate",
            durationMinutes: session.duration_minutes,
            scheduledStart: session.scheduled_start_time,
            scheduledEnd: session.scheduled_end_time,
            scheduledDays: session.scheduled_days,
        });
        setDrawerOpen(true);
    };

    return (
        <div className="recording-page">
            <div className="recording-layout recording-layout--split">
                <div className="recording-layout__rail">
                    <SessionList
                        sessionList={sessionList}
                        modules={moduleList}
                        onNewSession={() => setDrawerOpen(true)}
                        selectedSessionName={selectedSessionName}
                    />
                </div>
                <div className="recording-layout__detail">
                    <Outlet context={{ openCopyDrawer }} />
                </div>
            </div>

            {/* The start-session form and its readiness check live here
                rather than as permanent page furniture -- starting a
                session is a rare, deliberate action (especially for a
                long-running habitat deployment), not something worth a
                permanent slot competing with the rail for space at every
                other visit. */}
            <Drawer
                open={drawerOpen}
                onClose={() => { setDrawerOpen(false); setCopyPrefill(null); }}
                title="New Session"
            >
                {BUILT_VARIANT === "habitat" && (
                    <div className="recording-form-mode">
                        <span className="recording-form-mode__label">
                            {habitatForm
                                ? "Habitat Session — plan-based recording"
                                : "Standard session"}
                        </span>
                        <button
                            type="button"
                            className="recording-form-mode__switch"
                            onClick={() =>
                                setFormMode(habitatForm ? "standard" : "habitat")
                            }
                        >
                            {habitatForm
                                ? "Switch to standard session"
                                : "Switch to Habitat Session"}
                        </button>
                    </div>
                )}

                {habitatForm ? (
                    <HabitatSessionForm
                        modules={moduleList}
                        onSessionCreated={(sessionName) => {
                            setDrawerOpen(false);
                            navigate(`/recording/sessions/${encodeURIComponent(sessionName)}`);
                        }}
                    />
                ) : (
                    <>
                        <NewSessionForm
                            modules={moduleList}
                            sessionList={sessionList}
                            target={target}
                            setTarget={setTarget}
                            prefill={copyPrefill}
                            onSessionCreated={(sessionName) => {
                                setDrawerOpen(false);
                                setCopyPrefill(null);
                                navigate(`/recording/sessions/${encodeURIComponent(sessionName)}`);
                            }}
                        />
                        <ReadinessSummary modules={moduleList} target={target} />
                    </>
                )}
            </Drawer>
        </div>
    );
}

export default RecordingLayout;
