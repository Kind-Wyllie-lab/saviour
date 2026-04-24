import React, { useState, useEffect, useRef } from "react";
import "./HabitatLivestreamGrid.css";
import { NavLink } from "react-router-dom";
import HabitatLivestreamCard from "../HabitatLivestreamCard/HabitatLivestreamCard";

const STREAM_REFRESH_MS = 60 * 1000;

const COLUMNS = ["A", "B", "C", "D"];
const ROWS = [4, 3, 2, 1];
const ALL_CELLS = COLUMNS.flatMap(col => ROWS.map(row => `${col}${row}`));

function parseCell(cell) {
  return { col: COLUMNS.indexOf(cell[0]), row: ROWS.indexOf(Number(cell[1])) };
}

function getSelectionRect(start, end) {
  if (!start || !end) return null;
  const a = parseCell(start);
  const b = parseCell(end);
  return {
    colStart: Math.min(a.col, b.col), colEnd: Math.max(a.col, b.col),
    rowStart: Math.min(a.row, b.row), rowEnd: Math.max(a.row, b.row),
  };
}

function HabitatLivestreamGrid({ modules }) {
  const [startCell, setStartCell]     = useState("");
  const [endCell, setEndCell]         = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [refreshKey, setRefreshKey]   = useState(0);
  const settingsRef = useRef(null);

  const selectionRect = getSelectionRect(startCell, endCell);
  const visibleCols = selectionRect
    ? COLUMNS.slice(selectionRect.colStart, selectionRect.colEnd + 1) : COLUMNS;
  const visibleRows = selectionRect
    ? ROWS.slice(selectionRect.rowStart, selectionRect.rowEnd + 1) : ROWS;

  const modulesByName = React.useMemo(() =>
    Object.values(modules).reduce((acc, m) => {
      if (!acc[m.name]) acc[m.name] = [];
      acc[m.name].push(m);
      return acc;
    }, {}),
  [modules]);

  useEffect(() => {
    const t = setInterval(() => setRefreshKey(k => k + 1), STREAM_REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  // Close popover on outside click
  useEffect(() => {
    if (!showSettings) return;
    const handler = (e) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target))
        setShowSettings(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showSettings]);

  const hasSubset = !!(startCell || endCell);

  return (
    <div className="habitat-grid-wrapper">

      {/* Grid */}
      <div
        className="habitat-grid"
        style={{
          gridTemplateColumns: `repeat(${visibleCols.length}, 1fr)`,
          gridTemplateRows:    `repeat(${visibleRows.length}, 1fr)`,
        }}
      >
        {visibleCols.map(col =>
          visibleRows.map(row => {
            const cell = `${col}${row}`;
            const mods = modulesByName[cell];
            return (
              <div key={cell} className="habitat-grid-cell">
                {mods ? (
                  mods.length > 1 ? (
                    <div className="duplicate-modules">
                      <p>Multiple modules named {mods[0].name}</p>
                      {mods.map(m => <p key={m.id}>{m.id} — {m.ip}</p>)}
                      <p>Fix in <NavLink to="/settings">Settings</NavLink></p>
                    </div>
                  ) : (
                    <HabitatLivestreamCard key={`${cell}-${refreshKey}`} module={mods[0]} />
                  )
                ) : (
                  <div className="empty-cell">{cell}</div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Corner overlay buttons */}
      <div className="habitat-grid-corner">
        <button
          className="habitat-grid-icon-btn"
          title="Force refresh all streams"
          onClick={() => setRefreshKey(k => k + 1)}
        >
          ↺
        </button>

        <div ref={settingsRef} className="habitat-grid-settings-wrap">
          <button
            className={`habitat-grid-icon-btn${hasSubset ? " habitat-grid-icon-btn--active" : ""}`}
            title="Grid subset"
            onClick={() => setShowSettings(s => !s)}
          >
            ⚙
          </button>

          {showSettings && (
            <div className="habitat-grid-popover">
              <p className="habitat-grid-popover-title">View subset</p>
              <label>
                From
                <select value={startCell} onChange={e => setStartCell(e.target.value)}>
                  <option value="">—</option>
                  {ALL_CELLS.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label>
                To
                <select value={endCell} onChange={e => setEndCell(e.target.value)}>
                  <option value="">—</option>
                  {ALL_CELLS.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <button
                className="habitat-grid-popover-reset"
                disabled={!hasSubset}
                onClick={() => { setStartCell(""); setEndCell(""); setShowSettings(false); }}
              >
                Show all
              </button>
            </div>
          )}
        </div>
      </div>

    </div>
  );
}

export default HabitatLivestreamGrid;
