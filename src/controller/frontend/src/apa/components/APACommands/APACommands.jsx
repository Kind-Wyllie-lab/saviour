import { useState, useEffect, useRef, useSyncExternalStore } from "react";
import socket from "../../../socket";
import useSessions from "/src/hooks/useSessions";

// Styling and components
import "./APACommands.css";

// Shock arm/hold state is shared by every mounted APACommands: the fullscreen
// overlay mounts a second copy while the dashboard's stays mounted beneath it,
// and both listen for the spacebar. Per-instance state let a disarm in one
// leave the other armed, and one keypress send two activates.
const ARMED_KEY = "apa_shocker_armed";
let sharedArmed = false;
try {
    // Persist arm state across page reloads within the same browser session
    sharedArmed = sessionStorage.getItem(ARMED_KEY) === "1";
} catch { /* storage unavailable: start disarmed */ }
const armedSubscribers = new Set();
const subscribeArmed = (fn) => { armedSubscribers.add(fn); return () => armedSubscribers.delete(fn); };
const getArmed = () => sharedArmed;
function setSharedArmed(next) {
    sharedArmed = next;
    try { sessionStorage.setItem(ARMED_KEY, next ? "1" : "0"); } catch { /* ignore */ }
    armedSubscribers.forEach((fn) => fn());
}
// True while a spacebar or hold-button press is holding the shock on.
// `refresh` re-sends activate_shock while held: the module stops the shock
// when the refreshes stop (SHOCK_LEASE_S in shock.py), so a socket drop
// mid-hold can't leave it on even though the release never arrives.
const shockHold = { holding: false, refresh: null };
const SHOCK_REFRESH_MS = 250;

// A space typed into a form field is text, not the shock key.
const isTextEntry = (el) =>
    el instanceof HTMLElement &&
    (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

function APACommands( {modules} ) {
    const { sessionList } = useSessions();
    const activeSessions = sessionList.filter(s => s.state === "active" || s.state === "error");
    const [confirmStop, setConfirmStop] = useState(null); // session_name | null

    const [, setShockState] = useState(null); // display is commented out below
    const [arduinoState, setArduinoState] = useState(null);
    const shockerArmed = useSyncExternalStore(subscribeArmed, getArmed);
    // Throttle rapid command emissions — 200 ms minimum between same command type
    const lastCmdTime = useRef({});

    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    // apaModule ? console.log("APA Module Connected") : console.log("No APA module connected");
    // apaModule ? console.log(apaModule.ip) : null;

    useEffect(() => {
        function onShockStartBeingDelivered() { setShockState("Started shocking"); }
        function onShockStopBeingDelivered()  { setShockState("Stopped shocking"); }
        function onArduinoState(data)          { setArduinoState(data.state); }

        socket.on('shock_started_being_delivered', onShockStartBeingDelivered);
        socket.on('shock_stopped_being_delivered', onShockStopBeingDelivered);
        socket.on('arduino_state', onArduinoState);

        return () => {
            socket.off('shock_started_being_delivered', onShockStartBeingDelivered);
            socket.off('shock_stopped_being_delivered', onShockStopBeingDelivered);
            socket.off('arduino_state', onArduinoState);
        };
    }, []);

    // Latest module id for the window listeners, which are bound once (see
    // below) rather than re-bound on every render.
    const moduleIdRef = useRef(apaModule?.id);
    useEffect(() => { moduleIdRef.current = apaModule?.id; });

    const emitCommand = (type, { throttle = true } = {}) => {
        const now = Date.now();
        if (throttle && now - (lastCmdTime.current[type] ?? 0) < 200) return;
        lastCmdTime.current[type] = now;
        socket.emit("send_command", { type, module_id: moduleIdRef.current, params: {} });
    };

    // deactivate_shock is never throttled: dropping it (e.g. a quick
    // tap-tap of the spacebar) would leave the shock sequence running.
    const activateShock   = () => emitCommand("activate_shock");
    const deactivateShock = () => emitCommand("deactivate_shock", { throttle: false });
    const startMotor      = () => emitCommand("start_motor");
    const stopMotor       = () => emitCommand("stop_motor");
    const resetPulses     = () => emitCommand("reset_pulse_counter");

    const pressShock = () => {
        if (shockHold.holding || !getArmed()) return;
        shockHold.holding = true;
        activateShock();
        shockHold.refresh = setInterval(activateShock, SHOCK_REFRESH_MS);
    };

    // Ends a hold whatever the arm state is now, so disarming mid-hold or
    // losing focus can't strand the shock on.
    const releaseShock = () => {
        if (!shockHold.holding) return;
        shockHold.holding = false;
        clearInterval(shockHold.refresh);
        shockHold.refresh = null;
        deactivateShock();
    };

    const toggleShockerArmed = () => {
        const next = !getArmed();
        setSharedArmed(next);
        if (!next) releaseShock();
    };

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.code !== "Space" || !getArmed() || isTextEntry(event.target)) return;
            // Stop space scrolling the page or clicking a focused button
            // (e.g. toggling Disarm, or Start Motor) while it is the shock key.
            event.preventDefault();
            if (!event.repeat) pressShock();
        };

        const handleKeyUp = (event) => {
            if (event.code !== "Space") return;
            if (shockHold.holding) event.preventDefault();
            releaseShock();
        };

        // The keyup never arrives if the window loses focus mid-hold
        // (alt-tab, clicking another window, a dialog), or the tab is hidden.
        const handleVisibility = () => {
            if (document.visibilityState === "hidden") releaseShock();
        };

        window.addEventListener("keydown", handleKeyDown);
        window.addEventListener("keyup", handleKeyUp);
        window.addEventListener("blur", releaseShock);
        document.addEventListener("visibilitychange", handleVisibility);

        return () => {
            window.removeEventListener("keydown", handleKeyDown);
            window.removeEventListener("keyup", handleKeyUp);
            window.removeEventListener("blur", releaseShock);
            document.removeEventListener("visibilitychange", handleVisibility);
            // Navigating away from the dashboard mid-hold.
            releaseShock();
        };
    // Bound once; everything it reads goes through refs or the shared state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <div className="apa-commands">
            <h2>APA Commands</h2>
            {/* {shockState ? (
                <h3>Shock state: {shockState}</h3>
            ) : (
                <></>
            )} */}
            <div className="apa-state">
                {arduinoState? (
                    <div>
                        {arduinoState.shock_activated? (
                            <p className="apa-state--shock-active">Shock Sequence Active</p>
                        ): (
                            <p>Shock Sequence Inactive</p>
                        )}
                        {arduinoState.grid_live? (
                            <p className="apa-state--grid-live">Grid Live</p>
                        ) : (
                            <p>Grid Not Live</p>
                        )}
                        <p>Attempted Shocks {arduinoState.attempted_shocks}</p>
                        <p>Delivered Shocks {arduinoState.delivered_shocks}</p>
                        <p>Table RPM {arduinoState.rpm}</p>
                        {arduinoState.rotating? (
                            <p>Rotating</p>
                        ) : (
                            <p>Stationary</p>
                        )}
                        {arduinoState.speed_error && (
                            <p className="apa-state-warning">⚠ Motor {arduinoState.speed_error}</p>
                        )}
                    </div>
                ) : (
                    <p>Arduino not reporting state</p>
                )}
            </div>
            <div className = "apa-command-buttons">
                <button className="toggle-shocker" onClick={toggleShockerArmed} disabled={!apaModule}>
                    {shockerArmed ? "Disarm Shocker" : "Arm Shocker"}
                </button>
                <button
                    className="hold-to-shock"
                    onMouseDown={pressShock}
                    onMouseUp={releaseShock}
                    onMouseLeave={releaseShock}
                    // disabled={!apaModule}
                    disabled={!shockerArmed}
                    > 
                    Spacebar<br></br>
                    Hold to Shock
                </button>
                {/* <button
                    className="activate-shock"
                    onClick={activateShock}
                    disabled={!apaModule}>
                    Activate Shock
                </button>
                <button
                    className="deactivate-shock"
                    onClick={deactivateShock}
                    disabled={!apaModule}>
                    Deactivate Shock
                </button> */}
                <button
                    className="start_motor"
                    onClick={startMotor}
                    disabled={!apaModule}>
                    Start Motor
                </button>
                <button
                    className="stop_motor"
                    onClick={stopMotor}
                    disabled={!apaModule}>
                    Stop Motor
                </button>
                <button
                    className="reset_pulse_counter"
                    onClick={resetPulses}
                    disabled={!apaModule}>
                    Reset Pulse Counter
                </button>

                {activeSessions.length > 0 && (
                    <div className="apa-end-session-section">
                        {activeSessions.map(s => (
                            confirmStop === s.session_name ? (
                                <div key={s.session_name} className="apa-end-session-confirm">
                                    <span>End &ldquo;{s.session_name}&rdquo;?</span>
                                    <div className="apa-end-session-confirm-btns">
                                        <button
                                            className="apa-end-session-yes"
                                            onClick={() => { socket.emit("stop_session", { session_name: s.session_name }); setConfirmStop(null); }}
                                        >
                                            Yes, end
                                        </button>
                                        <button
                                            className="apa-end-session-cancel"
                                            onClick={() => setConfirmStop(null)}
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <button
                                    key={s.session_name}
                                    className="apa-end-session-btn"
                                    onClick={() => setConfirmStop(s.session_name)}
                                >
                                    End Session{activeSessions.length > 1 ? `: ${s.session_name}` : ""}
                                </button>
                            )
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

export default APACommands;
