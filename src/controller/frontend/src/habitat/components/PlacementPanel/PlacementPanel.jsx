import { useEffect, useMemo, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import useAudioLevels, { isStale } from "../../hooks/useAudioLevels";
import "./PlacementPanel.css";

// Which camera and AudioMoth is in which box (variants/habitat/placement.py).
// A camera's box is its module name and an AudioMoth's box its label, so
// choosing a box here saves that name / label to the module (the same
// set_config round trip as the Settings page). Whatever already sat in the
// chosen box is unplaced. Find an AudioMoth by making a noise next to it
// and watching which meter here jumps.

const DB_FLOOR = -80;
const pct = (db) => Math.max(0, Math.min(100, ((db - DB_FLOOR) / -DB_FLOOR) * 100));

function usePlacement(modules) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [applied, setApplied] = useState(null);

  useEffect(() => {
    const onState = (s) => setState(s);
    const onError = (e) => setError(e?.error ?? String(e));
    const onPreview = (p) => setPreview(p);
    const onApplied = (a) => { setApplied(a); setPreview(null); };
    socket.on("placement_state", onState);
    socket.on("placement_error", onError);
    socket.on("placement_preview", onPreview);
    socket.on("placement_applied", onApplied);
    return () => {
      socket.off("placement_state", onState);
      socket.off("placement_error", onError);
      socket.off("placement_preview", onPreview);
      socket.off("placement_applied", onApplied);
    };
  }, []);

  // Re-read whenever the module list changes (a saved name or label comes
  // back as a module config update), batched a little.
  useEffect(() => {
    const t = setTimeout(() => socket.emit("get_placement"), 300);
    return () => clearTimeout(t);
  }, [modules]);

  const send = (event, payload) => { setError(null); setApplied(null); socket.emit(event, payload); };
  return { state, error, preview, applied, send, clearPreview: () => setPreview(null) };
}

function MiniMeter({ level }) {
  if (isStale(level)) return <span className="placement-meter placement-meter--stale" title="No level" />;
  return (
    <span className={`placement-meter${level.clipping ? " placement-meter--clip" : ""}`}
          title={`${level.level_db.toFixed(0)} dBFS, peak ${level.hold_db.toFixed(0)}`}>
      <span className="placement-meter-fill" style={{ width: `${pct(level.level_db)}%` }} />
      <span className="placement-meter-peak" style={{ left: `${pct(level.hold_db)}%` }} />
    </span>
  );
}

function BoxSelect({ value, boxes, occupant, disabled, onChange, none = "not placed" }) {
  // Keep the current value selectable even if it's outside the offered set
  // (an AudioMoth labelled outside its module's column), so it shows truly.
  const options = value && !boxes.includes(value) ? [...boxes, value] : boxes;
  return (
    <select value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{none}</option>
      {options.map((b) => (
        <option key={b} value={b}>
          {b}{occupant(b) && b !== value ? ` (replaces ${occupant(b)})` : ""}
        </option>
      ))}
    </select>
  );
}

function describe(c) {
  const what = c.kind === "audiomoth" ? `AudioMoth ${c.serial}` : c.module_id;
  const spot = (v) => (v ? (c.kind === "mic" ? `column ${v}` : v) : "not placed");
  return `${what}: ${spot(c.before)} → ${spot(c.after)}`;
}

function PlacementPanel({ modules, onClose }) {
  const isLoggedIn = useIsLoggedIn();
  const { state, error, preview, applied, send, clearPreview } = usePlacement(modules);
  const levels = useAudioLevels();
  const [tab, setTab] = useState("audiomoths");
  const [text, setText] = useState("");

  const boxes = useMemo(() => state?.boxes.map((b) => b.id) ?? [], [state]);
  const camIn = useMemo(() => Object.fromEntries(
    (state?.boxes ?? []).map((b) => [b.id, b.cameras.join(", ")])), [state]);
  const mothIn = useMemo(() => Object.fromEntries(
    (state?.boxes ?? []).map((b) => [b.id, b.audiomoths.join(", ")])), [state]);
  const mothsByModule = useMemo(() => (state?.audiomoths ?? []).reduce((acc, a) => {
    (acc[a.module_id] ??= []).push(a);
    return acc;
  }, {}), [state]);
  const micIn = useMemo(() => Object.fromEntries(
    (state?.mics ?? []).filter((m) => m.column).map((m) => [m.column, m.module_id])), [state]);
  // A module's AudioMoths only reach its own column's boxes.
  const boxesFor = (column) => (column ? boxes.filter((b) => b.replace(/\d+$/, "") === column) : boxes);

  const place = (assignment) => send("placement_apply", { assignments: [assignment] });

  if (!state) return <aside className="placement-panel">Loading placement…</aside>;

  const placedCams = state.cameras.filter((c) => c.box).length;
  const placedMoths = state.audiomoths.filter((a) => a.box).length;
  const placedMics = state.mics.filter((m) => m.column).length;

  return (
    <aside className="placement-panel">
      <header className="placement-head">
        <h3>Placement</h3>
        <button type="button" className="placement-close" onClick={onClose} title="Close">×</button>
      </header>
      <p className="placement-summary">
        {placedCams}/{boxes.length} cameras · {placedMics}/{state.columns.length} mic columns ·{" "}
        {placedMoths}/{boxes.length} AudioMoths placed
      </p>
      {!isLoggedIn && <p className="placement-note">Sign in to change placement.</p>}
      {error && <p className="placement-error">{error}</p>}
      {applied && applied.changes.length > 0 && (
        <p className="placement-ok">Saved {applied.changes.length} change{applied.changes.length === 1 ? "" : "s"}; modules are applying it.</p>
      )}
      {state.problems.length > 0 && (
        <ul className="placement-problems">
          {state.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}

      <nav className="placement-tabs">
        {[["audiomoths", "Microphones"], ["cameras", "Cameras"], ["import", "Import"], ["log", "Changes"]].map(([k, label]) => (
          <button key={k} type="button" className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </nav>

      {tab === "audiomoths" && (
        <div className="placement-list">
          <p className="placement-hint">
            Set each microphone module's column first; its AudioMoths can then
            only go in that column. Make a noise next to a box and watch which
            meter jumps.
          </p>
          {state.mics.length === 0 && <p className="placement-empty">No microphone modules connected.</p>}
          {state.mics.map((m) => (
            <section key={m.module_id} className="placement-mic">
              <div className="placement-row placement-mic-head">
                <span className={`placement-dot${m.online ? "" : " placement-dot--off"}`} />
                <span className="placement-id">{m.module_id}</span>
                <BoxSelect value={m.column} boxes={state.columns} none="no column"
                  occupant={(c) => micIn[c]} disabled={!isLoggedIn || m.recording}
                  onChange={(column) => send("placement_apply", {
                    assignments: [{ kind: "mic", module_id: m.module_id, column }] })} />
              </div>
              {(mothsByModule[m.module_id] ?? []).length === 0 && (
                <p className="placement-empty">No AudioMoths reporting.</p>
              )}
              {(mothsByModule[m.module_id] ?? []).map((a) => (
                <div key={a.serial} className="placement-row">
                  <span className="placement-id" title={a.live ? "Sending levels" : "Not heard recently"}>
                    {a.serial}
                  </span>
                  <MiniMeter level={levels[`${m.module_id}:${a.serial}`]} />
                  <BoxSelect value={a.box} boxes={boxesFor(m.column)} occupant={(b) => mothIn[b]}
                    disabled={!isLoggedIn || m.recording}
                    onChange={(box) => place({ kind: "audiomoth", module_id: m.module_id, serial: a.serial, box })} />
                </div>
              ))}
            </section>
          ))}
        </div>
      )}

      {tab === "cameras" && (
        <div className="placement-list">
          <p className="placement-hint">A camera's box is its module name, which is also its recordings' folder.</p>
          {state.cameras.length === 0 && <p className="placement-empty">No cameras connected.</p>}
          {state.cameras.map((c) => (
            <div key={c.module_id} className="placement-row">
              <span className={`placement-dot${c.online ? "" : " placement-dot--off"}`} />
              <span className="placement-id" title={c.name !== c.module_id ? `Named ${c.name}` : ""}>{c.module_id}</span>
              <BoxSelect value={c.box} boxes={boxes} occupant={(b) => camIn[b]}
                disabled={!isLoggedIn || c.recording}
                onChange={(box) => place({ kind: "camera", module_id: c.module_id, box })} />
            </div>
          ))}
        </div>
      )}

      {tab === "import" && (
        <div className="placement-import">
          <p className="placement-hint">
            One per line: a camera module id or AudioMoth serial and its box,
            or a microphone module id and its column (comma, tab or space
            between). Anything not listed keeps what it has.
          </p>
          <textarea rows={10} value={text} placeholder={"hailo_camera_3606, A1\nmicrophone_4703, A\n24E1440A5F2C, A1"}
                    onChange={(e) => { setText(e.target.value); clearPreview(); }} />
          <button type="button" disabled={!isLoggedIn || !text.trim()}
                  onClick={() => send("placement_preview_import", { text })}>
            Preview
          </button>
          {preview && (
            <div className="placement-preview">
              {preview.errors.length > 0 && (
                <ul className="placement-problems">{preview.errors.map((e) => <li key={e}>{e}</li>)}</ul>
              )}
              {preview.changes.length === 0
                ? <p className="placement-empty">Nothing would change.</p>
                : <ul>{preview.changes.map((c) => <li key={`${c.module_id}:${c.serial}`}>{describe(c)}</li>)}</ul>}
              <button type="button" disabled={!isLoggedIn || preview.changes.length === 0}
                      onClick={() => send("placement_apply", { assignments: preview.assignments, reason: "import" })}>
                Apply {preview.changes.length} change{preview.changes.length === 1 ? "" : "s"}
                {preview.errors.length > 0 ? " (skipping the lines above)" : ""}
              </button>
            </div>
          )}
        </div>
      )}

      {tab === "log" && (
        <ul className="placement-log">
          {state.changes.length === 0 && <li className="placement-empty">No changes yet.</li>}
          {state.changes.map((c, i) => (
            <li key={i}>
              <span className="placement-log-time">{new Date(c.ts * 1000).toLocaleString()}</span>
              {describe(c)} <span className="placement-log-meta">({c.reason}, {c.operator || "?"})</span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

export default PlacementPanel;
