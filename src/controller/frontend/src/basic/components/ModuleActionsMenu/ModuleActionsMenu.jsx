import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import socket from "/src/socket";
import { confirmIfOtherAdmins } from "/src/clients";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./ModuleActionsMenu.css";

// Module types that need no hardware the scan can see (GPIO, or nothing).
const NO_DETECTABLE_HW = new Set(["ttl", "external_host"]);
// A module on older code never answers scan_hardware.
const SCAN_TIMEOUT_MS = 75000;

/**
 * ModuleActionsMenu — the module lifecycle actions (Update / Mend / Change
 * type / Restart / Reboot / Shutdown / Remove) shared between the System page's per-module
 * row and each module's config card header, so both places drive the same
 * commands through the same UI instead of maintaining separate copies.
 */
function ModuleActionsMenu({ id, name, isOnline }) {
  const loggedIn = useIsLoggedIn();
  const [showActions, setShowActions] = useState(false);
  const [stagedMeta, setStagedMeta] = useState(null);
  const [updateStatus, setUpdateStatus] = useState(null); // null | "updating" | { success, output }
  const [mendStatus, setMendStatus] = useState(null); // null | "mending" | { success, output }
  const [mendTarget, setMendTarget] = useState(false);
  const [restartTarget, setRestartTarget] = useState(false);
  const [rebootTarget, setRebootTarget] = useState(false);
  const [shutdownTarget, setShutdownTarget] = useState(false);
  const [shutdownState, setShutdownState] = useState(null); // null | "sent" | "acked"
  const [removeTarget, setRemoveTarget] = useState(false);
  const [typeTarget, setTypeTarget] = useState(false);
  const [moduleTypes, setModuleTypes] = useState([]);
  const [newType, setNewType] = useState("");
  // null | {stage: "started"|"success"|"error", output, new_module_id}
  const [retype, setRetype] = useState(null);
  // null while scanning | {ok, items, suggested, output} (module_hardware_scan)
  const [scan, setScan] = useState(null);
  const scanTimerRef = useRef(null);
  const shutdownTimerRef = useRef(null);
  // Module ids are "<type>_<last 4 of the MAC>".
  const currentType = id.replace(/_[0-9a-f]{4}$/i, "");

  useEffect(() => {
    const onTypes = (data) => setModuleTypes(data?.types ?? []);
    const onRetype = (data) => {
      if (data.module_id === id) setRetype(data);
    };
    const onScan = (data) => {
      if (data.module_id !== id) return;
      clearTimeout(scanTimerRef.current);
      setScan(data);
    };
    socket.on("module_types", onTypes);
    socket.on("module_type_change_result", onRetype);
    socket.on("module_hardware_scan", onScan);
    return () => {
      socket.off("module_types", onTypes);
      socket.off("module_type_change_result", onRetype);
      socket.off("module_hardware_scan", onScan);
      clearTimeout(scanTimerRef.current);
    };
  }, [id]);

  useEffect(() => {
    socket.emit("get_update_info");
    const onInfo = (data) => setStagedMeta(data?.staged ?? null);
    socket.on("update_info", onInfo);
    return () => socket.off("update_info", onInfo);
  }, []);

  useEffect(() => {
    const onResult = (data) => {
      if (data.module_id === id) setUpdateStatus({ success: data.success, output: data.output });
    };
    socket.on("module_update_result", onResult);
    return () => socket.off("module_update_result", onResult);
  }, [id]);

  useEffect(() => {
    const onMendResult = (data) => {
      if (data.module_id === id) setMendStatus({ success: data.success, rebootRequired: data.reboot_required, output: data.output });
    };
    socket.on("module_mend_result", onMendResult);
    return () => socket.off("module_mend_result", onMendResult);
  }, [id]);

  useEffect(() => {
    const onAck = ({ module_id }) => {
      if (module_id === id) setShutdownState("acked");
    };
    socket.on("module_shutdown_ack", onAck);
    return () => socket.off("module_shutdown_ack", onAck);
  }, [id]);

  // Reopen-login fallback: deploy_update_to_module/run_mend reject an
  // unauthenticated connection via the shared "auth_required" event (see
  // web.py's _require_auth) so the app-wide AuthGate reopens the login form.
  // Without this, this row's "updating"/"mending" badge would spin forever,
  // since only module_update_result/module_mend_result used to clear it.
  useEffect(() => {
    const onAuthRequired = () => {
      setUpdateStatus(prev => (prev === "updating"
        ? { success: false, output: "Login required — please log in and retry" }
        : prev));
      setMendStatus(prev => (prev === "mending"
        ? { success: false, output: "Login required — please log in and retry" }
        : prev));
    };
    socket.on("auth_required", onAuthRequired);
    return () => socket.off("auth_required", onAuthRequired);
  }, []);

  // Clear shutdown state once the module actually drops offline; the 90s
  // fallback timer below covers the case where that never happens in this
  // browser session.
  useEffect(() => {
    if (!isOnline) {
      setShutdownState(null);
      clearTimeout(shutdownTimerRef.current);
    }
  }, [isOnline]);

  useEffect(() => () => clearTimeout(shutdownTimerRef.current), []);

  const handleUpdate = () => {
    if (!confirmIfOtherAdmins("Update and restart this module")) return;
    setUpdateStatus("updating");
    setShowActions(false);
    socket.emit("deploy_update_to_module", { module_id: id });
  };

  const handleMendConfirm = () => {
    setMendStatus("mending");
    socket.emit("send_command", { module_id: id, type: "run_mend", params: {} });
    setMendTarget(false);
  };

  const handleRestartConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "restart_service", params: {} });
    setRestartTarget(false);
  };

  const handleRebootConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "reboot", params: {} });
    setRebootTarget(false);
  };

  const handleShutdownConfirm = () => {
    socket.emit("send_command", { module_id: id, type: "shutdown", params: {} });
    setShutdownState("sent");
    setShutdownTarget(false);
    clearTimeout(shutdownTimerRef.current);
    shutdownTimerRef.current = setTimeout(() => setShutdownState(null), 90000);
  };

  const openTypeChange = () => {
    socket.emit("get_module_types");
    setScan(null);
    socket.emit("send_command", { module_id: id, type: "scan_hardware", params: {} });
    clearTimeout(scanTimerRef.current);
    scanTimerRef.current = setTimeout(() => setScan((s) => s ?? {
      ok: false, output: "no answer (is the module on the latest version?)" }), SCAN_TIMEOUT_MS);
    setNewType("");
    setTypeTarget(true);
    setShowActions(false);
  };

  const handleTypeConfirm = () => {
    if (!newType) return;
    if (!confirmIfOtherAdmins(`Change ${name} to a ${newType} module`)) return;
    setRetype({ stage: "started", output: "Preparing…" });
    socket.emit("send_command", { module_id: id, type: "change_module_type", params: { new_type: newType } });
    setTypeTarget(false);
  };

  const handleRemoveConfirm = () => {
    socket.emit("remove_module", { id });
    setRemoveTarget(false);
  };

  return (
    <span className="module-actions">
      {shutdownState ? (
        <span className="module-actions-shutdown-status">
          {shutdownState === "acked" ? "Powering off…" : "Shutting down…"}
        </span>
      ) : (
        <button type="button" className="action-menu-btn" onClick={() => setShowActions(true)}
          disabled={!loggedIn} title={loggedIn ? undefined : "Login required for this action"}>
          Actions ▾
        </button>
      )}
      {updateStatus && updateStatus !== "updating" && (
        <span className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
          {updateStatus.success ? "Updated" : `Failed: ${updateStatus.output}`}
        </span>
      )}
      {retype && (
        <span className={`config-sync-badge ${retype.stage === "error" ? "config-sync-badge--failed" : "config-sync-badge--pending"}`}
          title={retype.output}>
          {retype.stage === "error"
            ? `Type change failed: ${retype.output}`
            : retype.stage === "success"
              ? `Becoming ${retype.new_module_id}…`
              : "Changing type…"}
        </span>
      )}
      {mendStatus && mendStatus !== "mending" && (
        <span className={`config-sync-badge ${mendStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
          {!mendStatus.success
            ? `Failed: ${mendStatus.output}`
            : mendStatus.rebootRequired
              ? "Mended — reboot required"
              : "Mended"}
        </span>
      )}

      {/* Modals portal to <body>: the System page mounts this menu in a table
          cell, and an offline row's cell opacity would trap a fixed overlay
          in its stacking context (half-transparent, later rows painting over
          it and taking its clicks). */}
      {createPortal(<>
      {showActions && (
        <div className="modal-overlay" onClick={() => setShowActions(false)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">{name}</p>
            <div className="actions-modal__list">
              {isOnline ? (<>
                {stagedMeta && (
                  <button type="button" className="actions-modal__item" onClick={handleUpdate}>
                    <span>Update</span>
                    <span className="actions-modal__hint">Deploy staged package {stagedMeta.version ?? ""} to this module only</span>
                  </button>
                )}
                <button type="button" className="actions-modal__item"
                  onClick={() => { setMendTarget(true); setShowActions(false); }}>
                  <span>Run mend</span>
                  <span className="actions-modal__hint">Repairs dependencies/config and regenerates the service file, then restarts - takes a few minutes</span>
                </button>
                <button type="button" className="actions-modal__item" onClick={openTypeChange}>
                  <span>Change type</span>
                  <span className="actions-modal__hint">Reprovision this Pi as another module type (e.g. camera → RFID) - resets its settings</span>
                </button>
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRestartTarget(true); setShowActions(false); }}>
                  <span>Restart service</span>
                  <span className="actions-modal__hint">Restarts the SAVIOUR program - module does not reboot, reconnects automatically</span>
                </button>
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRebootTarget(true); setShowActions(false); }}>
                  <span>Reboot</span>
                  <span className="actions-modal__hint">Reboots the module - reconnects automatically</span>
                </button>
                <div className="actions-modal__divider" />
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setShutdownTarget(true); setShowActions(false); }}>
                  <span>Shutdown</span>
                  <span className="actions-modal__hint">Powers off - reconnects when switched back on</span>
                </button>
              </>) : (
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setRemoveTarget(true); setShowActions(false); }}>
                  <span>Remove</span>
                  <span className="actions-modal__hint">Remove offline module from tracking</span>
                </button>
              )}
            </div>
            <div className="modal-buttons" style={{ marginTop: "8px" }}>
              <button className="save-button" type="button" onClick={() => setShowActions(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {mendTarget && (
        <div className="modal-overlay" onClick={() => setMendTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Run mend on <strong>{name}</strong>?</p>
            <p className="modal-subtext">Rebuilds dependencies, regenerates the service file, and restarts the service. Takes a few minutes; the module will briefly go offline then reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleMendConfirm}>Run mend</button>
              <button className="save-button" type="button" onClick={() => setMendTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {typeTarget && (
        <div className="modal-overlay" onClick={() => setTypeTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Change <strong>{name}</strong> from <strong>{currentType}</strong> to:</p>
            <HardwareSummary scan={scan} />
            <select className="module-type-select" value={newType} onChange={e => setNewType(e.target.value)}>
              <option value="">Choose a type…</option>
              {typeGroups(moduleTypes, currentType, scan).map(([label, types]) => (
                <optgroup key={label} label={label}>
                  {types.map(t => (
                    <option key={t.type} value={t.type}>{t.name}{t.description ? ` - ${t.description}` : ""}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            {hardwareWarning(newType, moduleTypes, scan) && (
              <p className="module-type-warning">{hardwareWarning(newType, moduleTypes, scan)}</p>
            )}
            <p className="modal-subtext">
              The module installs anything the new type needs, then reconfigures and restarts
              as <strong>{newType ? `${newType}_${id.slice(-4)}` : "a new module id"}</strong>.
              Its settings reset to the new type&apos;s defaults (name and any box placement
              included), and its old entry here is replaced once it reconnects. Takes a minute
              or two, longer if packages need downloading. Not possible while it records.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" disabled={!newType} onClick={handleTypeConfirm}>Change type</button>
              <button className="save-button" type="button" onClick={() => setTypeTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {restartTarget && (
        <div className="modal-overlay" onClick={() => setRestartTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Restart service on <strong>{name}</strong>?</p>
            <p className="modal-subtext">The saviour service will restart. The module will briefly go offline then reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRestartConfirm}>Restart</button>
              <button className="save-button" type="button" onClick={() => setRestartTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {rebootTarget && (
        <div className="modal-overlay" onClick={() => setRebootTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Reboot <strong>{name}</strong>?</p>
            <p className="modal-subtext">The module will reboot and reconnect automatically. Any active recording will be interrupted.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRebootConfirm}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setRebootTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {shutdownTarget && (
        <div className="modal-overlay" onClick={() => setShutdownTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Shut down <strong>{name}</strong>?</p>
            <p className="modal-subtext">
              The module will power off. It will be re-added automatically when it comes back online.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleShutdownConfirm}>Shutdown</button>
              <button className="save-button" type="button" onClick={() => setShutdownTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {removeTarget && (
        <div className="modal-overlay" onClick={() => setRemoveTarget(false)}>
          <div className="modal actions-confirm-modal" onClick={e => e.stopPropagation()}>
            <p>Remove <strong>{name}</strong> from tracking?</p>
            <p className="modal-subtext">
              This module is offline and will be removed from the system. If it comes back online it will be re-added automatically.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRemoveConfirm}>Remove</button>
              <button className="save-button" type="button" onClick={() => setRemoveTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      </>, document.body)}
    </span>
  );
}

// What the module's hardware scan found (saviour-config --hardware-scan),
// recording hardware only.
function HardwareSummary({ scan }) {
  if (!scan) return <p className="module-hw-scan module-hw-scan--pending">Scanning this module&apos;s hardware…</p>;
  if (!scan.ok) return <p className="module-hw-scan">Couldn&apos;t scan its hardware: {scan.output}</p>;
  const found = (scan.items ?? []).filter((i) => i.present && !i.system);
  return (
    <p className="module-hw-scan">
      {found.length === 0
        ? "Found no recording hardware on this Pi."
        : <>Found: {found.map((i, n) => (
            <span key={i.label}>{n > 0 && " · "}<strong>{i.label}</strong> {i.value}</span>
          ))}</>}
    </p>
  );
}

// Types matching the scan first; every type stays selectable (hardware can be
// plugged in later, and some can't be detected).
function typeGroups(moduleTypes, currentType, scan) {
  const others = moduleTypes.filter((t) => t.type !== currentType);
  const suggested = scan?.ok ? scan.suggested ?? [] : [];
  if (suggested.length === 0) return [["All types", others]];
  const rank = (t) => suggested.indexOf(t.type);
  const match = others.filter((t) => rank(t) >= 0).sort((a, b) => rank(a) - rank(b));
  const rest = others.filter((t) => rank(t) < 0);
  return [["Matches this Pi's hardware", match], ["Other types", rest]].filter(([, ts]) => ts.length);
}

function hardwareWarning(newType, moduleTypes, scan) {
  if (!newType || !scan?.ok || NO_DETECTABLE_HW.has(newType)) return null;
  if ((scan.suggested ?? []).includes(newType)) return null;
  const name = moduleTypes.find((t) => t.type === newType)?.name ?? newType;
  const gige = newType === "basler_camera" ? " (GigE Basler cameras aren't scanned, so that may be fine)" : "";
  return `Nothing this Pi has attached suits ${name}${gige}. It will report NOT_READY until its hardware is connected.`;
}

export default ModuleActionsMenu;
