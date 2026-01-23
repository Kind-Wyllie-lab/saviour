import React, { useState, useEffect } from "react";
import HabitatLivestreamCard from "../HabitatLivestreamCard/HabitatLivestreamCard";
import "./HabitatLivestreamGrid.css";

const STREAM_REFRESH_MS = 60 * 1000; // 1 minute in ms

const COLUMNS = ["A", "B", "C", "D"];
const ROWS = [1, 2, 3, 4];

const ALL_CELLS = COLUMNS.flatMap(col =>
  ROWS.map(row => `${col}${row}`)
);

function parseCell(cell) {
  return {
    col: COLUMNS.indexOf(cell[0]),
    row: ROWS.indexOf(Number(cell[1]))
  };
}

function getSelectionRect(start, end) {
  if (!start || !end) return null;

  const a = parseCell(start);
  const b = parseCell(end);

  return {
    colStart: Math.min(a.col, b.col),
    colEnd: Math.max(a.col, b.col),
    rowStart: Math.min(a.row, b.row),
    rowEnd: Math.max(a.row, b.row)
  };
}


function HabitatLivestreamGrid({ modules }) {
  // Making selection of subset of cells
  const [startCell, setStartCell] = useState("");
  const [endCell, setEndCell] = useState("");
  const selectionRect = getSelectionRect(startCell, endCell);

  const visibleCols = selectionRect
    ? COLUMNS.slice(
        selectionRect.colStart,
        selectionRect.colEnd + 1
      )
    : COLUMNS;

  const visibleRows = selectionRect
    ? ROWS.slice(
        selectionRect.rowStart,
        selectionRect.rowEnd + 1
      )
    : ROWS;

  const moduleByName = React.useMemo(() => {
    return Object.values(modules).reduce((acc, module) => {
      acc[module.name] = module;
      return acc;
    }, {});
  }, [modules]);

  // Refresh streams
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setRefreshKey(k => k + 1);
    }, STREAM_REFRESH_MS);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="habitat-grid-wrapper">
      {/* Grid */}
      <div
        className="habitat-grid"
        style={{
          gridTemplateColumns: `repeat(${visibleCols.length}, 1fr)`,
          gridTemplateRows: `repeat(${visibleRows.length}, 1fr)`
        }}
      >
        {visibleCols.map(col =>
          visibleRows.map(row => {
            const cell = `${col}${row}`;
            const module = moduleByName[cell];

            return (
              <div key={cell} className="habitat-grid-cell">
                {module ? (
                  <HabitatLivestreamCard key={`${cell}-${refreshKey}`} module={module} />
                ) : (
                  <div className="empty-cell">{cell}</div>
                )}
              </div>
            );
          })
        )}
      </div>
      {/* Controls */}
      <div className="habitat-grid-controls">
        <h3>Zoom Controls</h3>
        <button onClick={() => setRefreshKey(k => k + 1)}>Refresh Streams</button>
        <label>
          Start
          <select
            value={startCell}
            onChange={e => setStartCell(e.target.value)}
          >
            <option value="">—</option>
            {ALL_CELLS.map(cell => (
              <option key={cell} value={cell}>{cell}</option>
            ))}
          </select>
        </label>

        <label>
          End
          <select
            value={endCell}
            onChange={e => setEndCell(e.target.value)}
          >
            <option value="">—</option>
            {ALL_CELLS.map(cell => (
              <option key={cell} value={cell}>{cell}</option>
            ))}
          </select>
        </label>

        <button
          onClick={() => {
            setStartCell("");
            setEndCell("");
          }}
        >
          Reset
        </button>
      </div>
    </div>
  );
}
export default HabitatLivestreamGrid;