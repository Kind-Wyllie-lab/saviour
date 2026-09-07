import "./UpdateProgressModal.css";

// Shown while/after an "update all" action (module deploy, controller
// update/revert, or ModuleList's own Update All) is in flight. The caller
// owns tracking status per device -- this just presents it as a modal
// instead of a table left sitting on the page, so it reads as a step that
// follows the action that started it rather than a side effect elsewhere.
//
// `rows`: [{ id, name, status }], where status is
//   "updating" | "restarting" | { success, output } | undefined (pending)
export default function UpdateProgressModal({ rows, onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal update-progress-modal" onClick={(e) => e.stopPropagation()}>
        <p className="update-progress-modal__title">Update progress</p>
        <div className="update-progress-modal__table-wrap">
          <table className="update-progress-modal__table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Result</th>
                <th>Output</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ id, name, status }) => {
                const isInProgress = status === "updating" || status === "restarting";
                return (
                  <tr key={id}>
                    <td><span className="update-progress-modal__name">{name}</span></td>
                    <td>
                      {isInProgress ? (
                        <span className="update-progress-modal__muted">
                          {status === "restarting" ? "Restarting…" : "Updating…"}
                        </span>
                      ) : status?.success ? (
                        <span className="val--ok">&#10003; Updated</span>
                      ) : status ? (
                        <span className="val--danger">&#10007; Failed</span>
                      ) : (
                        <span className="update-progress-modal__muted">Pending…</span>
                      )}
                    </td>
                    <td className="update-progress-modal__output">
                      {status && !isInProgress ? status.output : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="modal-buttons" style={{ marginTop: "12px" }}>
          <button className="save-button" type="button" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
