import { useEffect, useMemo, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import HabitatGrid from "/src/habitat/components/HabitatGrid/HabitatGrid";

import "./Gates.css";

// RFID gate mapping (plans/rfid-gate-mapping.md): which reader unit is which
// habitat gate. The controller (variants/habitat/gate_map_service.py) owns
// the layout, the map and any mapping run, and broadcasts gate_map_state on
// every change, so all open browsers show the same run.

const KEEPALIVE_MS = 20000;
const FLASH_MS = 1500;
const NO_READ_HINT_S = 30;

const STATUS_LABEL = {
  mapped: "Mapped",
  closed: "Closed",
  not_fitted: "No antenna",
  faulty: "Faulty",
};

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "-");

function useGateMap() {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [hits, setHits] = useState([]);

  useEffect(() => {
    const onState = (s) => { setState(s); setHits(s.hits ?? []); };
    const onHit = (h) => setHits((prev) => [h, ...prev].slice(0, 20));
    const onError = (e) => setError(e?.error ?? String(e));
    const onConnect = () => socket.emit("get_gate_map");
    socket.on("gate_map_state", onState);
    socket.on("gate_map_hit", onHit);
    socket.on("gate_map_error", onError);
    socket.on("connect", onConnect);
    socket.emit("get_gate_map");
    // Keeps the modules' mapping mode on while this page is open (and only
    // then: the controller lets it lapse 90 s after the last renewal).
    const keepalive = setInterval(() => socket.emit("gate_map_keepalive"), KEEPALIVE_MS);
    return () => {
      socket.off("gate_map_state", onState);
      socket.off("gate_map_hit", onHit);
      socket.off("gate_map_error", onError);
      socket.off("connect", onConnect);
      clearInterval(keepalive);
    };
  }, []);

  const send = (event, payload) => { setError(null); socket.emit(event, payload ?? {}); };
  return { state, hits, error, clearError: () => setError(null), send };
}

// Gates that read in the last FLASH_MS, for the grid's flash.
function useFlash(hits) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!hits.length) return undefined;
    setNow(Date.now());
    const t = setTimeout(() => setNow(Date.now()), FLASH_MS);
    return () => clearTimeout(t);
  }, [hits]);
  return useMemo(() => new Set(
    hits.filter((h) => now - h.ts * 1000 < FLASH_MS).flatMap((h) => h.gates ?? [])
  ), [hits, now]);
}

function describeEntry(gate, entry) {
  if (!gate.fitted) return `${gate.id}: not fitted (layout)`;
  if (!entry) return `${gate.id}: not mapped`;
  if (entry.status === "mapped") return `${gate.id}: ${entry.module_id} unit ${entry.unit}`;
  return `${gate.id}: ${STATUS_LABEL[entry.status] ?? entry.status}`;
}

function Legend() {
  return (
    <div className="gates-legend">
      {[["mapped", "Mapped"], ["unmapped", "Not mapped"], ["closed", "Closed"],
        ["faulty", "Faulty"], ["unfitted", "Not fitted / no antenna"]].map(([k, label]) => (
        <span key={k} className="gates-legend-item">
          <span className={`gates-legend-dot gates-legend-dot--${k}`} />{label}
        </span>
      ))}
    </div>
  );
}

// ── Map overview ───────────────────────────────────────────────────────────

function BusCheck({ state, send }) {
  const c = state.check;
  const nothing = c.on_bus_not_mapped.length === 0 && c.mapped_not_on_bus.length === 0
    && c.serial_changed.length === 0 && c.shared_units.length === 0;
  return (
    <div className="gates-card">
      <div className="gates-card-head">
        <h3>Bus check</h3>
        <button type="button" className="gates-btn" onClick={() => send("gate_map_scan")}
                disabled={!state.rfid_modules.length}>
          Scan bus
        </button>
      </div>
      {!state.rfid_modules.length && <p className="gates-muted">No RFID module is connected.</p>}
      {!c.scanned ? (
        <p className="gates-muted">Not scanned yet. Scanning asks every RFID module which units answer.</p>
      ) : (
        <>
          <p className="gates-muted">
            {state.bus.length} unit(s) on the bus · scanned {fmtTime(state.bus_scanned_at)}
          </p>
          <Problems review={c} />
          {nothing && <p className="gates-ok">Every unit on the bus is mapped, and every mapped unit answered.</p>}
        </>
      )}
    </div>
  );
}

function Problems({ review }) {
  const items = [];
  for (const u of review.on_bus_not_mapped ?? []) {
    items.push(["warn", `${u} is on the bus, not mapped to any gate`]);
  }
  for (const u of review.mapped_not_on_bus ?? []) {
    items.push(["warn", `${u.unit} (${u.gates.join(", ")}) is mapped but didn't answer the scan`]);
  }
  for (const s of review.serial_changed ?? []) {
    items.push(["error", `${s.unit} (${s.gate}) has new hardware since it was mapped (serial ${s.mapped_serial} → ${s.bus_serial}): check it`]);
  }
  for (const s of review.shared_units ?? []) {
    items.push(["info", `${s.unit} is shared by ${s.gates.join(" and ")}`]);
  }
  if (!items.length) return null;
  return (
    <ul className="gates-problems">
      {items.map(([tone, text]) => <li key={text} className={`gates-problem gates-problem--${tone}`}>{text}</li>)}
    </ul>
  );
}

function MapTable({ state }) {
  const rows = state.layout.gates.filter((g) => g.fitted || state.map.entries[g.id]);
  return (
    <div className="gates-card">
      <h3>Gates</h3>
      <div className="gates-table-wrap">
        <table className="gates-table">
          <thead>
            <tr><th>Gate</th><th>Status</th><th>Unit</th><th>Serial</th><th>Mapped</th></tr>
          </thead>
          <tbody>
            {rows.map((g) => {
              const e = state.map.entries[g.id];
              return (
                <tr key={g.id}>
                  <td className="gates-mono">{g.id}</td>
                  <td>{e ? (STATUS_LABEL[e.status] ?? e.status) : <span className="gates-muted">Not mapped</span>}</td>
                  <td className="gates-mono">{e?.status === "mapped" ? `${e.module_id}:${e.unit}` : ""}</td>
                  <td className="gates-mono">{e?.unit_serial ?? ""}</td>
                  <td>{e ? fmtTime(e.mapped_at) : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {state.orphaned.length > 0 && (
        <p className="gates-problem gates-problem--warn">
          Mapped gates the layout no longer has as fitted: {state.orphaned.join(", ")}
        </p>
      )}
    </div>
  );
}

function ChangeLog({ changes }) {
  if (!changes.length) return null;
  const show = (e) => (!e ? "unmapped" : e.status === "mapped" ? `${e.module_id}:${e.unit}` : STATUS_LABEL[e.status] ?? e.status);
  return (
    <div className="gates-card">
      <h3>Recent changes</h3>
      <ul className="gates-changes">
        {changes.map((c) => (
          <li key={`${c.ts}-${c.gate}`}>
            <span className="gates-mono">{c.gate}</span>: {show(c.before)} → {show(c.after)}
            <span className="gates-muted"> · {c.operator} · {fmtTime(c.ts)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Walk-through check ─────────────────────────────────────────────────────

function WalkThrough({ state, hits, send }) {
  const on = state.walkthrough;
  return (
    <div className="gates-card">
      <div className="gates-card-head">
        <h3>Walk-through check</h3>
        <button type="button" className={`gates-btn ${on ? "" : "gates-btn--primary"}`}
                onClick={() => send("gate_map_walkthrough", { enabled: !on })}
                disabled={!state.rfid_modules.length}>
          {on ? "Stop" : "Start"}
        </button>
      </div>
      <p className="gates-muted">
        Pass a tag through gates in any order: each read lights up the gate the current
        map says it is. Nothing is changed.
      </p>
      {on && (
        <ul className="gates-hits">
          {hits.length === 0 && <li className="gates-muted">Waiting for a read…</li>}
          {hits.map((h, i) => (
            <li key={`${h.ts}-${h.unit}-${i}`}>
              <span className="gates-mono">{h.module_id}:{h.unit}</span> →{" "}
              {h.gates.length ? <strong>{h.gates.join(", ")}</strong>
                : <span className="gates-problem--warn">not mapped</span>}
              <span className="gates-muted"> · tag {h.tag}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Mapping run ────────────────────────────────────────────────────────────

function StartRun({ state, send, loggedIn, scopeGates, setScopeGates }) {
  const [operator, setOperator] = useState("");
  const [kind, setKind] = useState("all");
  const [wall, setWall] = useState(state.layout.walls[0]?.id ?? "");
  const fitted = state.layout.gates.filter((g) => g.fitted).length;

  const scope = kind === "all" ? "all"
    : kind === "wall" ? { wall }
    : { gates: [...scopeGates] };

  return (
    <div className="gates-card">
      <h3>New mapping run</h3>
      <p className="gates-muted">
        You'll register a probe tag, then pass it through one gate at a time. Other tags
        (animals still in the habitat) are ignored. Nothing is saved until the review.
      </p>
      <label className="gates-field">
        Your name or initials
        <input value={operator} onChange={(e) => setOperator(e.target.value)} maxLength={60} />
      </label>
      <div className="gates-field">
        Gates
        <label><input type="radio" checked={kind === "all"} onChange={() => setKind("all")} /> Whole habitat ({fitted} fitted)</label>
        <label>
          <input type="radio" checked={kind === "wall"} onChange={() => setKind("wall")} /> One wall{" "}
          <select value={wall} onChange={(e) => { setWall(e.target.value); setKind("wall"); }}>
            {state.layout.walls.map((w) => <option key={w.id} value={w.id}>{w.id}</option>)}
          </select>
        </label>
        <label>
          <input type="radio" checked={kind === "gates"} onChange={() => setKind("gates")} /> Gates I click
          on the grid ({scopeGates.size} chosen)
          {kind === "gates" && scopeGates.size > 0 && (
            <button type="button" className="gates-link" onClick={() => setScopeGates(new Set())}>clear</button>
          )}
        </label>
      </div>
      <button type="button" className="gates-btn gates-btn--primary"
              disabled={!loggedIn || !operator.trim() || (kind === "gates" && !scopeGates.size)}
              title={loggedIn ? undefined : "Sign in to map gates"}
              onClick={() => send("gate_map_start_run", { operator, scope })}>
        Start mapping
      </button>
    </div>
  );
}

function useSecondsSince(ts) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  return ts ? Math.max(0, now - ts) : 0;
}

function RunStep({ run, send, loggedIn }) {
  const waited = useSecondsSince(run.prompt_ts);
  const act = (action, extra) => send("gate_map_action", { action, ...extra });
  const c = run.capture;
  const issue = run.issue;
  const readers = Object.keys(c.counts);

  if (run.phase === "probe") {
    return (
      <div className="gates-step">
        <p className="gates-prompt">Hold the probe tag at any antenna.</p>
        <p className="gates-muted">The first tag read becomes the probe for this run.</p>
      </div>
    );
  }

  return (
    <div className="gates-step">
      <p className="gates-step-count">Gate {run.index + 1} of {run.total}</p>
      <p className="gates-prompt">Pass the probe through gate <strong>{run.gate}</strong></p>
      <p className="gates-muted">Probe tag {run.probe_tag}</p>

      {!c.first && (
        <p className={waited > NO_READ_HINT_S ? "gates-problem gates-problem--warn" : "gates-muted"}>
          {waited > NO_READ_HINT_S
            ? "No antenna has read the probe. Retry, mark the gate faulty, or skip it."
            : "Waiting for the probe…"}
        </p>
      )}
      {c.first && !c.settled && <p className="gates-muted">Read by {c.first}, checking for neighbours…</p>}
      {c.settled && !issue && <p className="gates-ok">Read by <strong>{c.first}</strong></p>}

      {issue?.kind === "multiple" && (
        <div className="gates-problem gates-problem--warn">
          Units {readers.join(" and ")} both read it. Pick the one at this gate, or retry.
          <div className="gates-actions">
            {issue.units.map((u) => (
              <button key={u} type="button" className="gates-btn" disabled={!loggedIn}
                      onClick={() => act("confirm", { unit: u })}>
                {u} ({c.counts[u]} reads)
              </button>
            ))}
          </div>
        </div>
      )}
      {issue?.kind === "already_mapped" && (
        <div className="gates-problem gates-problem--warn">
          {issue.unit} is already {issue.gates.join(", ")}.
          <div className="gates-actions">
            <button type="button" className="gates-btn" disabled={!loggedIn}
                    onClick={() => act("confirm", { resolve: "move" })}>
              Move it here ({issue.gates.join(", ")} becomes unmapped)
            </button>
            <button type="button" className="gates-btn" disabled={!loggedIn}
                    onClick={() => act("confirm", { resolve: "share" })}
                    title="Only if the two gates really share one antenna">
              Keep both
            </button>
          </div>
        </div>
      )}

      <div className="gates-actions">
        <button type="button" className="gates-btn gates-btn--primary"
                disabled={!loggedIn || !c.settled || !!issue} onClick={() => act("confirm")}>
          Confirm
        </button>
        <button type="button" className="gates-btn" disabled={!loggedIn} onClick={() => act("retry")}>Retry</button>
        <button type="button" className="gates-btn" disabled={!loggedIn} onClick={() => act("skip_closed")}>Skip: gate closed</button>
        <button type="button" className="gates-btn" disabled={!loggedIn} onClick={() => act("skip_not_fitted")}>Skip: no antenna</button>
        <button type="button" className="gates-btn gates-btn--danger" disabled={!loggedIn} onClick={() => act("faulty")}>Mark faulty</button>
        <button type="button" className="gates-link" disabled={!loggedIn || run.index === 0} onClick={() => act("back")}>← Back</button>
      </div>
    </div>
  );
}

function Review({ run, send, loggedIn }) {
  const show = (e) => (!e ? "unmapped" : e.status === "mapped" ? `${e.module_id}:${e.unit}` : STATUS_LABEL[e.status] ?? e.status);
  return (
    <div className="gates-step">
      <p className="gates-prompt">Review</p>
      {!run.review.scanned && (
        <p className="gates-muted">No bus scan answered, so the bus checks were skipped.</p>
      )}
      <Problems review={run.review} />
      <h4>{run.changes.length} change(s)</h4>
      {run.changes.length > 0 && (
        <ul className="gates-changes">
          {run.changes.map((c) => (
            <li key={c.gate}><span className="gates-mono">{c.gate}</span>: {show(c.before)} → <strong>{show(c.after)}</strong></li>
          ))}
        </ul>
      )}
      <div className="gates-actions">
        <button type="button" className="gates-btn gates-btn--primary" disabled={!loggedIn}
                onClick={() => send("gate_map_save_run")}>
          Save mapping
        </button>
        <button type="button" className="gates-link" disabled={!loggedIn}
                onClick={() => send("gate_map_action", { action: "back" })}>
          ← Back to the last gate
        </button>
      </div>
    </div>
  );
}

function RunPanel({ state, send, loggedIn }) {
  const run = state.run;
  return (
    <div className="gates-card">
      <div className="gates-card-head">
        <h3>Mapping run</h3>
        <button type="button" className="gates-btn gates-btn--danger" disabled={!loggedIn}
                onClick={() => {
                  if (window.confirm("Discard this mapping run? The saved map stays as it was.")) {
                    send("gate_map_discard_run");
                  }
                }}>
          Discard
        </button>
      </div>
      <p className="gates-muted">By {run.operator} · started {fmtTime(run.started_at)}</p>
      {run.phase === "review"
        ? <Review run={run} send={send} loggedIn={loggedIn} />
        : <RunStep run={run} send={send} loggedIn={loggedIn} />}
    </div>
  );
}

// ── Layout editor ──────────────────────────────────────────────────────────

function LayoutEditor({ state, send, loggedIn, draft, setDraft }) {
  const [size, setSize] = useState({
    cols: state.layout.cols, rows: state.layout.rows, gates_per_wall: state.layout.gates_per_wall,
  });
  const dirty = JSON.stringify(draft) !== JSON.stringify(state.layout);
  const resize = () => {
    if (!window.confirm("Rebuild the grid at this size? Every gate starts as fitted.")) return;
    setDraft(generateLayout(size.cols, size.rows, size.gates_per_wall));
  };
  const setAll = (fitted) => setDraft({ ...draft, gates: draft.gates.map((g) => ({ ...g, fitted })) });

  return (
    <div className="gates-card">
      <h3>Layout</h3>
      <p className="gates-muted">
        Click a gate on the grid to mark it fitted or not. Boxes are named column letter +
        row number seen from above (A1 top left); gates a-d run top to bottom along a
        vertical wall and left to right along a horizontal one.
      </p>
      {state.run && <p className="gates-problem gates-problem--warn">Finish or discard the mapping run to change the layout.</p>}
      <div className="gates-size">
        {[["cols", "Columns"], ["rows", "Rows"], ["gates_per_wall", "Gates per wall"]].map(([k, label]) => (
          <label key={k} className="gates-field gates-field--inline">
            {label}
            <input type="number" min={1} max={k === "gates_per_wall" ? 4 : 8} value={size[k]}
                   onChange={(e) => setSize({ ...size, [k]: Number(e.target.value) })} />
          </label>
        ))}
        <button type="button" className="gates-btn" onClick={resize}>Rebuild grid</button>
      </div>
      <div className="gates-actions">
        <button type="button" className="gates-btn" onClick={() => setAll(true)}>All fitted</button>
        <button type="button" className="gates-btn" onClick={() => setAll(false)}>None fitted</button>
        <span className="gates-muted">{draft.gates.filter((g) => g.fitted).length} of {draft.gates.length} gates fitted</span>
      </div>
      <div className="gates-actions">
        <button type="button" className="gates-btn gates-btn--primary"
                disabled={!loggedIn || !dirty || !!state.run}
                onClick={() => send("gate_map_save_layout", { layout: draft })}>
          Save layout
        </button>
        <button type="button" className="gates-link" disabled={!dirty} onClick={() => setDraft(state.layout)}>
          Revert
        </button>
      </div>
    </div>
  );
}

// Mirror of gate_map.generate_layout, so a resize previews before saving
// (the controller validates and regenerates it on save anyway).
function generateLayout(cols, rows, perWall) {
  const id = (c, r) => `${String.fromCharCode(65 + c)}${r + 1}`;
  const boxes = [];
  const walls = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      boxes.push({ id: id(c, r), col: c, row: r });
      if (c + 1 < cols) walls.push({ id: id(c, r) + id(c + 1, r), boxes: [id(c, r), id(c + 1, r)], orientation: "vertical" });
      if (r + 1 < rows) walls.push({ id: id(c, r) + id(c, r + 1), boxes: [id(c, r), id(c, r + 1)], orientation: "horizontal" });
    }
  }
  const gates = walls.flatMap((w) => Array.from({ length: perWall }, (_, i) => (
    { id: `${w.id}_${"abcd"[i]}`, wall: w.id, index: i, fitted: true })));
  return { version: 1, cols, rows, gates_per_wall: perWall, boxes, walls, gates };
}

// ── Page ───────────────────────────────────────────────────────────────────

const TABS = [
  ["map", "Map"],
  ["walk", "Walk-through"],
  ["run", "Mapping run"],
  ["layout", "Layout"],
];

function Gates() {
  const { state, hits, error, clearError, send } = useGateMap();
  const loggedIn = useIsLoggedIn();
  const [tab, setTab] = useState("map");
  const [scopeGates, setScopeGates] = useState(() => new Set());
  const [layoutDraft, setLayoutDraft] = useState(null);
  const flash = useFlash(hits);

  // A run in progress (from any browser) takes over the page.
  useEffect(() => { if (state?.run) setTab("run"); }, [state?.run?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (state && !layoutDraft) setLayoutDraft(state.layout); }, [state, layoutDraft]);
  useEffect(() => { if (state) setLayoutDraft(state.layout); }, [state?.layout]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!state) return <div className="gates-page"><p className="gates-muted">Loading gate map…</p></div>;

  const run = state.run;
  const editingLayout = tab === "layout" && layoutDraft;
  const layout = editingLayout ? layoutDraft : state.layout;
  const pickingScope = tab === "run" && !run;

  const onGateClick = editingLayout
    ? (gid) => setLayoutDraft({
      ...layoutDraft,
      gates: layoutDraft.gates.map((g) => (g.id === gid ? { ...g, fitted: !g.fitted } : g)),
    })
    : pickingScope
      ? (gid) => {
        if (!state.layout.gates.find((g) => g.id === gid)?.fitted) return;
        setScopeGates((prev) => {
          const next = new Set(prev);
          if (next.has(gid)) next.delete(gid); else next.add(gid);
          return next;
        });
      }
      : undefined;

  const entries = run && tab === "run" ? previewEntries(state, run) : state.map.entries;

  return (
    <div className="gates-page">
      <div className="gates-header">
        <h2>RFID gates</h2>
        <div className="gates-tabs" role="tablist">
          {TABS.map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k}
                    className={`gates-tab ${tab === k ? "gates-tab--active" : ""}`}
                    onClick={() => setTab(k)}>
              {label}{k === "run" && run ? " •" : ""}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="gates-error" role="alert">
          {error}
          <button type="button" className="gates-link" onClick={clearError}>dismiss</button>
        </div>
      )}

      <div className="gates-body">
        <div className="gates-grid-col">
          <HabitatGrid
            layout={layout}
            entries={editingLayout ? {} : entries}
            current={tab === "run" ? run?.gate : undefined}
            flash={flash}
            selected={pickingScope ? scopeGates : undefined}
            onGateClick={onGateClick}
            labelFor={describeEntry}
          />
          <Legend />
          {state.map.updated_at && <p className="gates-muted">Map saved {fmtTime(state.map.updated_at)}</p>}
        </div>
        <div className="gates-side">
          {tab === "map" && <><BusCheck state={state} send={send} /><MapTable state={state} /><ChangeLog changes={state.changes} /></>}
          {tab === "walk" && <WalkThrough state={state} hits={hits} send={send} />}
          {tab === "run" && (run
            ? <RunPanel state={state} send={send} loggedIn={loggedIn} />
            : <StartRun state={state} send={send} loggedIn={loggedIn}
                        scopeGates={scopeGates} setScopeGates={setScopeGates} />)}
          {editingLayout && (
            <LayoutEditor state={state} send={send} loggedIn={loggedIn}
                          draft={layoutDraft} setDraft={setLayoutDraft} />
          )}
        </div>
      </div>
    </div>
  );
}

// The map as the run would save it, so the grid shows progress. Mirrors
// MappingRun.entries(): base map + this run's results (null = unmapped).
function previewEntries(state, run) {
  const out = { ...state.map.entries };
  for (const [gate, entry] of Object.entries(run.results ?? {})) {
    if (entry === null) delete out[gate]; else out[gate] = entry;
  }
  return out;
}

export default Gates;
