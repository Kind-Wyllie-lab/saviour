import { useState } from "react";
import socket from "../../../../socket";
import "./TTLConfigCard.css";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";

const OUTPUT_MODES = new Set(["experiment_clock", "pseudorandom"]);

function TTLConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData } = useConfigForm(module.config);
  const [collapsed, setCollapsed] = useState(false);
  const [newPin, setNewPin] = useState("");
  const [newMode, setNewMode] = useState("");
  const [hasSaved, setHasSaved] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const ttlCfg = formData?.ttl ?? {};
  const availablePins = module.config?.ttl?._available_pins ?? [];
  const availableModes = (module.config?.ttl?._available_modes ?? []).filter((m) => m !== "None");
  const schema = module.config?.ttl?._mode_settings_schema ?? {};
  const activeLogicOptions = module.config?.ttl?._active_logic_options ?? ["active_low", "active_high"];

  const assignedPins = Object.keys(ttlCfg.pins ?? {}).map(Number);
  const unassignedPins = availablePins.filter((p) => !assignedPins.includes(p));
  const sortedPins = Object.keys(ttlCfg.pins ?? {}).sort((a, b) => Number(a) - Number(b));

  const addPin = () => {
    const parsed = parseInt(newPin, 10);
    if (isNaN(parsed) || !newMode) return;
    const defaults = schema[`_${newMode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [parsed]: {
            mode: newMode,
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
    setNewPin("");
    setNewMode("");
  };

  const removePin = (pin) => {
    setFormData((prev) => {
      const newPins = { ...prev.ttl.pins };
      delete newPins[pin];
      return { ...prev, ttl: { ...prev.ttl, pins: newPins } };
    });
  };

  const changeMode = (pin, mode) => {
    const defaults = schema[`_${mode}`] ?? {};
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: {
            mode,
            ...Object.fromEntries(
              Object.entries(defaults).map(([k, v]) => [k.replace(/^_/, ""), v.default ?? ""])
            ),
          },
        },
      },
    }));
  };

  const changeField = (pin, field, val) => {
    setFormData((prev) => ({
      ...prev,
      ttl: {
        ...prev.ttl,
        pins: {
          ...prev.ttl.pins,
          [pin]: { ...prev.ttl.pins[pin], [field]: val },
        },
      },
    }));
  };

  const saveConfig = () => {
    socket.emit("save_module_config", { id, config: filterPrivateKeys(formData) });
    setHasSaved(true);
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: id });
    setShowResetConfirm(false);
  };

  return (
    <div className={`config-card ttl-config-card ${collapsed ? "collapsed" : ""}`}>
      <div className="card-header">
        <div className="card-header-left">
          <h3 onClick={() => setCollapsed(!collapsed)} style={{ cursor: "pointer" }}>
            {module.name || id} {collapsed ? "(+)" : "(-)"}
          </h3>
          <span className="module-meta">{module.ip} · {module.version}</span>
        </div>
        <div className="card-header-right">
          {hasSaved && module.config_sync_status === "PENDING" && (
            <span className="config-sync-badge config-sync-badge--pending">Saving…</span>
          )}
          {hasSaved && module.config_sync_status === "SYNCED" && (
            <span className="config-sync-badge config-sync-badge--synced">Saved</span>
          )}
          {hasSaved && module.config_sync_status === "FAILED" && (
            <span className="config-sync-badge config-sync-badge--failed">Save failed</span>
          )}
        </div>
      </div>

      {!collapsed && (
        <div className="ttl-body">
          {/* ── Left column: stream ── */}
          <div className="ttl-stream-col">
            <LivestreamCard module={module} />
          </div>

          {/* ── Right column: config ── */}
          <div className="ttl-config-col">
            {/* Active logic */}
            <div className="form-field">
              <label>Active Logic</label>
              <select
                value={ttlCfg.active_logic ?? "active_low"}
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, ttl: { ...prev.ttl, active_logic: e.target.value } }))
                }
              >
                {activeLogicOptions.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </div>

            {/* Pin list */}
            <div className="pin-list">
              {sortedPins.length === 0 && (
                <p className="no-pins-hint">No pins configured. Add one below.</p>
              )}
              {sortedPins.map((pin) => {
                const settings = ttlCfg.pins[pin];
                const mode = settings.mode ?? "None";
                const isOutput = OUTPUT_MODES.has(mode);
                const modeFields = schema[`_${mode}`] ?? {};

                return (
                  <div key={pin} className={`pin-card pin-card--${isOutput ? "output" : "input"}`}>
                    <div className="pin-card-header">
                      <span className="pin-label">GPIO {pin}</span>
                      <span className={`pin-type-badge ${isOutput ? "badge--output" : "badge--input"}`}>
                        {isOutput ? "OUTPUT" : "INPUT"}
                      </span>
                      <button className="btn-remove" onClick={() => removePin(pin)}>✕</button>
                    </div>

                    <div className="field">
                      <label>Mode</label>
                      <select value={mode} onChange={(e) => changeMode(pin, e.target.value)}>
                        {availableModes.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </div>

                    {Object.entries(modeFields).map(([k, meta]) => {
                      const fieldName = k.replace(/^_/, "");
                      return (
                        <div key={k} className="field">
                          <label>{fieldName}</label>
                          <input
                            type={meta.type === "float" || meta.type === "int" ? "number" : "text"}
                            min={meta.min}
                            max={meta.max}
                            step={meta.type === "float" ? "any" : undefined}
                            value={settings[fieldName] ?? ""}
                            onChange={(e) => changeField(pin, fieldName, e.target.value)}
                          />
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>

            {/* Add pin row */}
            <div className="add-pin-row">
              <select value={newPin} onChange={(e) => setNewPin(e.target.value)}>
                <option value="">Pin…</option>
                {unassignedPins.map((p) => (
                  <option key={p} value={p}>GPIO {p}</option>
                ))}
              </select>
              <select value={newMode} onChange={(e) => setNewMode(e.target.value)}>
                <option value="">Mode…</option>
                {availableModes.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <button type="button" onClick={addPin} disabled={!newPin || !newMode}>
                Add Pin
              </button>
            </div>

            {/* Action buttons */}
            <div className="ttl-action-row">
              <button className="save-button" type="button" onClick={saveConfig}>
                Save Config
              </button>
              <button className="reset-button" type="button" onClick={() => setShowResetConfirm(true)}>
                Reset to Default
              </button>
              <button className="update-button" type="button"
                onClick={() => socket.emit("send_command", { module_id: id, type: "update_saviour", params: {} })}>
                Update Saviour
              </button>
              <button className="update-button" type="button"
                onClick={() => socket.emit("send_command", { module_id: id, type: "reboot", params: {} })}>
                Reboot
              </button>
            </div>
          </div>
        </div>
      )}

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p>Reset <strong>{module.name || id}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and custom configuration will be lost.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReset}>Reset</button>
              <button className="save-button" type="button" onClick={() => setShowResetConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default TTLConfigCard;
