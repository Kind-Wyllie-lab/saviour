import { useState, useEffect, useRef, useSyncExternalStore } from "react";
import socket from "../../../socket";
import useSessions from "/src/hooks/useSessions";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";

// Styling and components
import "./APACommands.css";

// Shock mode lives on the controller (variants/apa/shock_automation.py) and
// arrives as apa_shock_state, so every tab and both mounted copies (the
// fullscreen overlay mounts a second one) show the same mode. It's mirrored
// in this module-level store because the spacebar listeners are bound once.
// The controller also refuses manual shocks outside manual mode, so this copy
// is only for the UI.
const MODES = [
    { id: "disarmed", label: "Disarmed" },
    { id: "manual",   label: "Manual" },
    { id: "auto",     label: "Auto" },
    { id: "dry_run",  label: "Dry run" },
];
let sharedMode = "disarmed";
const modeSubscribers = new Set();
const subscribeMode = (fn) => { modeSubscribers.add(fn); return () => modeSubscribers.delete(fn); };
const getMode = () => sharedMode;
// Spacebar / hold-to-shock: manual mode only (the controller enforces it too).
const getManual = () => sharedMode === "manual";
function setSharedMode(next) {
    if (sharedMode === next) return;
    sharedMode = next;
    modeSubscribers.forEach((fn) => fn());
}
// True while a spacebar or hold-button press is holding the shock on.
// `refresh` re-sends activate_shock while held: the module stops the shock
// when the refreshes stop (SHOCK_LEASE_S in shock.py), so a socket drop
// mid-hold can't leave it on even though the release never arrives.
const shockHold = { holding: false, refresh: null };
// Subscribable like the arm state, so every mounted copy shows the button
// pressed however the hold started (spacebar, mouse, touch).
const holdSubscribers = new Set();
const subscribeHold = (fn) => { holdSubscribers.add(fn); return () => holdSubscribers.delete(fn); };
const getHolding = () => shockHold.holding;
function setHolding(next) {
    shockHold.holding = next;
    holdSubscribers.forEach((fn) => fn());
}
const SHOCK_REFRESH_MS = 250;

// The module sends arduino_state every 0.2 s; after this long without one the
// panel stops presenting the last values as current.
const STATE_STALE_MS = 2000;

const fmt = (value, digits, unit) =>
    typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)} ${unit}` : "?";

// A space typed into a form field is text, not the shock key.
const isTextEntry = (el) =>
    el instanceof HTMLElement &&
    (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

function APACommands( {modules} ) {
    const { sessionList } = useSessions();
    const activeSessions = sessionList.filter(s => s.state === "active" || s.state === "error");
    const [confirmStop, setConfirmStop] = useState(null); // session_name | null
    const [confirmReset, setConfirmReset] = useState(false);

    const [arduinoState, setArduinoState] = useState(null);
    const [lastStateAt, setLastStateAt] = useState(null);
    const [now, setNow] = useState(() => Date.now());
    // From the shock_*_being_delivered events, reset by each state message's
    // shock_being_delivered so a missed event can't latch the banner.
    const [deliveringEvent, setDeliveringEvent] = useState(false);
    const mode = useSyncExternalStore(subscribeMode, getMode);
    const shockerArmed = mode !== "disarmed";
    const [shockState, setShockState] = useState(null);
    const holding = useSyncExternalStore(subscribeHold, getHolding);
    const loggedIn = useIsLoggedIn();
    // Throttle rapid command emissions — 200 ms minimum between same command type
    const lastCmdTime = useRef({});

    const apaModule = modules.filter((m) => m.type === "apa_arduino")[0];
    const moduleOnline = !!apaModule && apaModule.online !== false && apaModule.status !== "OFFLINE";
    const canControl = loggedIn && moduleOnline;

    const stateAgeMs = lastStateAt == null ? null : now - lastStateAt;
    const stale = stateAgeMs != null && stateAgeMs > STATE_STALE_MS;
    const live = arduinoState && !stale ? arduinoState : null;
    const delivering = !!live && (live.shock_being_delivered || deliveringEvent);
    const capReached = !!live && live.max_shocks != null && live.attempted_shocks >= live.max_shocks;
    const sessionRunning = activeSessions.length > 0;

    // Why each button is disabled (null = enabled), shown as its tooltip.
    // Stop Motor is never disabled by table state: the state may be stale or
    // wrong, and a stop must always get through.
    const notAllowed = !loggedIn ? "Login required for this action"
        : !moduleOnline ? "APA Arduino module offline" : null;
    const holdReason = notAllowed
        ?? (mode !== "manual" ? "Manual shocks only in Manual mode"
            : capReached ? "Shock limit reached: reset the counter between sessions" : null);
    const startReason = notAllowed ?? (live?.rotating ? "Table already rotating" : null);
    const resetReason = notAllowed
        ?? (sessionRunning ? "Can't reset the counter during a session"
            : live?.shock_activated ? "Can't reset the counter while a shock sequence is active" : null);

    // A confirm left open must not outlive the conditions that allowed it.
    useEffect(() => {
        if (resetReason) setConfirmReset(false);
    }, [resetReason]);

    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 500);
        return () => clearInterval(id);
    }, []);

    useEffect(() => {
        function onShockStartBeingDelivered() { setDeliveringEvent(true); }
        function onShockStopBeingDelivered()  { setDeliveringEvent(false); }
        function onArduinoState(data) {
            setArduinoState(data.state);
            setLastStateAt(Date.now());
            // Same PUB socket as the events, so the state is never older
            // than the last event: it also recovers a missed stop.
            setDeliveringEvent(!!data.state?.shock_being_delivered);
        }

        function onShockState(data) {
            setShockState(data);
            setSharedMode(data.mode);
            // Left manual elsewhere (another tab, Stop all, session end): end a hold.
            if (data.mode !== "manual") releaseShock();
        }
        function requestShockState() { socket.emit("get_apa_shock_state"); }

        socket.on('shock_started_being_delivered', onShockStartBeingDelivered);
        socket.on('shock_stopped_being_delivered', onShockStopBeingDelivered);
        socket.on('arduino_state', onArduinoState);
        socket.on('apa_shock_state', onShockState);
        socket.on('connect', requestShockState);
        requestShockState();

        return () => {
            socket.off('apa_shock_state', onShockState);
            socket.off('connect', requestShockState);
            socket.off('shock_started_being_delivered', onShockStartBeingDelivered);
            socket.off('shock_stopped_being_delivered', onShockStopBeingDelivered);
            socket.off('arduino_state', onArduinoState);
        };
    // releaseShock only touches the shared hold store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Latest module id for the window listeners, which are bound once (see
    // below) rather than re-bound on every render.
    const moduleIdRef = useRef(apaModule?.id);
    const capReachedRef = useRef(capReached);
    useEffect(() => {
        moduleIdRef.current = apaModule?.id;
        capReachedRef.current = capReached;
    });

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
        // Same rule as the button: no new hold once the cap is reached.
        if (shockHold.holding || !getManual() || capReachedRef.current) return;
        setHolding(true);
        activateShock();
        shockHold.refresh = setInterval(activateShock, SHOCK_REFRESH_MS);
    };

    // Ends a hold whatever the arm state is now, so disarming mid-hold or
    // losing focus can't strand the shock on.
    const releaseShock = () => {
        if (!shockHold.holding) return;
        setHolding(false);
        clearInterval(shockHold.refresh);
        shockHold.refresh = null;
        deactivateShock();
    };

    const setMode = (next) => {
        if (next !== "manual") releaseShock();
        socket.emit("set_shock_mode", { mode: next });
    };

    // Never disabled: it can only make things safer.
    const stopAll = () => {
        releaseShock();
        socket.emit("stop_all_shocks");
    };

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.code !== "Space" || !getManual() || isTextEntry(event.target)) return;
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
        <div className={`apa-commands${shockerArmed ? " apa-commands--armed" : ""}`}>
            <h2>APA Commands</h2>
            {/* The armed state lives here, not in the Disarm button's colour:
                disarming is the safe action, so that button stays plain. */}
            {shockerArmed && (
                <div className="apa-armed-notice" role="status">
                    {mode === "manual" && "ARMED (manual): Spacebar delivers shock"}
                    {mode === "auto" && "ARMED (auto): shocks the rat in the zone"}
                    {mode === "dry_run" && "DRY RUN: logs would-be shocks, delivers none"}
                </div>
            )}
            {(mode === "auto" || mode === "dry_run") && shockState && (
                <AutoStatus state={shockState} dry={mode === "dry_run"} />
            )}
            <div className="apa-state">
                {!apaModule ? (
                    <p className="apa-state-alert">No APA Arduino module connected</p>
                ) : !moduleOnline ? (
                    <p className="apa-state-alert">APA Arduino module offline</p>
                ) : !arduinoState ? (
                    <div className="apa-state-alert">
                        <p>Arduino not reporting state</p>
                        <p className="apa-state-hint">
                            Check the module&apos;s hardware fault on the System page
                            (both Arduinos must be connected), and that the controller
                            runs the APA variant.
                        </p>
                    </div>
                ) : (
                    <>
                        {stale && (
                            <p className="apa-state-alert">
                                No update for {Math.floor(stateAgeMs / 1000)} s: values below are out of date
                            </p>
                        )}
                        {live && (
                            <div className={`apa-shock-banner${delivering ? " apa-shock-banner--delivering" : live.shock_activated ? " apa-shock-banner--active" : ""}`}>
                                {delivering ? "Shock being delivered" : live.shock_activated ? "Shock sequence active" : "No shock"}
                            </div>
                        )}
                        <dl className={`apa-state-grid${stale ? " apa-state-grid--stale" : ""}`}>
                            <dt>Current</dt>
                            <dd>{fmt(arduinoState.current_ma, 2, "mA")}</dd>
                            <dt>On / off</dt>
                            <dd>{fmt(arduinoState.time_on_s, 2, "s")} / {fmt(arduinoState.time_off_s, 2, "s")}</dd>
                            <dt>Shocks</dt>
                            <dd className={capReached ? "apa-state-warning" : undefined}>
                                {arduinoState.attempted_shocks}
                                {arduinoState.max_shocks != null && ` / ${arduinoState.max_shocks}`} attempted,{" "}
                                {arduinoState.delivered_shocks} delivered
                                {capReached && " (limit reached)"}
                            </dd>
                            <dt>Grid</dt>
                            <dd className={arduinoState.grid_live ? "apa-state--grid-live" : undefined}>
                                {arduinoState.grid_live ? "Live" : "Not live"}
                            </dd>
                            <dt>Table</dt>
                            <dd>
                                {arduinoState.rotating ? "Rotating" : "Stationary"} {fmt(arduinoState.rpm, 2, "rpm")}
                                {" "}(target: {fmt(arduinoState.target_rpm, 2, "rpm")})
                            </dd>
                        </dl>
                        {arduinoState.speed_error && (
                            <p className="apa-state-warning">⚠ Motor {arduinoState.speed_error}</p>
                        )}
                    </>
                )}
            </div>
            {!loggedIn && apaModule && (
                <p className="apa-state-hint">Log in to arm the shocker or drive the table.</p>
            )}
            <div className="apa-command-buttons">
                <div className="apa-command-group" role="group" aria-labelledby="apa-group-shocker">
                    <span className="apa-command-group-label" id="apa-group-shocker">Shocker</span>
                    <div className="apa-mode-select" role="radiogroup" aria-label="Shock mode">
                        {MODES.map((m) => (
                            <button
                                key={m.id}
                                type="button"
                                role="radio"
                                aria-checked={mode === m.id}
                                className={`apa-mode-btn${mode === m.id ? ` apa-mode-btn--on apa-mode-btn--${m.id}` : ""}`}
                                onClick={() => setMode(m.id)}
                                // Disarming is always allowed; arming needs a login.
                                disabled={m.id !== "disarmed" && !canControl}
                                title={m.id !== "disarmed" && !canControl ? notAllowed : undefined}
                            >
                                {m.label}
                            </button>
                        ))}
                    </div>
                    <button type="button" className="apa-stop-all" onClick={stopAll}
                        title="Stop any shock and disarm">
                        Stop all shocks
                    </button>
                    {/* Pointer events cover mouse, touch and pen; touch-action
                        (CSS) stops a held finger scrolling the page instead. */}
                    <button
                        className={`hold-to-shock${holding ? " hold-to-shock--pressed" : ""}`}
                        aria-pressed={holding}
                        onPointerDown={(e) => {
                            if (e.pointerType === "mouse" && e.button !== 0) return;
                            pressShock();
                        }}
                        onPointerUp={releaseShock}
                        onPointerLeave={releaseShock}
                        onPointerCancel={releaseShock}
                        onContextMenu={(e) => e.preventDefault()}
                        disabled={!!holdReason}
                        title={holdReason ?? undefined}
                    >
                        Spacebar<br></br>
                        Hold to Shock
                    </button>
                </div>

                <div className="apa-command-group" role="group" aria-labelledby="apa-group-table">
                    <span className="apa-command-group-label" id="apa-group-table">Table</span>
                    <div className="apa-command-row">
                        <button
                            className="start_motor"
                            onClick={startMotor}
                            disabled={!!startReason}
                            title={startReason ?? undefined}>
                            Start Motor
                        </button>
                        <button
                            className="stop_motor"
                            onClick={stopMotor}
                            disabled={!canControl}
                            title={notAllowed ?? undefined}>
                            Stop Motor
                        </button>
                    </div>
                </div>

                <div className="apa-command-group">
                    {confirmReset ? (
                        <div className="apa-confirm">
                            <span>
                                Reset the pulse counter
                                {arduinoState?.attempted_shocks != null &&
                                    ` (${arduinoState.attempted_shocks} attempted)`}?
                            </span>
                            <div className="apa-confirm-btns">
                                <button
                                    className="apa-confirm-yes"
                                    onClick={() => { resetPulses(); setConfirmReset(false); }}
                                >
                                    Yes, reset
                                </button>
                                <button
                                    className="apa-confirm-cancel"
                                    onClick={() => setConfirmReset(false)}
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    ) : (
                        <button
                            className="reset_pulse_counter apa-btn-secondary"
                            onClick={() => setConfirmReset(true)}
                            disabled={!!resetReason}
                            title={resetReason ?? undefined}>
                            Reset Pulse Counter
                        </button>
                    )}
                </div>

                {activeSessions.length > 0 && (
                    <div className="apa-end-session-section">
                        {activeSessions.map(s => (
                            confirmStop === s.session_name ? (
                                <div key={s.session_name} className="apa-confirm">
                                    <span>End &ldquo;{s.session_name}&rdquo;?</span>
                                    <div className="apa-confirm-btns">
                                        <button
                                            className="apa-confirm-yes"
                                            onClick={() => { socket.emit("stop_session", { session_name: s.session_name }); setConfirmStop(null); }}
                                        >
                                            Yes, end
                                        </button>
                                        <button
                                            className="apa-confirm-cancel"
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

// Live view of the controller's auto decision: what it sees and why it is or
// isn't shocking.
function AutoStatus({ state, dry }) {
    const { settings = {} } = state;
    let line;
    if (!state.session_recording) line = "Waiting for a recording session: no automated shocks until one starts";
    else if (!state.zone_report_fresh) line = "No zone report from the APA camera: is tracking on?";
    else if (state.capped) line = `Per-minute limit reached (${state.pulses_last_min}/${settings.max_pulses_per_min} pulses)`;
    else if (state.shocking) line = dry ? "Would be shocking: rat in zone" : "Shocking: rat in zone";
    else if (state.in_zone) line = `Rat in zone ${state.in_zone_s.toFixed(1)} s (shock after ${settings.entry_latency_s} s)`;
    else line = "Rat outside the zone";
    const ev = state.last_event;
    return (
        <div className={`apa-auto-status${state.shocking ? " apa-auto-status--shocking" : ""}`} role="status">
            <div>{line}</div>
            {ev && (
                <div className="apa-auto-status__last">
                    Last: {ev.event.replaceAll("_", " ")}{ev.reason ? ` (${ev.reason})` : ""}
                    {" "}at {new Date(ev.time_utc).toLocaleTimeString()}
                </div>
            )}
        </div>
    );
}

export default APACommands;
