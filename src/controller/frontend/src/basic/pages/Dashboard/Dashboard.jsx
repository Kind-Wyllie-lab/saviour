import React, { useState, useMemo, useEffect, useRef } from "react";
import "./Dashboard.css";

import useModules from "/src/hooks/useModules";
import useDashboardViews from "/src/hooks/useDashboardViews";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import DraggableTile from "/src/basic/components/DraggableTile/DraggableTile";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";

// MJPEG stream port by module type
const STREAM_PORTS = {
  camera:     8080,
  microphone: 8081,
  ttl:        8082,
  rfid:       8083,
};

const COMPACT_BREAKPOINT = 1280;

// Free drag/resize canvas of tiles. On the wide layout the dashboard is a
// canvas of "widgets" — camera/sensor streams plus status widgets (health
// summary, module list) — arranged into named "Saved Views" persisted on the
// controller (see useDashboardViews). The compact layout keeps the simple
// stacked view.
const ARRANGE_GAP = 12;
const DEFAULT_TILE_W = 440;
const TILE_CHROME_PX = 64; // grip strip + card label header + padding, for flow layout
// The default ("unsaved") layout puts the status widgets in a fixed-width
// right-hand column, same width for both, with the streams filling the rest —
// mirroring the old fixed dashboard. Both start content-sized (no dead space);
// a manual resize turns a widget into a fixed-height scrolling box.
const PANEL_COL_W = 360;
const PANEL_H_ESTIMATE = 210; // fallback until a widget reports its real height
// A content-sized panel (health summary, module list, …) is capped to
// whatever room is actually left below it on THIS screen, never sillier-tall
// than this even with room to spare — a small laptop gets a short, scrolling
// box instead of a widget that pushes the rest of the canvas off-screen.
const PANEL_MAX_H_CAP = 480;
const PANEL_MIN_H = 160;
const TILE_GRIP_PX = 20;      // .dash-tile__grip height — the drag strip above every body
const STREAM_TILE_CHROME = 46; // grip + stream-card header, per stream-tile row

// Largest aspect-locked cell (w = h * ratio) for `n` tiles laid out in some
// cols x rows grid that still fits a boxW x boxH area — tries every column
// count and keeps the one giving the biggest cell. Used so the default view's
// streams fill the space left of the status column instead of flowing at a
// fixed width. Mirrors the pre-widget-board dashboard's fitted grid.
function fitStreamGrid(boxW, boxH, n, ratio) {
  if (!boxW || !boxH || n < 1) return null;
  let best = null;
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    // 2px per tile for the .dash-tile border (both axes).
    const availW = boxW - ARRANGE_GAP * (cols - 1) - 2 * cols;
    const availH = boxH - (STREAM_TILE_CHROME + ARRANGE_GAP + 2) * rows;
    if (availW <= 0 || availH <= 0) continue;
    let w = availW / cols;
    let h = w / (ratio || 16 / 9);
    if (h * rows > availH) { h = availH / rows; w = h * (ratio || 16 / 9); }
    const area = w * h;
    if (area > 0 && (!best || area > best.area)) best = { cols, w, h, area };
  }
  return best;
}

const LS_VIEW = "saviour_dashboard_view";       // last-selected view id (per browser)
const LS_LEGACY_LAYOUT = "saviour_dashboard_layout"; // dead Phase 1 key — cleared on mount

const SYNTHETIC = "__unsaved__";

function readLS(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : v;
  } catch {
    return fallback;
  }
}
function writeLS(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

function useIsCompact() {
  const [isCompact, setIsCompact] = useState(
    () => window.innerWidth <= COMPACT_BREAKPOINT
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${COMPACT_BREAKPOINT}px)`);
    const handler = (e) => setIsCompact(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return isCompact;
}

// Tracks an element's content-box size. Callback ref so it reattaches when
// the wide/compact layouts swap which element exists.
function useElementSize() {
  const [node, setNode] = useState(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, size];
}

// Stream port + display label for a module, or null if it has no stream.
function streamInfo(m) {
  if (m.type?.includes("camera")) {
    return { port: STREAM_PORTS.camera, label: m.name, isCamera: true };
  }
  if (m.type === "microphone") {
    return { port: STREAM_PORTS.microphone, label: `${m.name} - Audio` };
  }
  if (m.type === "ttl") {
    return { port: STREAM_PORTS.ttl, label: `${m.name} - TTL` };
  }
  if (m.type === "rfid") {
    return {
      port: m.config?.monitoring?._port ?? STREAM_PORTS.rfid,
      label: `${m.name} - RFID`,
    };
  }
  return null;
}
const isStreamModule = (m) => streamInfo(m) != null;

// The default widget set for a fresh / unsaved view: every module stream
// (cameras first) plus the two status widgets.
function defaultWidgets(modules) {
  const streams = modules
    .filter(isStreamModule)
    .slice()
    .sort((a, b) => {
      const ac = a.type?.includes("camera") ? 0 : 1;
      const bc = b.type?.includes("camera") ? 0 : 1;
      return ac - bc;
    })
    .map((m) => ({ id: `stream:${m.id}`, type: "stream", target: m.id }));
  return [
    ...streams,
    { id: "widget:health", type: "health" },
    { id: "widget:module-list", type: "module-list" },
  ];
}

function Dashboard() {
  const { moduleList } = useModules();
  const loggedIn = useIsLoggedIn();
  const {
    views, defaultId, loaded, lastSaved, error, clearError,
    saveView, deleteView, setDefaultView,
  } = useDashboardViews();

  const isCompact = useIsCompact();
  const [canvasRef, canvasSize] = useElementSize();
  const [ratios, setRatios] = useState({});
  const noteRatio = (id) => (r) => {
    setRatios((p) => (p[id] === r ? p : { ...p, [id]: r }));
  };
  // Rendered height of each content-sized ("both", not yet resized) widget,
  // so the default layout can stack the right-hand column tightly.
  const [autoHeights, setAutoHeights] = useState({});
  const noteAutoHeight = (id) => (px) =>
    setAutoHeights((p) => (Math.abs((p[id] || 0) - px) < 2 ? p : { ...p, [id]: px }));

  // Stacking order for overlapping tiles — the last one interacted with wins.
  // Kept as an id list (bounded by widget count) so z-index values stay small
  // and never approach modal/overlay ranges. Session-only (not persisted).
  const [zOrder, setZOrder] = useState([]);
  const bringToFront = (id) =>
    setZOrder((o) => (o[o.length - 1] === id ? o : [...o.filter((x) => x !== id), id]));

  const [activeViewId, setActiveViewId] = useState(() => readLS(LS_VIEW, ""));
  // Working copy of the active view. `id: null` == an unsaved bootstrap view
  // whose widget list is derived live from the connected modules.
  const [draft, setDraft] = useState({
    id: null, name: "", group: "", widgets: [], layout: {},
  });

  // Drop the dead Phase 1 per-browser layout key so it can't resurface.
  useEffect(() => {
    try { localStorage.removeItem(LS_LEGACY_LAYOUT); } catch { /* ignore */ }
  }, []);

  // Keep activeViewId pointing at something real. SYNTHETIC is an explicit,
  // always-valid choice (the auto "Default layout"); "" is "not chosen yet"
  // and resolves to the server default view, else the first, else SYNTHETIC.
  useEffect(() => {
    if (!loaded) return;
    if (activeViewId === SYNTHETIC) return;
    if (activeViewId && views.some((v) => v.id === activeViewId)) return;
    const fallback =
      defaultId && views.some((v) => v.id === defaultId)
        ? defaultId
        : views[0]?.id || SYNTHETIC;
    if (fallback !== activeViewId) {
      setActiveViewId(fallback);
      writeLS(LS_VIEW, fallback);
    }
  }, [loaded, views, defaultId, activeViewId]);

  // Load the draft from the selected view. Keyed on the id (not the view
  // object) so a background broadcast doesn't stomp an in-progress drag.
  const viewsRef = useRef(views);
  viewsRef.current = views;
  const draftLoadedRef = useRef(false);
  useEffect(() => {
    const v = viewsRef.current.find((x) => x.id === activeViewId);
    draftLoadedRef.current = false;
    if (v) {
      setDraft({
        id: v.id,
        name: v.name,
        group: v.group || "",
        widgets: Array.isArray(v.widgets) ? v.widgets : [],
        layout: v.layout && typeof v.layout === "object" ? v.layout : {},
      });
    } else {
      // The unsaved "default" view is never persisted — always recompute it
      // fresh (fitted stream grid + right-hand status column) so a refresh
      // lands on the same layout as "Reset layout".
      setDraft({ id: null, name: "", group: "", widgets: [], layout: {} });
    }
    const t = setTimeout(() => { draftLoadedRef.current = true; }, 0);
    return () => clearTimeout(t);
  }, [activeViewId, loaded]);

  // Adopt a freshly *created* view (New / Save as / Duplicate). Those set
  // pendingCreateRef right before calling saveView; an ordinary save ack (a
  // drag auto-save, or the flush in pickView) must NOT move the selection —
  // otherwise switching to "Default layout" gets yanked back to the view its
  // own flush-save just re-acked.
  const pendingCreateRef = useRef(false);
  const adoptedAtRef = useRef(0);
  useEffect(() => {
    if (!lastSaved?.id || adoptedAtRef.current === lastSaved._at) return;
    if (!pendingCreateRef.current) { adoptedAtRef.current = lastSaved._at; return; }
    if (!views.some((v) => v.id === lastSaved.id)) return; // wait for the broadcast
    adoptedAtRef.current = lastSaved._at;
    pendingCreateRef.current = false;
    setActiveViewId(lastSaved.id);
    writeLS(LS_VIEW, lastSaved.id);
  }, [lastSaved, views]);

  const groups = useMemo(
    () => [...new Set(moduleList.map((m) => m.group).filter(Boolean))].sort(),
    [moduleList]
  );
  const groupModules = useMemo(
    () => (draft.group ? moduleList.filter((m) => m.group === draft.group) : moduleList),
    [moduleList, draft.group]
  );

  // The widgets actually on the canvas: an explicit list for a saved view,
  // derived live from the modules for the unsaved bootstrap view.
  const widgets = useMemo(
    () => (draft.id ? draft.widgets : defaultWidgets(moduleList)),
    [draft.id, draft.widgets, moduleList]
  );

  // Effective geometry per widget: an explicitly-placed {x,y,width,height?}
  // from the view wins; anything unplaced falls back to the auto arrangement —
  // status widgets in a fixed-width right-hand column, streams sized by a
  // fitted grid to fill the space to their left. This is also exactly what
  // "Reset layout" produces (it just clears the explicit placements).
  const effectiveLayout = useMemo(() => {
    const canvasW = canvasSize.width || 1200;
    const canvasH = canvasSize.height || 800;
    const out = {};

    const streams = widgets.filter((w) => w.type === "stream");
    const panels = widgets.filter((w) => w.type !== "stream");

    const rightX = Math.max(DEFAULT_TILE_W, canvasW - PANEL_COL_W - ARRANGE_GAP);
    const leftW = Math.max(320, rightX - ARRANGE_GAP);

    // Streams: fitted grid filling the left region; a fixed flow only as a
    // fallback before the canvas has been measured.
    const repRatio = ratios[streams[0]?.id] || Object.values(ratios)[0] || 16 / 9;
    const fit = streams.length
      ? fitStreamGrid(leftW - 2, canvasH - 4, streams.length, repRatio)
      : null;
    const flowPerRow = Math.max(
      1, Math.floor((leftW + ARRANGE_GAP) / (DEFAULT_TILE_W + ARRANGE_GAP))
    );
    const flowRowH =
      Math.round(DEFAULT_TILE_W / (16 / 9)) + TILE_CHROME_PX + ARRANGE_GAP;
    streams.forEach((w, i) => {
      if (draft.layout[w.id]) { out[w.id] = draft.layout[w.id]; return; }
      out[w.id] = fit
        ? {
            x: (i % fit.cols) * (fit.w + ARRANGE_GAP),
            y: Math.floor(i / fit.cols) * (fit.h + STREAM_TILE_CHROME + ARRANGE_GAP),
            width: fit.w,
          }
        : {
            x: (i % flowPerRow) * (DEFAULT_TILE_W + ARRANGE_GAP),
            y: Math.floor(i / flowPerRow) * flowRowH,
            width: DEFAULT_TILE_W,
          };
    });

    // Status widgets: fixed-width right column, stacked by their measured
    // heights (grip strip included) so there's no gap between them.
    let y = ARRANGE_GAP;
    panels.forEach((w) => {
      if (draft.layout[w.id]) { out[w.id] = draft.layout[w.id]; return; }
      const roomBelow = canvasH - y - ARRANGE_GAP - TILE_GRIP_PX;
      const maxHeight = Math.min(PANEL_MAX_H_CAP, Math.max(PANEL_MIN_H, roomBelow));
      out[w.id] = { x: rightX, y, width: PANEL_COL_W, maxHeight }; // content-sized, capped
      y += TILE_GRIP_PX + (autoHeights[w.id] ?? PANEL_H_ESTIMATE) + ARRANGE_GAP;
    });

    return out;
  }, [widgets, draft.layout, canvasSize.width, canvasSize.height, autoHeights, ratios]);

  // Refs so the debounced auto-save closure always sees current values
  // without re-arming on every render.
  const draftRef = useRef(draft); draftRef.current = draft;
  const widgetsRef = useRef(widgets); widgetsRef.current = widgets;
  const effLayoutRef = useRef(effectiveLayout); effLayoutRef.current = effectiveLayout;

  const materialisedLayout = () => {
    const out = {};
    widgetsRef.current.forEach((w) => {
      const entry =
        draftRef.current.layout[w.id] ||
        effLayoutRef.current[w.id] ||
        { x: 0, y: 0, width: DEFAULT_TILE_W };
      // maxHeight is a derived, this-screen-only cap (see effectiveLayout) —
      // never persist it, so a view saved on a small screen doesn't stay
      // capped short when reopened on a bigger one.
      const { maxHeight: _mh, ...persisted } = entry;
      out[w.id] = persisted;
    });
    return out;
  };
  const cleanWidgets = () =>
    widgetsRef.current.map(({ id, type, target }) =>
      type === "stream" ? { id, type, target } : { id, type });
  const viewPayload = (over = {}) => {
    const d = draftRef.current;
    return {
      ...(d.id ? { id: d.id } : {}),
      name: d.name,
      group: d.group || "",
      widgets: cleanWidgets(),
      layout: materialisedLayout(),
      ...over,
    };
  };

  // Auto-save edits to a saved view when logged in. The unsaved bootstrap
  // view (no id) and guests persist nothing — an explicit "Save as view".
  const [pendingSave, setPendingSave] = useState(false);
  useEffect(() => {
    if (!draftLoadedRef.current) return;
    if (!loggedIn || !draftRef.current.id) return;
    setPendingSave(true);
    const t = setTimeout(() => saveView(viewPayload()), 700);
    return () => clearTimeout(t);
  }, [draft, loggedIn, saveView]); // eslint-disable-line react-hooks/exhaustive-deps
  // A save ack (lastSaved changes on every persisted write) clears the flag.
  useEffect(() => { setPendingSave(false); }, [lastSaved]);

  // ── draft mutations ──────────────────────────────────────────────────
  const updateTile = (id, patch) => setDraft((d) => ({
    ...d,
    layout: {
      ...d.layout,
      [id]: {
        ...(d.layout[id] || effLayoutRef.current[id] || { x: 0, y: 0, width: DEFAULT_TILE_W }),
        ...patch,
      },
    },
  }));
  const removeWidget = (id) => setDraft((d) => {
    const layout = { ...d.layout };
    delete layout[id];
    return { ...d, widgets: d.widgets.filter((w) => w.id !== id), layout };
  });
  const addFromCatalog = (key) => {
    let w;
    if (key === "widget:health") w = { id: key, type: "health" };
    else if (key === "widget:module-list") w = { id: key, type: "module-list" };
    else w = { id: key, type: "stream", target: key.slice("stream:".length) };
    setDraft((d) => {
      const n = d.widgets.length % 6;
      const isStream = w.type === "stream";
      const slotY = 24 + n * 28;
      const canvasH = canvasSize.height || 800;
      const roomBelow = canvasH - slotY - ARRANGE_GAP - TILE_GRIP_PX;
      const slot = {
        x: 24 + n * 28,
        y: slotY,
        width: isStream ? DEFAULT_TILE_W : PANEL_COL_W, // panels stay content-sized
        ...(isStream ? {} : { maxHeight: Math.min(PANEL_MAX_H_CAP, Math.max(PANEL_MIN_H, roomBelow)) }),
      };
      return { ...d, widgets: [...d.widgets, w], layout: { ...d.layout, [w.id]: slot } };
    });
  };
  const resetLayout = () => setDraft((d) => ({ ...d, layout: {} }));

  // ── view management (all admin-gated server-side) ────────────────────
  const newView = () => {
    const name = window.prompt("New view name", "Overview");
    if (name && name.trim()) {
      pendingCreateRef.current = true;
      saveView({ name: name.trim(), group: "", widgets: defaultWidgets(moduleList), layout: {} });
    }
  };
  const saveAsView = () => {
    const name = window.prompt("Name this view", "Overview");
    if (name && name.trim()) {
      pendingCreateRef.current = true;
      saveView(viewPayload({ id: undefined, name: name.trim() }));
    }
  };
  const duplicateView = () => {
    const name = window.prompt("Name for the copy", `${draft.name} copy`);
    if (name && name.trim()) {
      pendingCreateRef.current = true;
      saveView(viewPayload({ id: undefined, name: name.trim() }));
    }
  };
  const renameView = () => {
    const name = window.prompt("Rename view", draft.name);
    if (name && name.trim()) setDraft((d) => ({ ...d, name: name.trim() }));
  };
  const removeView = () => {
    if (draft.id && window.confirm(`Delete the "${draft.name}" view?`)) {
      deleteView(draft.id);
    }
  };
  const pickView = (id) => {
    // Flush any edit still inside the auto-save debounce before leaving.
    if (loggedIn && draftLoadedRef.current && draftRef.current.id) {
      saveView(viewPayload());
    }
    setActiveViewId(id);
    writeLS(LS_VIEW, id);
  };

  // Widgets that could still be added to the current saved view.
  const catalog = useMemo(() => {
    if (!draft.id) return [];
    const present = new Set(widgets.map((w) => w.id));
    const out = [];
    if (!present.has("widget:health")) out.push({ key: "widget:health", label: "Health summary" });
    if (!present.has("widget:module-list")) out.push({ key: "widget:module-list", label: "Module list" });
    groupModules.filter(isStreamModule).forEach((m) => {
      const id = `stream:${m.id}`;
      if (!present.has(id)) out.push({ key: id, label: streamInfo(m).label });
    });
    return out;
  }, [draft.id, widgets, groupModules]);

  const canManage = loggedIn;
  const savingHint = !draft.id
    ? "Default layout — Save as a view to keep changes"
    : !loggedIn
    ? "Log in to save changes"
    : pendingSave
    ? "Saving…"
    : "All changes saved";

  return (
    <div className="dashboard">
      {!isCompact && (
        <div className="dashboard-toolbar">
          <div className="dashboard-view-picker">
            <label htmlFor="view-select">View:</label>
            <select
              id="view-select"
              value={draft.id || SYNTHETIC}
              onChange={(e) => e.target.value && pickView(e.target.value)}
            >
              <option value={SYNTHETIC}>Default layout</option>
              {views.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}{v.id === defaultId ? "  ★" : ""}
                </option>
              ))}
            </select>

            {!draft.id ? (
              <button
                type="button"
                className="dashboard-panel-toggle"
                onClick={saveAsView}
                disabled={!canManage}
                title={canManage ? "Save this layout as a named view" : "Log in to save views"}
              >
                Save as view…
              </button>
            ) : (
              <>
                <button type="button" className="dashboard-panel-toggle" onClick={renameView} disabled={!canManage}>Rename</button>
                <button type="button" className="dashboard-panel-toggle" onClick={duplicateView} disabled={!canManage}>Duplicate</button>
                <button
                  type="button"
                  className="dashboard-panel-toggle"
                  onClick={() => setDefaultView(draft.id)}
                  disabled={!canManage || draft.id === defaultId}
                  title="Show this view by default"
                >
                  {draft.id === defaultId ? "Default ★" : "Set default"}
                </button>
                <button type="button" className="dashboard-panel-toggle" onClick={removeView} disabled={!canManage}>Delete</button>
              </>
            )}
            <button type="button" className="dashboard-panel-toggle" onClick={newView} disabled={!canManage}>New view</button>
          </div>

          <div className="dashboard-toolbar-right">
            {groups.length > 0 && draft.id && (
              <div className="dashboard-group-filter">
                <label htmlFor="group-select">Group:</label>
                <select
                  id="group-select"
                  value={draft.group || "all"}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, group: e.target.value === "all" ? "" : e.target.value }))
                  }
                >
                  <option value="all">All modules</option>
                  {groups.map((g) => (
                    <option key={g} value={g}>{g}</option>
                  ))}
                </select>
              </div>
            )}
            {draft.id && catalog.length > 0 && (
              <select
                className="dashboard-add-widget"
                value=""
                onChange={(e) => { if (e.target.value) addFromCatalog(e.target.value); e.target.value = ""; }}
              >
                <option value="">+ Add widget</option>
                {catalog.map((c) => (
                  <option key={c.key} value={c.key}>{c.label}</option>
                ))}
              </select>
            )}
            <button
              type="button"
              className="dashboard-panel-toggle"
              onClick={resetLayout}
              title="Clear the custom positions and re-flow the tiles"
            >
              Reset layout
            </button>
            <span className="dashboard-saving-hint">{savingHint}</span>
          </div>

          {error && (
            <div className="dashboard-view-error" role="alert">
              {error}
              <button type="button" onClick={clearError} aria-label="Dismiss">✕</button>
            </div>
          )}
        </div>
      )}

      {isCompact ? (
        /* ── Compact: stream grid + status below ── */
        <div className="dashboard-compact">
          {moduleList.filter(isStreamModule).length === 0 ? (
            <div className="dashboard-no-cameras">No streams connected</div>
          ) : (
            <div
              className="dashboard-compact-streams"
              style={{ gridTemplateColumns: `repeat(${Math.min(4, Math.max(1, moduleList.filter(isStreamModule).length))}, 1fr)` }}
            >
              {moduleList.filter(isStreamModule).map((m) => {
                const info = streamInfo(m);
                return (
                  <MJPEGStreamCard
                    key={m.id}
                    ip={m.ip}
                    port={info.port}
                    label={info.label}
                    isRecording={m.status === "RECORDING"}
                    syncStatus={m.config_sync_status}
                    className={info.isCamera ? "" : "mjpeg-stream-card--fit"}
                  />
                );
              })}
            </div>
          )}
          <div className="dashboard-compact-panel">
            <HealthSummaryWidget />
            <ModuleList modules={moduleList} />
          </div>
        </div>
      ) : (
        /* ── Wide: every widget is a free-positioned tile on one canvas ── */
        <div className="dashboard-main">
          <div className="dashboard-arrange-canvas" ref={canvasRef}>
            {widgets.map((w) => {
              const t = effectiveLayout[w.id] || { x: 0, y: 0, width: DEFAULT_TILE_W };
              const isStream = w.type === "stream";
              const mod = isStream ? moduleList.find((m) => m.id === w.target) : null;
              if (isStream && draft.group && (!mod || mod.group !== draft.group)) {
                return null;
              }
              const info = isStream && mod ? streamInfo(mod) : null;
              return (
                <DraggableTile
                  key={w.id}
                  x={t.x}
                  y={t.y}
                  width={t.width}
                  height={t.height}
                  maxHeight={t.maxHeight}
                  zIndex={zOrder.indexOf(w.id) + 1 || undefined}
                  ratio={isStream ? (ratios[w.id] || 16 / 9) : undefined}
                  resize={isStream ? "aspect" : "both"}
                  bounds={canvasSize}
                  onChange={(patch) => updateTile(w.id, patch)}
                  onRemove={draft.id ? () => removeWidget(w.id) : undefined}
                  onAutoHeight={isStream ? undefined : noteAutoHeight(w.id)}
                  onActivate={() => bringToFront(w.id)}
                >
                  {isStream ? (
                    mod && info ? (
                      <MJPEGStreamCard
                        ip={mod.ip}
                        port={info.port}
                        label={info.label}
                        isRecording={mod.status === "RECORDING"}
                        onAspectRatio={noteRatio(w.id)}
                        syncStatus={mod.config_sync_status}
                        settingsId={mod.id}
                      />
                    ) : (
                      <div className="dashboard-widget-missing">
                        {w.target}<br />not connected
                      </div>
                    )
                  ) : w.type === "health" ? (
                    <HealthSummaryWidget />
                  ) : (
                    <ModuleList modules={groupModules} />
                  )}
                </DraggableTile>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default Dashboard;
