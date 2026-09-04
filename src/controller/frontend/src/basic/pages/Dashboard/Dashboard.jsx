import React, { useState, useMemo, useEffect } from "react";
import "./Dashboard.css";

import useModules from "/src/hooks/useModules";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
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

// Tracks an element's content-box size. Uses a callback ref (not
// useRef+one-shot useEffect) so it reattaches correctly when the element
// itself mounts/unmounts — e.g. the wide/compact dashboard layouts swap
// which grid div exists as the window crosses COMPACT_BREAKPOINT.
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

const GRID_GAP_PX = 12;
// Fixed vertical chrome per card outside the video box itself (the ~8px
// top/bottom padding plus the uppercase label header) — subtracted from the
// per-row height budget so the fitted grid doesn't overshoot by a few
// pixels per row and reintroduce a scrollbar.
const CARD_CHROME_PX = 40;
// .mjpeg-stream-card's left+right padding (8px each side, see
// MJPEGStreamCard.css). The card is width:fit-content clamped to the grid
// column via max-width:100%, but the video box inside it gets an explicit
// px width — without subtracting this, that width leaves no room for the
// card's own padding and the video overflows the card by this amount,
// producing a horizontal scrollbar inside the card.
const CARD_PADDING_X = 16;

// Largest (width, height) matching `ratio` that still fits `cols` columns x
// `rows` rows inside a `containerW` x `containerH` box — so the camera grid
// always fits the available space instead of overflowing and forcing the
// dashboard to scroll.
function fitCellSize(containerW, containerH, cols, rows, ratio) {
  if (!containerW || !containerH || cols < 1 || rows < 1) return null;
  const availW = containerW - GRID_GAP_PX * (cols - 1) - CARD_PADDING_X * cols;
  const availH = containerH - GRID_GAP_PX * (rows - 1) - CARD_CHROME_PX * rows;
  if (availW <= 0 || availH <= 0) return null;
  let width = availW / cols;
  let height = width / ratio;
  if (height * rows > availH) {
    height = availH / rows;
    width = height * ratio;
  }
  return { width, height };
}

// Picks the column count (1..n) that yields the largest per-card area for
// `n` cards in a containerW x containerH box. A fixed column count picked
// from `n` alone (the old heuristic) can land on a layout that's row-bound
// — e.g. 2 cards stacked in 1 column, 2 rows — leaving each card's width
// unused once fitCellSize shrinks it to fit the row budget. Trying every
// column count and keeping the biggest result finds the arrangement that
// actually uses the most of the available box. `ratio` is the camera
// ratio; non-camera streams (spectrogram, RFID timeline) just letterbox
// within their cell via object-fit: contain.
function bestGridLayout(containerW, containerH, n, ratio) {
  if (!containerW || !containerH || n === 0) return null;
  let best = null;
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    const cellSize = fitCellSize(containerW, containerH, cols, rows, ratio);
    if (!cellSize) continue;
    const area = cellSize.width * cellSize.height;
    if (!best || area > best.area) best = { cols, cellSize, area };
  }
  return best;
}

function Dashboard() {
  const { moduleList } = useModules();
  const [selectedGroup, setSelectedGroup] = useState("all");
  const isCompact = useIsCompact();
  const [camerasGridRef, camerasGridSize] = useElementSize();
  // Real aspect ratio of the camera streams, discovered from the first
  // loaded frame — used to fit the grid (see fitCellSize above). Assumes a
  // uniform ratio across camera modules, which holds for a real rig.
  const [streamRatio, setStreamRatio] = useState(16 / 9);
  // The right-hand status panel (health + module list) is the single biggest
  // consumer of horizontal space on the wide layout — let it be hidden so
  // the stream grid gets the full width. Persisted; wide layout only (the
  // compact layout stacks it below the grid, where it costs no width).
  const [panelHidden, setPanelHidden] = useState(() => {
    try { return localStorage.getItem("saviour_dashboard_panel_hidden") === "1"; }
    catch { return false; }
  });
  const togglePanel = () => setPanelHidden((v) => {
    const next = !v;
    try { localStorage.setItem("saviour_dashboard_panel_hidden", next ? "1" : "0"); }
    catch { /* storage unavailable — toggle still works for this session */ }
    return next;
  });

  const groups = useMemo(() => {
    const names = [...new Set(moduleList.map(m => m.group).filter(Boolean))].sort();
    return names;
  }, [moduleList]);

  const visibleModules = selectedGroup === "all"
    ? moduleList
    : moduleList.filter(m => m.group === selectedGroup);

  const cameraModules = visibleModules.filter(m => m.type?.includes("camera"));
  const micModules    = visibleModules.filter(m => m.type === "microphone");
  const ttlModules    = visibleModules.filter(m => m.type === "ttl");
  const rfidModules   = visibleModules.filter(m => m.type === "rfid");

  // Flat ordered list of every module stream — cameras first, then the
  // sensor streams. Both dashboard layouts render this one list so nothing
  // gets stranded in a scrolling side panel.
  const allStreams = useMemo(() => [
    ...cameraModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.camera, isCamera: true,
      label: m.name, isRecording: m.status === "RECORDING",
      syncStatus: m.config_sync_status,
    })),
    ...micModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.microphone,
      label: `${m.name} - Audio`, isRecording: m.status === "RECORDING",
      syncStatus: m.config_sync_status,
    })),
    ...ttlModules.map(m => ({
      id: m.id, ip: m.ip, port: STREAM_PORTS.ttl,
      label: `${m.name} - TTL`, isRecording: m.status === "RECORDING",
      syncStatus: m.config_sync_status,
    })),
    ...rfidModules.map(m => ({
      id: m.id, ip: m.ip, port: m.config?.monitoring?._port ?? STREAM_PORTS.rfid,
      label: `${m.name} - RFID`, isRecording: m.status === "RECORDING",
      syncStatus: m.config_sync_status,
    })),
  ], [cameraModules, micModules, ttlModules, rfidModules]);

  // Column count used before the grid's box has been measured yet (first
  // render) — a reasonable guess so nothing flashes unstyled.
  const streamColsFallback = (() => { const n = allStreams.length; return n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4; })();
  const streamLayout = useMemo(
    () => bestGridLayout(camerasGridSize.width, camerasGridSize.height, allStreams.length, streamRatio),
    [camerasGridSize.width, camerasGridSize.height, allStreams.length, streamRatio]
  );
  const streamCols = streamLayout?.cols ?? streamColsFallback;
  const streamCellSize = streamLayout?.cellSize ?? null;

  const compactCols = streamColsFallback;

  return (
    <div className="dashboard">
      {(groups.length > 0 || !isCompact) && (
        <div className="dashboard-toolbar">
          {groups.length > 0 && (
            <div className="dashboard-group-filter">
              <label htmlFor="group-select">Group:</label>
              <select
                id="group-select"
                value={selectedGroup}
                onChange={e => setSelectedGroup(e.target.value)}
              >
                <option value="all">All modules</option>
                {groups.map(g => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          )}
          {!isCompact && (
            <button
              type="button"
              className="dashboard-panel-toggle"
              onClick={togglePanel}
              title={panelHidden ? "Show the status panel" : "Hide the status panel"}
            >
              {panelHidden ? "Show panel" : "Hide panel"}
            </button>
          )}
        </div>
      )}

      {isCompact ? (
        /* ── Compact: stream grid + status below ── */
        <div className="dashboard-compact">
          {allStreams.length === 0 ? (
            <div className="dashboard-no-cameras">No streams connected</div>
          ) : (
            <div className="dashboard-compact-streams" style={{ gridTemplateColumns: `repeat(${compactCols}, 1fr)` }}>
              {allStreams.map(s => (
                <MJPEGStreamCard
                  key={s.id}
                  ip={s.ip}
                  port={s.port}
                  label={s.label}
                  isRecording={s.isRecording}
                  syncStatus={s.syncStatus}
                  className={s.isCamera ? "" : "mjpeg-stream-card--fit"}
                />
              ))}
            </div>
          )}

          <div className="dashboard-compact-panel">
            <HealthSummaryWidget />
            <ModuleList modules={visibleModules} />
          </div>
        </div>
      ) : (
        /* ── Wide: all streams in the fitted grid, status panel right ── */
        <div className="dashboard-main">
          <div
            className="dashboard-cameras"
            ref={camerasGridRef}
            style={{
              gridTemplateColumns: `repeat(${streamCols}, 1fr)`,
              ...(streamCellSize && {
                "--cell-w": `${streamCellSize.width}px`,
                "--cell-h": `${streamCellSize.height}px`,
              }),
            }}
          >
            {allStreams.length === 0 ? (
              <div className="dashboard-no-cameras">No streams connected</div>
            ) : (
              allStreams.map(s => (
                <MJPEGStreamCard
                  key={s.id}
                  ip={s.ip}
                  port={s.port}
                  label={s.label}
                  isRecording={s.isRecording}
                  onAspectRatio={s.isCamera ? setStreamRatio : undefined}
                  syncStatus={s.syncStatus}
                  className={s.isCamera ? "" : "mjpeg-stream-card--fit"}
                />
              ))
            )}
          </div>

          {!panelHidden && (
            <div className="dashboard-panel">
              <HealthSummaryWidget />
              <ModuleList modules={visibleModules} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default Dashboard;
