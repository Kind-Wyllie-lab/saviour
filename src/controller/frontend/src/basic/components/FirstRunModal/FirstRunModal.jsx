import { useEffect, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./FirstRunModal.css";

// The variant this frontend bundle was actually built for (saviour-config's
// build_frontend() writes VITE_VARIANT). Compared against the controller
// TYPE the backend reports so a wrong `saviour-config` selection is caught
// before an experiment runs.
const BUILT_VARIANT = import.meta.env.VITE_VARIANT || "basic";
const SNOOZE_KEY = "saviour_first_run_snooze";

// Self-contained: renders nothing until the backend says first-run setup is
// needed (a sentinel written by saviour-config on a role/type change). Drop
// <FirstRunModal /> into a variant App once; no per-App wiring.
export default function FirstRunModal() {
  const loggedIn = useIsLoggedIn();
  const [state, setState] = useState(null); // backend first_run_state payload
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [ackVariant, setAckVariant] = useState(false);
  const [prefilled, setPrefilled] = useState(false);
  const [status, setStatus] = useState(null); // null | "saving" | "error"
  const [error, setError] = useState("");
  const [snoozed, setSnoozed] = useState(
    () => sessionStorage.getItem(SNOOZE_KEY) === "1"
  );

  useEffect(() => {
    const onState = (payload) => {
      if (!payload?.needed) {
        setState(null); // completed (here or on another client)
        setStatus(null);
        return;
      }
      setState(payload);
      // Pre-fill from current config once, without clobbering edits in progress.
      setPrefilled((done) => {
        if (!done) {
          setName(payload.name || "");
          setLocation(payload.location || "");
        }
        return true;
      });
    };
    const onError = (payload) => {
      setStatus("error");
      setError(payload?.error || "Could not complete setup");
    };
    socket.on("first_run_state", onState);
    socket.on("first_run_error", onError);
    socket.emit("get_first_run_state");
    return () => {
      socket.off("first_run_state", onState);
      socket.off("first_run_error", onError);
    };
  }, []);

  if (!state?.needed || snoozed) return null;

  const provisioned = state.provisioned_type;
  const mismatch = provisioned && provisioned !== BUILT_VARIANT;
  const canSave =
    loggedIn && name.trim().length > 0 && ackVariant && status !== "saving";

  const save = () => {
    setStatus("saving");
    setError("");
    socket.emit("complete_first_run", {
      name: name.trim(),
      location: location.trim(),
    });
  };

  const snooze = () => {
    sessionStorage.setItem(SNOOZE_KEY, "1");
    setSnoozed(true);
  };

  return (
    <div className="modal-overlay">
      <div className="modal first-run-modal" onClick={(e) => e.stopPropagation()}>
        <h2>First-time controller setup</h2>
        <p className="modal-subtext">
          This controller was just configured
          {state.reason === "type-change" ? " for a new experiment type" : ""}.
          Give it a name and confirm it's set up for the right rig.
        </p>

        <div className="first-run-modal__field">
          <label htmlFor="fr-name">Controller name</label>
          <input
            id="fr-name"
            type="text"
            placeholder="e.g. Habitat rig A"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={status === "saving"}
          />
        </div>

        <div className="first-run-modal__field">
          <label htmlFor="fr-loc">Location (optional)</label>
          <input
            id="fr-loc"
            type="text"
            placeholder="e.g. Room 204"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            disabled={status === "saving"}
          />
        </div>

        <div
          className={`first-run-modal__variant${
            mismatch ? " first-run-modal__variant--warn" : ""
          }`}
        >
          <div className="first-run-modal__variant-row">
            <span>Experiment interface</span>
            <strong>{BUILT_VARIANT}</strong>
          </div>
          {provisioned && (
            <div className="first-run-modal__variant-row">
              <span>Provisioned type</span>
              <strong>{provisioned}</strong>
            </div>
          )}
          {mismatch && (
            <p className="first-run-modal__msg val--danger">
              The interface build ({BUILT_VARIANT}) doesn't match the
              provisioned type ({provisioned}). If this is wrong, re-run{" "}
              <code>sudo saviour-config</code> and pick the correct controller
              type — this screen can't change it.
            </p>
          )}
          <label className="first-run-modal__ack">
            <input
              type="checkbox"
              checked={ackVariant}
              onChange={(e) => setAckVariant(e.target.checked)}
              disabled={status === "saving"}
            />
            This controller is set up for the <strong>{BUILT_VARIANT}</strong>{" "}
            experiment.
          </label>
        </div>

        {!loggedIn && (
          <p className="first-run-modal__msg modal-subtext">
            Log in (top right) to complete first-time setup.
          </p>
        )}
        {status === "error" && (
          <p className="first-run-modal__msg val--danger">{error}</p>
        )}

        <div className="modal-buttons">
          <button className="reset-button" onClick={snooze} disabled={status === "saving"}>
            Later
          </button>
          <button className="save-button" onClick={save} disabled={!canSave}>
            {status === "saving" ? "Saving…" : "Save & finish"}
          </button>
        </div>
      </div>
    </div>
  );
}
