import { useEffect, useRef, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import ExportConfigSection from "./ExportConfigSection";
import ControllerActionsMenu from "/src/basic/components/ControllerActionsMenu/ControllerActionsMenu";

const TABS = [
  { key: "basic",    label: "Basic"    },
  { key: "settings", label: "Settings" },
  { key: "export",   label: "Export"   },
];

function ControllerConfigCard() {
  const loggedIn = useIsLoggedIn();
  const { formData, setFormData, handleChange, markSaved } = useConfigForm();
  const [controllerInfo, setControllerInfo] = useState({ ip: null, version: null });
  const [saveStatus, setSaveStatus] = useState(null);
  const [activeTab, setActiveTab] = useState("basic");
  const [teamsTestStatus, setTeamsTestStatus] = useState(null); // null | "testing" | {success, detail}
  const saveTimerRef = useRef(null);

  useEffect(() => {
    socket.emit("get_controller_config");
    socket.emit("get_controller_info");

    socket.on("controller_config_response", (data) => {
      setFormData(data.config || {});
      markSaved(data.config || {});
      setSaveStatus(prev => {
        if (prev === "saving") {
          clearTimeout(saveTimerRef.current);
          saveTimerRef.current = setTimeout(() => setSaveStatus(null), 3000);
          return "saved";
        }
        return prev;
      });
    });

    socket.on("controller_info_response", (data) => {
      setControllerInfo({ ip: data.ip, version: data.version });
    });

    socket.on("teams_test_result", (data) => {
      setTeamsTestStatus(data);
    });

    return () => {
      socket.off("controller_config_response");
      socket.off("controller_info_response");
      socket.off("teams_test_result");
      clearTimeout(saveTimerRef.current);
    };
  }, []);

  const handleSave = () => {
    setSaveStatus("saving");
    socket.emit("save_controller_config", { config: filterPrivateKeys(formData) });
    markSaved();
  };

  // Settings tab: everything except controller.name, export, and teams (rendered custom below)
  const settingsData = (() => {
    if (!formData) return formData;
    const { export: _e, controller: ctrl, teams: _t, ...rest } = filterPrivateKeys(formData) ?? {};
    // Keep controller section only if it has fields beyond `name` (name goes in Basic)
    const { name: _n, ...ctrlRest } = ctrl ?? {};
    const result = { ...rest };
    if (Object.keys(ctrlRest).length > 0) result.controller = ctrlRest;
    return result;
  })();

  const NOTIFY_TOGGLES = [
    { key: "notify_recording_started", label: "Recording started" },
    { key: "notify_recording_stopped", label: "Recording stopped" },
    { key: "notify_daily_summary",     label: "Daily summary (scheduled sessions)" },
    { key: "notify_session_faults",    label: "Session errors, missed runs & export stalls" },
    { key: "notify_module_offline",    label: "Module goes offline" },
    { key: "notify_module_online",     label: "Module comes back online" },
    { key: "notify_ptp_degraded",      label: "PTP sync degrades mid-recording" },
    { key: "notify_disk_space",        label: "Low disk space (local & NAS)" },
  ];

  return (
    <div className="config-card controller-config-card">
      <div className="card-header">
        <div className="card-header-top">
          <h3>Controller Config</h3>
          <div className="card-header-actions">
            <ControllerActionsMenu />
          </div>
        </div>
        <div className="device-info">
          {controllerInfo.ip && <span>IP: {controllerInfo.ip}</span>}
          {controllerInfo.version && <span>{controllerInfo.version}</span>}
        </div>
      </div>
      <div className="config-card-body">
        <div className="config-form">

          <div className="config-tabs">
            {TABS.map(t => (
              <button key={t.key} type="button"
                className={`config-tab-btn${activeTab === t.key ? " active" : ""}`}
                onClick={() => setActiveTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="config-tab-content">

            {/* BASIC */}
            {activeTab === "basic" && (
              <div className="form-field">
                <label>Name:</label>
                <input type="text"
                  value={formData?.controller?.name ?? ""}
                  onChange={e => handleChange(["controller", "name"], e)} />
              </div>
            )}

            {/* SETTINGS */}
            {activeTab === "settings" && (
              <>
                <form>
                  <ConfigFields data={settingsData} handleChange={handleChange} />
                </form>

                {/* Teams / Notifications — custom section */}
                <fieldset className="nested-fieldset teams-fieldset">
                  <legend className="nested-fieldset-legend teams-fieldset-legend">
                    teams
                  </legend>
                  <div className="nested">
                    <div className="form-field">
                      <label>webhook_url:</label>
                      <input
                        type="text"
                        value={formData?.teams?.webhook_url ?? ""}
                        onChange={e => handleChange(["teams", "webhook_url"], e)}
                      />
                    </div>
                    <div className="form-field">
                      <label>alert_cooldown_secs:</label>
                      <input
                        type="number"
                        value={formData?.teams?.alert_cooldown_secs ?? 600}
                        onChange={e => handleChange(["teams", "alert_cooldown_secs"], e)}
                      />
                    </div>
                    <div className="teams-notify-section">
                      <span className="teams-notify-label">Notify on:</span>
                      <div className="teams-notify-grid">
                        {NOTIFY_TOGGLES.map(({ key, label }) => (
                          <label key={key} className="teams-notify-row">
                            <input
                              type="checkbox"
                              checked={formData?.teams?.[key] ?? false}
                              onChange={e => handleChange(["teams", key], e)}
                            />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                    {formData?.teams?.webhook_url && (
                      <div className="teams-test-row">
                        <button
                          type="button"
                          className="teams-test-btn"
                          disabled={teamsTestStatus === "testing"}
                          onClick={() => {
                            setTeamsTestStatus("testing");
                            socket.emit("test_teams_webhook");
                          }}
                        >
                          {teamsTestStatus === "testing" ? "Sending…" : "Send test message"}
                        </button>
                        {teamsTestStatus && teamsTestStatus !== "testing" && (
                          <span className={`teams-test-result ${teamsTestStatus.success ? "teams-test-result--ok" : "teams-test-result--fail"}`}>
                            {teamsTestStatus.success ? "✓" : "✗"} {teamsTestStatus.detail}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </fieldset>
              </>
            )}

            {/* EXPORT */}
            {activeTab === "export" && (
              <ExportConfigSection
                exportConfig={formData?.export}
                handleChange={handleChange}
              />
            )}
          </div>

          <div className="config-section-divider" />

          <button className="save-button" type="button" onClick={handleSave}
            disabled={!loggedIn} title={loggedIn ? undefined : "Login required for this action"}>
            Save Config
          </button>
          {saveStatus === "saving" && (
            <span className="config-sync-badge config-sync-badge--pending">Saving...</span>
          )}
          {saveStatus === "saved" && (
            <span className="config-sync-badge config-sync-badge--synced">&#10003; Saved</span>
          )}
        </div>
      </div>
    </div>
  );
}

export default ControllerConfigCard;
