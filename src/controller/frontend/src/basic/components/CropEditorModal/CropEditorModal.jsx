import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import "./CropEditorModal.css";

/**
 * Drag-draw a crop/digital-zoom rectangle over a live camera snapshot.
 * Architectural precedent: LoomRoiLineEditorModal (coordinate mapping,
 * snapshot/save pattern) -- not a literal reuse target, since this is a
 * single drag-rectangle rather than a 4-point polygon + line.
 *
 * Saved rect is in the *displayed preview's* pixel space (the snapshot's
 * natural width/height, i.e. whatever camera.width/height currently are) --
 * the module converts it into sensor-native ScalerCrop coordinates using
 * the active sensor mode's crop_limits (see
 * CameraBase._compute_scaler_crop_rect in camera_base.py).
 */
export default function CropEditorModal({ moduleIp, moduleId, open, onClose, initialCropRect }) {
  const imgRef = useRef(null);
  const canvasRef = useRef(null);
  const draggingRef = useRef(false);
  const dragStartRef = useRef(null); // {x, y} in image-natural pixel space

  const [snapshotKey, setSnapshotKey] = useState(0);
  const [rect, setRect] = useState(null); // {x, y, width, height} in image-natural pixel space
  const [lockAspect, setLockAspect] = useState(false);
  const [status, setStatus] = useState("");

  const getMapping = () => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return null;
    const rectDom = canvas.getBoundingClientRect();
    const W = rectDom.width;
    const H = rectDom.height;
    const nW = img.naturalWidth || 1;
    const nH = img.naturalHeight || 1;
    const scale = Math.min(W / nW, H / nH);
    const dispW = nW * scale;
    const dispH = nH * scale;
    const offX = (W - dispW) / 2;
    const offY = (H - dispH) / 2;
    return { rectDom, scale, offX, offY, dispW, dispH, nW, nH };
  };

  const canvasEventToImagePixel = (e) => {
    const m = getMapping();
    if (!m) return null;
    const cx = e.clientX - m.rectDom.left;
    const cy = e.clientY - m.rectDom.top;
    if (cx < m.offX || cx > m.offX + m.dispW || cy < m.offY || cy > m.offY + m.dispH) {
      return null;
    }
    return {
      x: Math.max(0, Math.min(m.nW, (cx - m.offX) / m.scale)),
      y: Math.max(0, Math.min(m.nH, (cy - m.offY) / m.scale)),
    };
  };

  const baseUrl = useMemo(() => (moduleIp ? `http://${moduleIp}:8080` : null), [moduleIp]);
  const snapshotUrl = useMemo(
    () => (baseUrl ? `${baseUrl}/snapshot.jpg?ts=${Date.now()}&k=${snapshotKey}` : null),
    [baseUrl, snapshotKey]
  );

  const redraw = () => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;

    const m = getMapping();
    if (!m) return;
    const toViewer = (x, y) => ({ x: m.offX + x * m.scale, y: m.offY + y * m.scale });

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!rect) return;

    const topLeft = toViewer(rect.x, rect.y);
    const w = rect.width * m.scale;
    const h = rect.height * m.scale;
    ctx.strokeStyle = "lime";
    ctx.fillStyle = "rgba(0,255,0,0.12)";
    ctx.lineWidth = 2;
    ctx.fillRect(topLeft.x, topLeft.y, w, h);
    ctx.strokeRect(topLeft.x, topLeft.y, w, h);
  };

  // Reset to whatever crop is already saved (if any) every time the modal opens.
  useEffect(() => {
    if (!open) return;
    setRect(
      initialCropRect && initialCropRect.width > 0 && initialCropRect.height > 0
        ? { x: initialCropRect.x, y: initialCropRect.y, width: initialCropRect.width, height: initialCropRect.height }
        : null
    );
    setStatus("");
    setSnapshotKey((k) => k + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    redraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rect, open]);

  useEffect(() => {
    if (!moduleId) return;
    const onModuleStatus = (msg) => {
      if (!msg || msg.module_id !== moduleId) return;
      if (msg.type === "camera_crop_updated") {
        setStatus(msg.crop_rect ? "Saved. Crop will apply immediately." : "Crop cleared.");
        // Pull a fresh snapshot so the preview reflects the now-applied
        // (or now-removed) crop rather than the pre-change frame.
        setSnapshotKey((k) => k + 1);
      }
    };
    // Command failures (Command._handle_error's generic "error" status) are
    // routed by web.py to a separate "module_error" event, not "module_status"
    // -- see handle_module_status's "error" case.
    const onModuleError = (msg) => {
      if (!msg || msg.module_id !== moduleId) return;
      setStatus(`Save failed: ${msg.error ?? "unknown error"}`);
    };
    socket.on("module_status", onModuleStatus);
    socket.on("module_error", onModuleError);
    return () => {
      socket.off("module_status", onModuleStatus);
      socket.off("module_error", onModuleError);
    };
  }, [moduleId]);

  const handleMouseDown = (e) => {
    const p = canvasEventToImagePixel(e);
    if (!p) return;
    draggingRef.current = true;
    dragStartRef.current = p;
    setRect({ x: p.x, y: p.y, width: 0, height: 0 });
  };

  const handleMouseMove = (e) => {
    if (!draggingRef.current) return;
    const p = canvasEventToImagePixel(e);
    if (!p) return;
    const start = dragStartRef.current;
    let x = Math.min(start.x, p.x);
    let y = Math.min(start.y, p.y);
    let width = Math.abs(p.x - start.x);
    let height = Math.abs(p.y - start.y);

    if (lockAspect) {
      const img = imgRef.current;
      const targetRatio = img && img.naturalWidth && img.naturalHeight
        ? img.naturalWidth / img.naturalHeight
        : 1;
      if (width / (height || 1) > targetRatio) {
        height = width / targetRatio;
      } else {
        width = height * targetRatio;
      }
      x = p.x >= start.x ? start.x : start.x - width;
      y = p.y >= start.y ? start.y : start.y - height;
    }

    setRect({ x, y, width, height });
  };

  const handleMouseUp = () => {
    draggingRef.current = false;
    if (rect && (rect.width < 5 || rect.height < 5)) setRect(null);
  };

  const handleSave = () => {
    if (!moduleId) { setStatus("No moduleId provided."); return; }
    const img = imgRef.current;
    if (!img) { setStatus("No snapshot loaded yet."); return; }
    if (!rect || rect.width < 5 || rect.height < 5) { setStatus("Drag a rectangle on the image first."); return; }

    setStatus("Saving…");
    socket.emit("send_command", {
      module_id: moduleId,
      type: "set_camera_crop",
      params: {
        crop_rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          preview_width: img.naturalWidth,
          preview_height: img.naturalHeight,
        },
      },
    });
  };

  const handleClear = () => {
    if (!moduleId) return;
    setRect(null);
    setStatus("Clearing…");
    socket.emit("send_command", {
      module_id: moduleId,
      type: "set_camera_crop",
      params: { crop_rect: null },
    });
  };

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal crop-editor-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Crop / Digital Zoom</h3>
        <p className="modal-subtext">
          Drag a rectangle on the image to define the recorded region. This restricts what the sensor
          actually reads out (ScalerCrop) -- it affects the real recorded file, not a copy.
        </p>
        <div className="crop-editor-modal__content">
          <div className="crop-editor-modal__viewer">
            <img ref={imgRef} src={snapshotUrl} alt="snapshot" onLoad={redraw} />
            <canvas
              ref={canvasRef}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              style={{ cursor: "crosshair" }}
            />
          </div>

          <div className="crop-editor-modal__sidebar">
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <button className="copy-btn" type="button" onClick={() => setSnapshotKey((k) => k + 1)}>
                Refresh snapshot
              </button>

              <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.85rem" }}>
                <input
                  type="checkbox"
                  checked={lockAspect}
                  onChange={(e) => setLockAspect(e.target.checked)}
                />
                Lock aspect ratio to preview
              </label>

              <button className="copy-btn" type="button" onClick={() => setRect(null)}>
                Reset selection
              </button>

              <button className="save-button" type="button" onClick={handleSave}>
                Save Crop
              </button>

              <button className="copy-btn" type="button" onClick={handleClear}>
                Clear Crop
              </button>

              <button className="save-button" type="button" onClick={onClose}>
                Close
              </button>

              {rect && (
                <div className="sensor-mode-info">
                  {Math.round(rect.width)}×{Math.round(rect.height)} at ({Math.round(rect.x)}, {Math.round(rect.y)})
                </div>
              )}
              {status && <div className="sensor-mode-info">{status}</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
