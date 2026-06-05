import { useEffect, useMemo, useRef, useState } from "react";

/**
 * Interactive ROI (4 points) + vertical line selection over a camera snapshot.
 *
 * Workflow
 * --------
 * 1) Click 4 points to define polygon (any order).
 * 2) Move mouse to position vertical line.
 * 3) Click to set line.
 * 4) Save posts JSON to /roi on the module.
 */
export default function LoomRoiLineEditorModal({ moduleIp, open, onClose }) {
  const imgRef = useRef(null);
  const canvasRef = useRef(null);

  const [snapshotKey, setSnapshotKey] = useState(0);
  const [points, setPoints] = useState([]); // [{x,y}, ...] in displayed image px
  const [lineX, setLineX] = useState(null); // in displayed image px
  const [phase, setPhase] = useState("polygon"); // 'polygon' | 'line' | 'done'
  const [status, setStatus] = useState("");

  const baseUrl = useMemo(() => {
    if (!moduleIp) return null;
    return `http://${moduleIp}:8080`;
  }, [moduleIp]);

  const snapshotUrl = useMemo(() => {
    if (!baseUrl) return null;
    // cache-bust so user can refresh lighting/position
    return `${baseUrl}/roi/snapshot.jpg?ts=${Date.now()}&k=${snapshotKey}`;
  }, [baseUrl, snapshotKey]);

  const redraw = () => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;

    const w = img.clientWidth;
    const h = img.clientHeight;
    canvas.width = w;
    canvas.height = h;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    // polygon
    if (points.length) {
      ctx.strokeStyle = "lime";
      ctx.fillStyle = "rgba(0,255,0,0.12)";
      ctx.lineWidth = 2;

      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
      if (points.length >= 3) ctx.closePath();
      ctx.stroke();
      if (points.length >= 3) ctx.fill();

      // points
      ctx.fillStyle = "red";
      for (const p of points) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 4, 0, 2 * Math.PI);
        ctx.fill();
      }
    }

    // vertical line
    if (lineX != null) {
      ctx.strokeStyle = "yellow";
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 6]);
      ctx.beginPath();
      ctx.moveTo(lineX, 0);
      ctx.lineTo(lineX, h);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  };

  useEffect(() => {
    if (!open) return;
    setPoints([]);
    setLineX(null);
    setPhase("polygon");
    setStatus("");
    setSnapshotKey(k => k + 1);
  }, [open]);

  useEffect(() => {
    redraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, lineX, open]);

  const handleClick = (e) => {
    if (phase === "done") return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (phase === "polygon") {
      const next = [...points, { x, y }];
      setPoints(next);
      if (next.length === 4) setPhase("line");
    } else if (phase === "line") {
      setLineX(x);
      setPhase("done");
    }
  };

  const handleMove = (e) => {
    if (phase !== "line") return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    setLineX(x);
  };

  const handleSave = async () => {
    if (!baseUrl) return;
    const img = imgRef.current;
    if (!img) return;

    if (points.length < 3) {
      setStatus("Need at least 3 polygon points.");
      return;
    }
    if (lineX == null) {
      setStatus("Need a vertical line.");
      return;
    }

    const payload = {
      image_size: { width: img.clientWidth, height: img.clientHeight },
      arena_polygon: points.map(p => ({ x: p.x, y: p.y })),
      crossing_line: { kind: "vertical", x: lineX, direction: "left_is_in" },
      created: new Date().toISOString()
    };

    try {
      setStatus("Saving…");
      const res = await fetch(`${baseUrl}/roi`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(`Save failed: ${data.error ?? res.statusText}`);
        return;
      }
      setStatus("Saved. ROI will apply immediately.");
    } catch (err) {
      setStatus(`Save failed: ${String(err)}`);
    }
  };

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(1100px, 96vw)" }} onClick={e => e.stopPropagation()}>
        <h3>Set Loom ROI + Crossing Line</h3>
        <p className="modal-subtext">
          Step 1: click 4 polygon points. Step 2: move mouse + click to set vertical line.
        </p>

        <div style={{ display: "flex", gap: "12px", alignItems: "flex-start" }}>
          <div style={{ position: "relative", width: "100%" }}>
            <img
              ref={imgRef}
              src={snapshotUrl}
              alt="snapshot"
              onLoad={redraw}
              style={{ width: "100%", height: "auto", display: "block", borderRadius: "6px" }}
            />
            <canvas
              ref={canvasRef}
              onClick={handleClick}
              onMouseMove={handleMove}
              style={{
                position: "absolute",
                left: 0, top: 0,
                width: "100%", height: "100%",
                cursor: phase === "polygon" ? "crosshair" : "col-resize"
              }}
            />
          </div>

          <div style={{ minWidth: "260px" }}>
            <div><strong>Phase:</strong> {phase}</div>
            <div><strong>Points:</strong> {points.length}/4</div>
            <div><strong>Line X:</strong> {lineX == null ? "—" : lineX.toFixed(1)}</div>

            <div style={{ marginTop: "10px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              <button className="copy-btn" type="button" onClick={() => setSnapshotKey(k => k + 1)}>
                Refresh snapshot
              </button>
              <button className="copy-btn" type="button" onClick={() => { setPoints([]); setLineX(null); setPhase("polygon"); }}>
                Reset
              </button>
            </div>

            <div style={{ marginTop: "10px", display: "flex", gap: "8px" }}>
              <button className="save-button" type="button" onClick={handleSave}>
                Save ROI
              </button>
              <button className="reset-button" type="button" onClick={onClose}>
                Close
              </button>
            </div>

            {status && <div className="sensor-mode-info" style={{ marginTop: "10px" }}>{status}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
