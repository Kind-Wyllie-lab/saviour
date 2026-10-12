import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import "./CropEditorModal.css";

/**
 * Crop / digital zoom editor (plans/field-install-feedback-2026-10.md,
 * Phase B).
 *
 * While open, the module shows its sensor mode's whole field of view on the
 * preview (set_crop_editing), squeezed into the output's aspect ratio; this
 * editor displays that snapshot at the field of view's own aspect (`fov`
 * from the module) so it looks undistorted, and the crop is drawn on it in
 * fractions 0..1 of the field of view. Saving sends those fractions plus the
 * chosen aspect preset; the module sets the recorded resolution to the
 * crop's aspect, so the recording is never stretched.
 */

const ASPECTS = [
  { key: "free", label: "Free", ratio: null },
  { key: "1:1", label: "1:1", ratio: 1 },
  { key: "4:3", label: "4:3", ratio: 4 / 3 },
  { key: "16:9", label: "16:9", ratio: 16 / 9 },
  { key: "3:4", label: "3:4", ratio: 3 / 4 },
  { key: "9:16", label: "9:16", ratio: 9 / 16 },
];
const MIN = 0.02;                 // smallest crop side, fraction of the view
const KEEPALIVE_MS = 30_000;      // module reverts the full view after 90 s
const CORNERS = ["nw", "ne", "sw", "se"];
const EDGES = ["n", "s", "e", "w"];

// Same maths as crop_geometry.output_size_for_crop on the module, for the
// "Output" readout before saving.
function estimateOutput(rect, fov, modeSize, base) {
  if (!rect || !fov || !modeSize || !base) return null;
  const sw = rect.width * fov[0];
  const sh = rect.height * fov[1];
  const aspect = sw / sh;
  const native = (sw * modeSize[0] / fov[0]) * (sh * modeSize[1] / fov[1]);
  const target = Math.min(base[0] * base[1], native);
  let h = Math.sqrt(target / aspect);
  let w = h * aspect;
  const shrink = Math.min(1, modeSize[0] / w, modeSize[1] / h);
  w *= shrink;
  h *= shrink;
  const outW = Math.max(64, Math.floor(w / 32) * 32);
  const outH = Math.max(64, Math.floor(Math.round(outW / aspect) / 2) * 2);
  return [outW, Math.min(outH, Math.floor(modeSize[1] / 2) * 2)];
}

const clamp01 = (v) => Math.min(1, Math.max(0, v));

// Shrink/position a rect so it fits inside the view, keeping its size ratio.
function fitInside(r) {
  let { x, y, width, height } = r;
  const s = Math.min(1, 1 / width, 1 / height);
  width *= s;
  height *= s;
  x = Math.min(Math.max(0, x), 1 - width);
  y = Math.min(Math.max(0, y), 1 - height);
  return { x, y, width, height };
}

// Largest rect of pixel-aspect `ratio` centred on `r` and inside it.
function refitToRatio(r, ratio, k) {
  if (!ratio) return r;
  const cx = r.x + r.width / 2;
  const cy = r.y + r.height / 2;
  let w = r.width;
  let h = (w * k) / ratio;
  if (h > r.height) {
    h = r.height;
    w = (h * ratio) / k;
  }
  return fitInside({ x: cx - w / 2, y: cy - h / 2, width: w, height: h });
}

export default function CropEditorModal({ moduleIp, moduleId, open, onClose }) {
  const viewerRef = useRef(null);
  const stageRef = useRef(null);
  const dragRef = useRef(null);
  const initRef = useRef(false);

  const [info, setInfo] = useState(null);       // crop_editing status from the module
  const [rect, setRect] = useState(null);       // {x, y, width, height}, fractions of the view
  const [aspectKey, setAspectKey] = useState("free");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshotKey, setSnapshotKey] = useState(0);
  const [stageSize, setStageSize] = useState({ w: 0, h: 0 });

  const fov = info?.fov;
  const k = fov ? fov[0] / fov[1] : 16 / 9;   // view's pixel aspect (w/h)
  const ratio = ASPECTS.find((a) => a.key === aspectKey)?.ratio ?? null;

  const snapshotUrl = useMemo(
    () => (moduleIp ? `http://${moduleIp}:8080/snapshot.jpg?ts=${Date.now()}&k=${snapshotKey}` : null),
    [moduleIp, snapshotKey],
  );

  // Enter full-view editing on open (refreshed every 30 s); leave on close.
  useEffect(() => {
    if (!open || !moduleId) return undefined;
    initRef.current = false;
    setInfo(null);
    setRect(null);
    setStatus("Showing the camera's full view…");
    setBusy(false);
    const send = (enabled) =>
      socket.emit("send_command", { module_id: moduleId, type: "set_crop_editing", params: { enabled } });
    send(true);
    const keepalive = setInterval(() => send(true), KEEPALIVE_MS);
    return () => {
      clearInterval(keepalive);
      send(false);
    };
  }, [open, moduleId]);

  useEffect(() => {
    if (!moduleId) return undefined;
    const onStatus = (msg) => {
      if (!msg || msg.module_id !== moduleId) return;
      if (msg.type === "crop_editing") {
        if (msg.error) {
          setStatus(msg.error);
          return;
        }
        if (!msg.enabled) return;
        setInfo(msg);
        if (!initRef.current) {
          // Only the first reply seeds the editor; keepalive replies must
          // not overwrite what the operator is drawing.
          initRef.current = true;
          setRect(msg.crop_rect || null);
          setAspectKey(ASPECTS.some((a) => a.key === msg.aspect) ? msg.aspect : "free");
          setStatus(msg.crop_rect
            ? "Showing the current crop on the full view."
            : "Drag on the image to draw a crop.");
          // The new ScalerCrop takes a frame or two to reach the preview.
          setTimeout(() => setSnapshotKey((n) => n + 1), 700);
        }
      } else if (msg.type === "camera_crop_updated") {
        setBusy(false);
        if (msg.error) {
          setStatus(msg.error);
          return;
        }
        onClose();
      }
    };
    const onError = (msg) => {
      if (!msg || msg.module_id !== moduleId) return;
      setBusy(false);
      setStatus(`Failed: ${msg.error ?? "unknown error"}`);
    };
    socket.on("module_status", onStatus);
    socket.on("module_error", onError);
    return () => {
      socket.off("module_status", onStatus);
      socket.off("module_error", onError);
    };
  }, [moduleId, onClose]);

  // Fit the stage (the view at its true aspect) inside the viewer.
  useEffect(() => {
    if (!open) return undefined;
    const el = viewerRef.current;
    if (!el) return undefined;
    const fit = () => {
      const W = el.clientWidth;
      const H = el.clientHeight;
      if (!W || !H) return;
      const w = Math.min(W, H * k);
      setStageSize({ w, h: w / k });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, k]);

  // Pointer position as fractions of the stage.
  const toNorm = (e) => {
    const r = stageRef.current.getBoundingClientRect();
    return { x: clamp01((e.clientX - r.left) / r.width), y: clamp01((e.clientY - r.top) / r.height) };
  };

  // Rect from a fixed anchor corner to the pointer, honouring the ratio.
  const fromAnchor = (a, p) => {
    let w = Math.abs(p.x - a.x);
    let h = Math.abs(p.y - a.y);
    if (ratio) {
      // Follow whichever side the pointer has moved further along.
      if ((w * k) / ratio >= h) h = (w * k) / ratio;
      else w = (h * ratio) / k;
      const maxW = p.x >= a.x ? 1 - a.x : a.x;
      const maxH = p.y >= a.y ? 1 - a.y : a.y;
      const s = Math.min(1, maxW / (w || 1), maxH / (h || 1));
      w *= s;
      h *= s;
    }
    return {
      x: p.x >= a.x ? a.x : a.x - w,
      y: p.y >= a.y ? a.y : a.y - h,
      width: w,
      height: h,
    };
  };

  const onPointerDown = (e, handle = null) => {
    if (!info || busy) return;
    e.preventDefault();
    e.stopPropagation();
    stageRef.current.setPointerCapture?.(e.pointerId);
    const p = toNorm(e);
    if (handle && rect) {
      const { x, y, width: w, height: h } = rect;
      if (CORNERS.includes(handle)) {
        const anchor = { x: handle.includes("w") ? x + w : x, y: handle.includes("n") ? y + h : y };
        dragRef.current = { mode: "corner", anchor };
      } else {
        dragRef.current = { mode: "edge", edge: handle, start: rect };
      }
    } else if (rect && p.x > rect.x && p.x < rect.x + rect.width && p.y > rect.y && p.y < rect.y + rect.height) {
      dragRef.current = { mode: "move", offset: { x: p.x - rect.x, y: p.y - rect.y } };
    } else {
      dragRef.current = { mode: "corner", anchor: p };
      setRect({ x: p.x, y: p.y, width: 0, height: 0 });
    }
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const p = toNorm(e);
    if (d.mode === "corner") {
      setRect(fromAnchor(d.anchor, p));
    } else if (d.mode === "move") {
      setRect((r) => fitInside({ ...r, x: p.x - d.offset.x, y: p.y - d.offset.y }));
    } else if (d.mode === "edge") {
      const s = d.start;
      const r = { ...s };
      if (d.edge === "w") { r.x = Math.min(p.x, s.x + s.width - MIN); r.width = s.x + s.width - r.x; }
      if (d.edge === "e") { r.width = Math.max(MIN, p.x - s.x); }
      if (d.edge === "n") { r.y = Math.min(p.y, s.y + s.height - MIN); r.height = s.y + s.height - r.y; }
      if (d.edge === "s") { r.height = Math.max(MIN, p.y - s.y); }
      setRect(fitInside(r));
    }
  };

  const onPointerUp = () => {
    dragRef.current = null;
    setRect((r) => (r && (r.width < MIN || r.height < MIN) ? null : r));
  };

  const chooseAspect = (key) => {
    setAspectKey(key);
    const r = ASPECTS.find((a) => a.key === key)?.ratio;
    if (r && rect) setRect(refitToRatio(rect, r, k));
  };

  const save = () => {
    if (!rect) { setStatus("Draw a crop on the image first."); return; }
    setBusy(true);
    setStatus("Saving… the camera restarts at the new resolution.");
    socket.emit("send_command", {
      module_id: moduleId,
      type: "set_camera_crop",
      params: { crop_rect: { ...rect, aspect: aspectKey } },
    });
  };

  const clear = () => {
    setBusy(true);
    setStatus("Clearing…");
    socket.emit("send_command", { module_id: moduleId, type: "set_camera_crop", params: { crop_rect: null } });
  };

  if (!open) return null;

  const output = estimateOutput(rect, fov, info?.mode_size, info?.base);
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  const handles = ratio ? CORNERS : [...CORNERS, ...EDGES];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal crop-editor-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Crop / Digital Zoom</h3>
        <p className="modal-subtext">
          The whole field of view is shown while this is open. Draw the region to record;
          drag inside it to move it, or its handles to resize. The recorded resolution
          follows the crop&apos;s shape, so nothing is stretched. Not available while recording.
        </p>
        <div className="crop-editor-modal__content">
          <div className="crop-editor-modal__viewer" ref={viewerRef}>
            {fov ? (
              <div
                className="crop-editor-modal__stage"
                ref={stageRef}
                style={{ width: stageSize.w, height: stageSize.h }}
                onPointerDown={(e) => onPointerDown(e)}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
              >
                {snapshotUrl && <img src={snapshotUrl} alt="Full field of view" draggable={false} />}
                {rect && (
                  <>
                    <div className="crop-editor-modal__shade" style={{
                      clipPath: `polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${pct(rect.x)} ${pct(rect.y)}, ${pct(rect.x)} ${pct(rect.y + rect.height)}, ${pct(rect.x + rect.width)} ${pct(rect.y + rect.height)}, ${pct(rect.x + rect.width)} ${pct(rect.y)}, ${pct(rect.x)} ${pct(rect.y)})`,
                    }} />
                    <div
                      className="crop-editor-modal__rect"
                      style={{ left: pct(rect.x), top: pct(rect.y), width: pct(rect.width), height: pct(rect.height) }}
                    >
                      {handles.map((h) => (
                        <span
                          key={h}
                          className={`crop-editor-modal__handle crop-editor-modal__handle--${h}`}
                          onPointerDown={(e) => onPointerDown(e, h)}
                        />
                      ))}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="crop-editor-modal__waiting">{status || "Waiting for the camera…"}</div>
            )}
          </div>

          <div className="crop-editor-modal__sidebar">
            <div className="crop-editor-modal__group">
              <span className="crop-editor-modal__label">Aspect ratio</span>
              <div className="crop-editor-modal__aspects">
                {ASPECTS.map((a) => (
                  <button
                    key={a.key}
                    type="button"
                    className={`crop-editor-modal__aspect${aspectKey === a.key ? " crop-editor-modal__aspect--active" : ""}`}
                    onClick={() => chooseAspect(a.key)}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </div>

            {rect && fov && (
              <div className="sensor-mode-info">
                Region: {Math.round(rect.width * fov[0])}×{Math.round(rect.height * fov[1])} sensor px
                {output && <><br />Output: about {output[0]}×{output[1]}</>}
              </div>
            )}
            {info?.base && (
              <div className="sensor-mode-info sensor-mode-info--muted">
                Uncropped output: {info.base[0]}×{info.base[1]}
              </div>
            )}

            <div className="crop-editor-modal__actions">
              <button className="copy-btn" type="button" onClick={() => setSnapshotKey((n) => n + 1)}>
                Refresh snapshot
              </button>
              <button className="copy-btn" type="button" onClick={() => setRect(null)} disabled={!rect || busy}>
                Reset selection
              </button>
              <button className="save-button" type="button" onClick={save} disabled={!rect || !info || busy}>
                Save crop
              </button>
              <button className="copy-btn" type="button" onClick={clear} disabled={busy}>
                Clear crop
              </button>
              <button className="save-button" type="button" onClick={onClose}>
                Close
              </button>
            </div>
            {status && <div className="sensor-mode-info">{status}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
