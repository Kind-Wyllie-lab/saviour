import React, { useEffect, useRef, useState } from "react";
import { clampRect, resizeRect, snapRect } from "./composeLayout";

const HANDLES = ["tl", "t", "tr", "r", "br", "b", "bl", "l"];
const SNAP_PX = 8;          // snap distance on screen
export const SOURCE_MIME = "application/x-saviour-source";

/**
 * The output canvas, drawn at its aspect and scaled to the space available,
 * with one box per tile at its rect. Each box shows that tile's pixels from
 * the last preview, so moving a tile carries its picture with it and the
 * real frame arrives when the debounced preview comes back.
 *
 * Drag a box to move it, a handle to resize (Shift keeps the aspect), Alt
 * to turn snapping off. Sources dragged from the palette drop where
 * released. Keyboard shortcuts live in ComposeEditor (page-wide).
 */
export default function LayoutCanvas({
  doc, selectedId, onSelect, onRect, images, labelFor, onDropSource,
  maxHeight = 560,
}) {
  const wrapRef = useRef(null);
  const [boxW, setBoxW] = useState(800);
  const [drag, setDrag] = useState(null);   // { id, rect, guides } while dragging

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setBoxW(el.clientWidth || 800));
    ro.observe(el);
    setBoxW(el.clientWidth || 800);
    return () => ro.disconnect();
  }, []);

  const { width: W, height: H } = doc.canvas;
  const scale = Math.max(0.05, Math.min(boxW / W, maxHeight / H));
  const order = doc.tiles
    .map((t, i) => ({ t, i }))
    .sort((a, b) => (a.t.z - b.t.z) || (a.i - b.i))
    .map(({ t }) => t);

  const startPointer = (tile, edges) => (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    onSelect(tile.id);
    const sx = e.clientX;
    const sy = e.clientY;
    const start = tile.rect;
    const aspect = start[2] / start[3];
    const others = doc.tiles.filter((t) => t.id !== tile.id).map((t) => t.rect);
    let latest = null;
    const move = (ev) => {
      const dx = (ev.clientX - sx) / scale;
      const dy = (ev.clientY - sy) / scale;
      let rect;
      let guides = [];
      if (edges === "move") {
        rect = [start[0] + dx, start[1] + dy, start[2], start[3]];
        if (!ev.altKey) ({ rect, guides } = snapRect(rect, others, doc.canvas, SNAP_PX / scale, ["l", "r", "t", "b"]));
      } else {
        rect = resizeRect(start, edges, dx, dy, ev.shiftKey ? aspect : null);
        if (!ev.altKey && !ev.shiftKey) {
          ({ rect, guides } = snapRect(rect, others, doc.canvas, SNAP_PX / scale, edges.split("")));
        }
      }
      latest = clampRect(rect, doc.canvas);
      setDrag({ id: tile.id, rect: latest, guides });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("layout-canvas-dragging");
      setDrag(null);
      if (latest) onRect(tile.id, latest);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    document.body.classList.add("layout-canvas-dragging");
  };

  const onDrop = (e) => {
    const raw = e.dataTransfer.getData(SOURCE_MIME);
    if (!raw) return;
    e.preventDefault();
    const box = e.currentTarget.getBoundingClientRect();
    try {
      const { kind, source } = JSON.parse(raw);
      onDropSource(kind, source, (e.clientX - box.left) / scale, (e.clientY - box.top) / scale);
    } catch {
      // not one of ours
    }
  };

  return (
    <div className="layout-canvas__wrap" ref={wrapRef}>
      <div
        className="layout-canvas"
        style={{ width: W * scale, height: H * scale, background: doc.canvas.background }}
        onPointerDown={() => onSelect(null)}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes(SOURCE_MIME)) e.preventDefault();
        }}
        onDrop={onDrop}
        aria-label="Output canvas"
      >
        {order.map((tile, zi) => {
          const [x, y, w, h] = drag?.id === tile.id ? drag.rect : tile.rect;
          const selected = tile.id === selectedId;
          const img = images?.[tile.id];
          return (
            <div
              key={tile.id}
              className={`layout-tile layout-tile--${tile.kind}${selected ? " layout-tile--selected" : ""}`}
              style={{ left: x * scale, top: y * scale, width: w * scale, height: h * scale, zIndex: zi + 1 }}
              onPointerDown={startPointer(tile, "move")}
              title={`${labelFor(tile)}: ${w}×${h} at ${x},${y}`}
            >
              {img ? (
                <img src={img} alt="" draggable={false} className="layout-tile__img" />
              ) : (
                <div className="layout-tile__empty">{labelFor(tile)}</div>
              )}
              <span className="layout-tile__tag">{labelFor(tile)}</span>
              {selected && HANDLES.map((hd) => (
                <span
                  key={hd}
                  className={`layout-tile__handle layout-tile__handle--${hd}`}
                  onPointerDown={startPointer(tile, hd)}
                />
              ))}
            </div>
          );
        })}
        {drag?.guides.map((g, i) => (
          <span
            key={i}
            className={`layout-canvas__guide layout-canvas__guide--${g.axis}`}
            style={g.axis === "x" ? { left: g.pos * scale } : { top: g.pos * scale }}
          />
        ))}
      </div>
    </div>
  );
}
