import { useEffect, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import useModules from "/src/hooks/useModules";
import useHealth from "/src/hooks/useHealth";

const STATE_LABEL = {
  ok: "Syncing",
  error: "Sync failing",
  not_configured: "No database set",
  store_error: "Local store failed",
  stopped: "Stopped",
};

function formatAge(seconds) {
  if (seconds == null) return "none";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/**
 * Long-term MariaDB for RFID reads. Saved with the rest of the controller
 * config; a changed section is pushed to every RFID module on save (and on
 * every module connect), like the export share credentials. Each RFID module
 * keeps its reads in a local store and forwards them every few seconds, so a
 * database outage only grows the backlog shown below.
 */
function RfidDbSection({ rfidDb, handleChange }) {
  const loggedIn = useIsLoggedIn();
  const { moduleList } = useModules();
  const { moduleHealth } = useHealth({ pollInterval: 15000 });
  const [testResults, setTestResults] = useState({}); // module_id -> "testing" | {ok, message}
  const [pushResult, setPushResult] = useState(null);

  useEffect(() => {
    const onTest = (data) =>
      setTestResults((prev) => ({ ...prev, [data.module_id]: data }));
    const onPush = (data) => setPushResult(data);
    socket.on("rfid_db_test_result", onTest);
    socket.on("rfid_db_sync_result", onPush);
    return () => {
      socket.off("rfid_db_test_result", onTest);
      socket.off("rfid_db_sync_result", onPush);
    };
  }, []);

  const db = rfidDb ?? {};
  const rfidModules = moduleList.filter((m) => m.type === "rfid");
  const field = (key, label, type = "text", placeholder = "") => (
    <div className="form-field">
      <label>{label}</label>
      <input
        type={type}
        placeholder={placeholder}
        value={db[key] ?? ""}
        onChange={(e) => handleChange(["rfid_db", key], e)}
      />
    </div>
  );

  const runTest = (moduleId) => {
    setTestResults((prev) => ({ ...prev, [moduleId]: "testing" }));
    socket.emit("test_rfid_db", { module_id: moduleId });
  };

  return (
    <fieldset className="nested-fieldset">
      <legend className="nested-fieldset-legend" style={{ cursor: "default" }}>
        RFID database
      </legend>
      <div className="nested">
        {field("host", "Host:", "text", "e.g. 192.168.1.2")}
        {field("port", "Port:", "number")}
        {field("database", "Database:", "text", "habitat_rfid")}
        {field("username", "Username:", "text", "saviour_rfid")}
        {field("password", "Password:", "password")}

        <span className="frontend-accent-hint">
          RFID modules log every read locally and send them to this MariaDB every
          few seconds. Saving a change here pushes it to every RFID module. Leave
          the host empty to turn the sync off. Create the table first with{" "}
          <code>docs/sql/rfid_schema.sql</code>.
        </span>

        <div className="export-sync-row">
          <button
            type="button"
            className="save-button"
            onClick={() => socket.emit("sync_rfid_db_to_all")}
            disabled={!loggedIn}
            title={loggedIn ? "Re-send the saved settings to every RFID module" : "Login required for this action"}
          >
            Sync to RFID Modules
          </button>
          {pushResult && (
            <span className="config-sync-badge config-sync-badge--synced">
              sent to {pushResult.total} RFID module{pushResult.total === 1 ? "" : "s"}
            </span>
          )}
        </div>

        {rfidModules.length === 0 ? (
          <span className="field-hint">No RFID modules connected.</span>
        ) : (
          rfidModules.map((m) => {
            const st = moduleHealth?.[m.id]?.rfid_db;
            const test = testResults[m.id];
            return (
              <div className="threshold-field" key={m.id}>
                <div className="form-field">
                  <label>{m.name || m.id}:</label>
                  <span>
                    {st ? STATE_LABEL[st.state] ?? st.state : "waiting for heartbeat"}
                    {st && st.backlog != null &&
                      ` · ${st.backlog} waiting · oldest ${formatAge(st.oldest_unsynced_age_s)}`}
                    {st && st.zero_reads ? ` · ${st.zero_reads} zero-ID reads` : ""}
                  </span>
                  <button
                    type="button"
                    className="teams-test-btn"
                    onClick={() => runTest(m.id)}
                    disabled={!loggedIn || test === "testing"}
                    title={loggedIn
                      ? "The module connects with its saved settings (save first) and checks the rfid_read table"
                      : "Login required for this action"}
                  >
                    {test === "testing" ? "Testing…" : "Test connection"}
                  </button>
                </div>
                {st?.last_error && <span className="field-hint">Last error: {st.last_error}</span>}
                {test && test !== "testing" && (
                  <span className={`teams-test-result ${test.ok ? "teams-test-result--ok" : "teams-test-result--fail"}`}>
                    {test.ok ? "✓" : "✗"} {test.message}
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </fieldset>
  );
}

export default RfidDbSection;
