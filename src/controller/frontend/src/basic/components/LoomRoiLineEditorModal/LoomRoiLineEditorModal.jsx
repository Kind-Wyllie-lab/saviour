import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import "./LoomRoiLineEditorModal.css";

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
export default function LoomRoiLineEditorModal({ moduleIp, moduleId, open, onClose }) {
  const imgRef = useRef(null);
  const canvasRef = useRef(null);

  const [snapshotKey, setSnapshotKey] = useState(0);
  const [points, setPoints] = useState([]); // [{x,y}, ...] in displayed image px
  const [lineX, setLineX] = useState(null); // in displayed image px
  const [phase, setPhase] = useState("polygon"); // 'polygon' | 'line' | 'done'
  const [status, setStatus] = useState("");

  const canvasEventToImagePixel = (e) => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return null;

    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    // Display size of canvas
    const W = rect.width;
    const H = rect.height;

    // Natural (intrinsic) image size
    const nW = img.naturalWidth;
    const nH = img.naturalHeight;

    // How the image is fitted into the viewer (contain)
    const scale = Math.min(W / nW, H / nH);
    const dispW = nW * scale;
    const dispH = nH * scale;
    const offX = (W - dispW) / 2;
    const offY = (H - dispH) / 2;

    // Reject clicks outside the actual image region
    if (cx < offX || cx > offX + dispW || cy < offY || cy > offY + dispH) {
      return null;
    }

    const ix = (cx - offX) / scale;
    const iy = (cy - offY) / scale;
    return { x: ix, y: iy, imageWidth: nW, imageHeight: nH };
  };

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

    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    canvas.width = W;
    canvas.height = H;

    const nW = img.naturalWidth || 1;
    const nH = img.naturalHeight || 1;

    const scale = Math.min(W / nW, H / nH);
    const dispW = nW * scale;
    const dispH = nH * scale;
    const offX = (W - dispW) / 2;
    const offY = (H - dispH) / 2;

    const toViewer = (p) => ({
      x: offX + p.x * scale,
      y: offY + p.y * scale
    });

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, W, H);

    // polygon
    if (points.length) {
      ctx.strokeStyle = "lime";
      ctx.fillStyle = "rgba(0,255,0,0.12)";
      ctx.lineWidth = 2;

      const p0 = toViewer(points[0]);
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y);
      for (let i = 1; i < points.length; i++) {
        const pi = toViewer(points[i]);
        ctx.lineTo(pi.x, pi.y);
      }
      if (points.length >= 3) ctx.closePath();
      ctx.stroke();
      if (points.length >= 3) ctx.fill();

      ctx.fillStyle = "red";
      for (const p of points) {
        const pv = toViewer(p);
        ctx.beginPath();
        ctx.arc(pv.x, pv.y, 4, 0, 2 * Math.PI);
        ctx.fill();
      }
    }

    // line
    if (lineX != null) {
      const xV = offX + lineX * scale;
      ctx.strokeStyle = "yellow";
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 6]);
      ctx.beginPath();
      ctx.moveTo(xV, offY);
      ctx.lineTo(xV, offY + dispH);
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

  useEffect(() => {
    if (!moduleId) return;

    const onModuleStatus = (msg) => {
      if (!msg) return;
      if (msg.module_id !== moduleId) return;

      if (msg.type === "loom_roi_updated") {
        setStatus("Saved. ROI will apply immediately.");
      } else if (msg.type === "loom_roi_update_failed") {
        setStatus(`Save failed: ${msg.error ?? "unknown error"}`);
      }
    };

    socket.on("module_status", onModuleStatus);
    return () => socket.off("module_status", onModuleStatus);
  }, [moduleId]);
  
  const handleClick = (e) => {
    if (phase === "done") return;
    const p = canvasEventToImagePixel(e);
    if (!p) return;

    if (phase === "polygon") {
      const next = [...points, { x: p.x, y: p.y }];
      setPoints(next);
      if (next.length === 4) setPhase("line");
    } else if (phase === "line") {
      setLineX(p.x);
      setPhase("done");
    }
  };

  const handleMove = (e) => {
    if (phase !== "line") return;
    const p = canvasEventToImagePixel(e);
    if (!p) return;
    setLineX(p.x);
  };

  const handleSave = () => {
    if (!moduleId) {
      setStatus("No moduleId provided.");
      return;
    }

    const img = imgRef.current;
    if (!img) {
      setStatus("No snapshot loaded yet.");
      return;
    }

    if (points.length < 3) {
      setStatus("Need at least 3 polygon points.");
      return;
    }
    if (lineX == null) {
      setStatus("Need a vertical line.");
      return;
    }

    const payload = {
      image_size: { width: img.naturalWidth, height: img.naturalHeight },
      arena_polygon: points.map(p => ({ x: p.x, y: p.y })),
      crossing_line: { kind: "vertical", x: lineX, direction: "left_is_in" },
      created: new Date().toISOString(),
    };

    setStatus("Saving…");

    socket.emit("send_command", {
      module_id: moduleId,
      type: "set_loom_roi",
      params: { payload },   // IMPORTANT: kwargs on python side
    });
  };


  if (!open) return null;

  return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal loom-roi-modal" onClick={e => e.stopPropagation()}>
          <h3>Set Loom ROI + Crossing Line</h3>
          <p className="modal-subtext">
            Step 1: click 4 polygon points. Step 2: move mouse + click to set vertical line.
          </p>
          <div className="loom-roi-modal__content">
            <div className="loom-roi-modal__viewer">
              <img
                  ref={imgRef}
                  src={snapshotUrl}
                  alt="snapshot"
                  onLoad={redraw}
              />
              <canvas
                  ref={canvasRef}
                  onClick={handleClick}
                  onMouseMove={handleMove}
                  style={{ cursor: phase === "polygon" ? "crosshair" : "col-resize" }}
              />
            </div>

        <div className="loom-roi-modal__sidebar">
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <button className="copy-btn" type="button" onClick={() => setSnapshotKey(k => k + 1)}>
              Refresh snapshot
            </button>

            <button className="copy-btn" type="button" onClick={() => { setPoints([]); setLineX(null); setPhase("polygon"); }}>
              Reset selection
            </button>

            <button className="save-button" type="button" onClick={handleSave}>
              Save ROI
            </button>

            <button className="save-button" type="button" onClick={onClose}>
              Close
            </button>

            {status && <div className="sensor-mode-info">{status}</div>}
          </div>
        </div>
          </div>
        </div>
      </div>
  );
};