import React, { useRef } from "react";
import "./DraggableTile.css";

const MIN_W = 160;

// A free-positioned, aspect-ratio-locked tile for the dashboard's "Arrange"
// mode. Drag from the grip strip; resize from the bottom-right corner (width
// only — height is always width / ratio, so a stream can't be squashed).
// Position/size are owned by the parent (persisted there); this just reports
// deltas via onChange({ x, y } | { width }).
export default function DraggableTile({
  x, y, width, ratio = 16 / 9, bounds, onChange, children,
}) {
  // onChange is recreated on every parent render; keep the latest so a drag
  // that started a render ago still writes through to current state.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const startPointer = (mode) => (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const sx = e.clientX;
    const sy = e.clientY;
    const ox = x;
    const oy = y;
    const ow = width;
    const bw = bounds?.width || 4000;
    const bh = bounds?.height || 4000;

    const move = (ev) => {
      const dx = ev.clientX - sx;
      const dy = ev.clientY - sy;
      if (mode === "drag") {
        onChangeRef.current({
          x: Math.min(Math.max(0, bw - 80), Math.max(0, ox + dx)),
          y: Math.min(Math.max(0, bh - 60), Math.max(0, oy + dy)),
        });
      } else {
        onChangeRef.current({
          width: Math.min(bw, Math.max(MIN_W, ow + dx)),
        });
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("dash-tile-dragging");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    document.body.classList.add("dash-tile-dragging");
  };

  const h = Math.round(width / (ratio || 16 / 9));

  return (
    <div className="dash-tile" style={{ left: x, top: y, width }}>
      <div
        className="dash-tile__grip"
        onPointerDown={startPointer("drag")}
        title="Drag to move"
      >
        <span aria-hidden="true">⠿⠿⠿</span>
      </div>
      <div
        className="dash-tile__body"
        style={{ "--tile-w": `${Math.round(width)}px`, "--tile-h": `${h}px` }}
      >
        {children}
      </div>
      <div
        className="dash-tile__resize"
        onPointerDown={startPointer("resize")}
        title="Drag to resize (keeps aspect ratio)"
      />
    </div>
  );
}
