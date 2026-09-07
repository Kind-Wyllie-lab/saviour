import { useEffect, useRef, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import { filterPrivateKeys } from "../configUtils";
import ExportConfigSection from "./ExportConfigSection";
import ConfigActionBar from "../ConfigActionBar";
import ControllerActionsMenu from "/src/basic/components/ControllerActionsMenu/ControllerActionsMenu";
import ThemeImportModal from "/src/basic/components/ThemeImportModal/ThemeImportModal";
import { listThemes, themeTokens, DEFAULT_THEME_ID, BUILTIN_THEMES } from "/src/basic/utils/themes";

const BUILTIN_THEME_IDS = new Set(BUILTIN_THEMES.map(t => t.id));

const TABS = [
  { key: "basic",      label: "Basic"      },
  { key: "thresholds", label: "Thresholds" },
  { key: "export",     label: "Export"     },
  { key: "alerts",     label: "Alerts"     },
  { key: "frontend",   label: "Frontend"   },
];

// recording.* keys surfaced on the Thresholds tab. Every one is wired:
//  - ptp_start_gate_us   -> recording-START gate: _check_ptp_sync (recording.py) blocks
//                           a session start until every target module is under this
//  - ptp_threshold_us    -> mid-recording "PTP sync degraded" warning (recording.py)
//  - nas_min_free_pct     -> scheduled-session pre-flight block + mid-recording NAS check
//  - nas_warn_free_pct    -> "NAS space low" warning
//  - local_min_free_pct   -> scheduled-session pre-flight per-module low-disk warning
//  - export_stale_mins    -> "export stalled" alert for a session still exporting
const THRESHOLD_FIELDS = [
  { key: "ptp_start_gate_us",  label: "PTP start gate (µs)", step: 1,
    hint: "A session won't start until every target module's clock offset (both servos) is under this. Kept tight so recording only begins on a converged, stable offset — 50 µs is sub-frame at every fps. Raise it on a multi-hop network if transient jitter causes start retries." },
  { key: "ptp_threshold_us",   label: "PTP degraded warning (µs)", step: 1,
    hint: "Warn (not block) mid-recording if a module's offset exceeds this. Deliberately looser than the start gate so routine sub-ms jitter doesn't alert." },
  { key: "nas_min_free_pct",   label: "NAS minimum free (%)", step: 1,
    hint: "A scheduled session will not start, and a running one alerts, below this much free space on the export share." },
  { key: "nas_warn_free_pct",  label: "NAS warning free (%)", step: 1,
    hint: "Advisory 'NAS space low' alert threshold. Should be above the minimum." },
  { key: "local_min_free_pct", label: "Module local minimum free (%)", step: 1,
    hint: "Per-module SD-card free space below which a scheduled session flags a low-disk warning at start." },
  { key: "export_stale_mins",  label: "Export stall alert (min)", step: 5,
    hint: "Alert if a session still has files waiting to export this long after they were recorded." },
];

function ControllerConfigCard() {
  const loggedIn = useIsLoggedIn();
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm();
  const [controllerInfo, setControllerInfo] = useState({ ip: null, version: null });
  const [saveStatus, setSaveStatus] = useState(null);
  const [activeTab, setActiveTab] = useHashTab("basic", TABS.map(t => t.key));
  const [teamsTestStatus, setTeamsTestStatus] = useState(null); // null | "testing" | {success, detail}
  // null == closed; "new" == the blank import flow; a theme object == editing
  // that custom theme.
  const [themeModal, setThemeModal] = useState(null);
  const saveTimerRef = useRef(null);

  useEffect(() => {
    socket.emit("get_controller_config");
    socket.emit("get_controller_info");

    // Named handlers so cleanup removes exactly these listeners (socket.off(event)
    // with no handler removes *every* listener for that event — since other
    // things also listen on "controller_config_response" (e.g. useControllerTheme,
    // mounted for the lifetime of the app), the bare form would silently kill
    // their listeners too the moment this component unmounts.
    const handleConfigResponse = (data) => {
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
    };
    const handleInfoResponse = (data) => {
      setControllerInfo({ ip: data.ip, version: data.version });
    };
    const handleTeamsTestResult = (data) => {
      setTeamsTestStatus(data);
    };

    socket.on("controller_config_response", handleConfigResponse);
    socket.on("controller_info_response", handleInfoResponse);
    socket.on("teams_test_result", handleTeamsTestResult);

    return () => {
      socket.off("controller_config_response", handleConfigResponse);
      socket.off("controller_info_response", handleInfoResponse);
      socket.off("teams_test_result", handleTeamsTestResult);
      clearTimeout(saveTimerRef.current);
    };
  }, []);

  const handleSave = () => {
    setSaveStatus("saving");
    socket.emit("save_controller_config", { config: filterPrivateKeys(formData) });
    markSaved();
  };

  const NOTIFY_TOGGLES = [
    { key: "notify_recording_started", label: "Recording started" },
    { key: "notify_recording_stopped", label: "Recording stopped" },
    { key: "notify_daily_summary",     label: "Daily summary (scheduled sessions)" },
    { key: "notify_session_faults",    label: "Session errors, missed runs & export stalls" },
    { key: "notify_module_offline",    label: "Module goes offline" },
    { key: "notify_module_online",     label: "Module comes back online" },
    { key: "notify_ptp_degraded",      label: "PTP sync degrades mid-recording" },
    { key: "notify_recording_health",  label: "A module self-reports its recording as unhealthy" },
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
        <div className={`config-form${isDirty ? " config-form--dirty" : ""}`}>

          <div className="config-tabs-layout">
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
              <>
                <div className="form-field">
                  <label>Name:</label>
                  <input type="text"
                    value={formData?.controller?.name ?? ""}
                    onChange={e => handleChange(["controller", "name"], e)} />
                </div>
                <div className="form-field">
                  <label>Location:</label>
                  <input type="text"
                    placeholder="e.g. Room 204"
                    value={formData?.controller?.location ?? ""}
                    onChange={e => handleChange(["controller", "location"], e)} />
                </div>
              </>
            )}

            {/* THRESHOLDS */}
            {activeTab === "thresholds" && (
              <>
                {THRESHOLD_FIELDS.map(({ key, label, step, hint }) => (
                  <div className="threshold-field" key={key}>
                    <div className="form-field">
                      <label>{label}</label>
                      <input
                        type="number"
                        step={step}
                        value={formData?.recording?.[key] ?? ""}
                        onChange={e => handleChange(["recording", key], e)}
                      />
                    </div>
                    <span className="field-hint">{hint}</span>
                  </div>
                ))}
              </>
            )}

            {/* EXPORT */}
            {activeTab === "export" && (
              <ExportConfigSection
                exportConfig={formData?.export}
                handleChange={handleChange}
              />
            )}

            {/* ALERTS (Teams webhook + notification toggles) */}
            {activeTab === "alerts" && (
              <fieldset className="nested-fieldset teams-fieldset">
                <legend className="nested-fieldset-legend teams-fieldset-legend">
                  Teams webhook
                </legend>
                <div className="nested">
                  <div className="form-field">
                    <label>Webhook URL:</label>
                    <input
                      type="text"
                      value={formData?.teams?.webhook_url ?? ""}
                      onChange={e => handleChange(["teams", "webhook_url"], e)}
                    />
                  </div>
                  <div className="form-field">
                    <label>Alert cooldown (secs):</label>
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
                  <div className="teams-test-row">
                    <button
                      type="button"
                      className="teams-test-btn"
                      disabled={teamsTestStatus === "testing" || !formData?.teams?.webhook_url?.trim()}
                      title={formData?.teams?.webhook_url?.trim()
                        ? "Sends a real alert to the webhook URL above (the typed value, saved or not)"
                        : "Enter a webhook URL above first"}
                      onClick={() => {
                        setTeamsTestStatus("testing");
                        socket.emit("test_teams_webhook", { webhook_url: formData?.teams?.webhook_url });
                      }}
                    >
                      {teamsTestStatus === "testing" ? "Sending…" : "Send test alert"}
                    </button>
                    {teamsTestStatus && teamsTestStatus !== "testing" && (
                      <span className={`teams-test-result ${teamsTestStatus.success ? "teams-test-result--ok" : "teams-test-result--fail"}`}>
                        {teamsTestStatus.success ? "✓" : "✗"} {teamsTestStatus.detail}
                      </span>
                    )}
                  </div>
                  <span className="field-hint">
                    Posts a real alert card to the webhook (tests the URL you've typed, even before Save) — bypasses the cooldown and the "Notify on" filters.
                  </span>
                </div>
              </fieldset>
            )}

            {/* FRONTEND */}
            {activeTab === "frontend" && (
              <>
                <div className="form-field frontend-theme-field">
                  <label>Theme:</label>
                  <div className="theme-picker">
                    {listThemes(formData?.frontend?.custom_themes).map(t => {
                      const darkMode = formData?.frontend?.dark_mode ?? true;
                      const selected =
                        (formData?.frontend?.theme_id ?? DEFAULT_THEME_ID) === t.id;
                      const preview = themeTokens(t, darkMode);
                      return (
                        <label
                          key={t.id}
                          className={`theme-swatch${selected ? " selected" : ""}`}
                          title={t.name}
                        >
                          <input
                            type="radio"
                            name="frontend-theme"
                            value={t.id}
                            checked={selected}
                            onChange={e => handleChange(["frontend", "theme_id"], e)}
                          />
                          <span
                            className="theme-swatch-preview"
                            style={{
                              background: preview["--bg-color"],
                              borderColor: preview["--border-color"],
                            }}
                          >
                            <span className="theme-swatch-dot" style={{ background: preview["--card-bg-color"] }} />
                            <span className="theme-swatch-dot" style={{ background: preview["--accent-color"] }} />
                            <span className="theme-swatch-dot" style={{ background: preview["--accent-color-alt"] }} />
                            <span className="theme-swatch-dot" style={{ background: preview["--text-color"] }} />
                          </span>
                          <span className="theme-swatch-name">{t.name}</span>
                          {!BUILTIN_THEME_IDS.has(t.id) && loggedIn && (
                            <>
                              <button
                                type="button"
                                className="theme-swatch-edit"
                                title={`Edit "${t.name}"`}
                                onClick={e => {
                                  e.preventDefault();
                                  setThemeModal(t);
                                }}
                              >
                                ✎
                              </button>
                              <button
                                type="button"
                                className="theme-swatch-delete"
                                title={`Delete "${t.name}"`}
                                onClick={e => {
                                  e.preventDefault();
                                  if (window.confirm(`Delete the "${t.name}" theme?`)) {
                                    socket.emit("delete_custom_theme", { id: t.id });
                                  }
                                }}
                              >
                                ×
                              </button>
                            </>
                          )}
                        </label>
                      );
                    })}
                  </div>
                  <div className="theme-picker-actions">
                    <button
                      type="button"
                      className="teams-test-btn"
                      onClick={() => setThemeModal("new")}
                      disabled={!loggedIn}
                      title={loggedIn ? undefined : "Login required for this action"}
                    >
                      + Import from Coolors
                    </button>
                  </div>
                  <span className="frontend-accent-hint">
                    Applied live across every dashboard variant on save - background, cards,
                    text, buttons, links and highlights. Preview shows each theme's{" "}
                    {(formData?.frontend?.dark_mode ?? true) ? "dark" : "light"} variant.
                  </span>
                </div>
                <div className="form-field">
                  <label>Dark mode:</label>
                  <label className="toggle-switch">
                    <input
                      type="checkbox"
                      checked={formData?.frontend?.dark_mode ?? true}
                      onChange={e => handleChange(["frontend", "dark_mode"], e)}
                    />
                    <span className="toggle-switch-track">
                      <span className="toggle-switch-thumb" />
                    </span>
                  </label>
                  <span className="frontend-accent-hint">
                    Applied live across every dashboard variant on save. Each theme
                    has its own light and dark variant.
                  </span>
                </div>
              </>
            )}
          </div>
          </div>

          <div className="config-form-actions">
            <div className="config-section-divider" />

            <ConfigActionBar
              onSave={handleSave}
              isDirty={isDirty}
              saveStatus={saveStatus ?? "idle"}
            />
          </div>
        </div>
      </div>

      {themeModal && (
        <ThemeImportModal
          key={themeModal === "new" ? "new" : themeModal.id}
          editingTheme={themeModal === "new" ? null : themeModal}
          onClose={() => setThemeModal(null)}
        />
      )}
    </div>
  );
}

export default ControllerConfigCard;
