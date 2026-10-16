import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import usePersistedState from "/src/hooks/usePersistedState";
import useComposeJobs from "/src/hooks/useComposeJobs";
import ComposeJobRow from "../Recording/SessionDetailPage/ComposeJobRow";
import { SYNC_CLASS, SYNC_LABEL, worstSummary } from "../Recording/syncFormat";
import LayoutCanvas, { SOURCE_MIME } from "./LayoutCanvas";
import TileInspector from "./TileInspector";
import {
  CANVAS_PRESETS, DEFAULT_SPECTROGRAM, LIMITS, TEMPLATES, TEMPLATE_AUDIO,
  clampRect, fitToCanvas, missingSources, newCameraTile, newSpectrogramTile,
  newTileId, streamsByDate,
} from "./composeLayout";
import "./ComposeEditor.css";

const PREVIEW_DEBOUNCE_MS = 500;
const UNDO_DEPTH = 50;

/**
 * Post-Process compose: a layout editor (plans/compose-layout-editor.md).
 * Pick the output canvas, place the day's cameras and AudioMoth
 * spectrograms on it, set each tile's options, and render. The layout
 * document this edits is exactly what the controller previews and renders
 * (src/controller/compose_layout.py), so the canvas is what you get.
 */
export default function ComposeEditor({ sessionName, session, files, onSelectSession }) {
  const byDate = useMemo(() => streamsByDate(files), [files]);
  const dates = Object.keys(byDate).sort();
  const [dateDir, setDateDir] = useState("");
  const [info, setInfo] = useState(null);
  // The layout survives leaving the page (and carries over to another
  // session with the same cameras, which is often what's wanted).
  const [doc, setDocRaw] = usePersistedState("compose_layout_doc_v1", null);
  const [selectedId, setSelectedId] = useState(null);
  const [images, setImages] = useState({});
  const [preview, setPreview] = useState(null);       // { image, error }
  const [previewing, setPreviewing] = useState(false);
  const [notice, setNotice] = useState("");
  const [template, setTemplate] = usePersistedState("compose_template", "auto");
  const [templateAudio, setTemplateAudio] = usePersistedState("compose_template_audio", "none");
  const [keepCanvas, setKeepCanvas] = usePersistedState("compose_template_keep_canvas", false);
  const [fps, setFps] = useState("");
  const fpsTouched = useRef(false);
  const [fmt, setFmt] = usePersistedState("compose_fmt", "mp4");
  const jobs = useComposeJobs();
  const previewReqId = useRef(0);
  const templateReqId = useRef(0);
  const undoStack = useRef([]);
  const redoStack = useRef([]);
  const fullRef = useRef(null);

  const cameras = (info?.session_name === sessionName && info?.date_dir === dateDir
    && info.cameras) || [];
  const micDevices = (info?.session_name === sessionName && info?.date_dir === dateDir
    && info.mic_devices) || [];
  const infoReady = cameras.length > 0;
  const camSources = (doc?.tiles || []).filter((t) => t.kind === "camera").map((t) => t.source);
  const camKey = [...new Set(camSources)].join("|");
  const missing = infoReady ? missingSources(doc, cameras.map((c) => c.name),
    micDevices.map((d) => d.id)) : [];
  const docKey = JSON.stringify(doc);
  const selected = doc?.tiles.find((t) => t.id === selectedId) || null;
  const hasSpectrogram = !!doc?.tiles.some((t) => t.kind === "spectrogram");

  // Every edit goes through here so it can be undone. Reads the latest
  // layout from a ref (not a state updater, which StrictMode runs twice).
  const docRef = useRef(doc);
  docRef.current = doc;
  const setDoc = useCallback((next) => {
    const cur = docRef.current;
    const value = typeof next === "function" ? next(cur) : next;
    if (value === cur) return;
    if (cur) {
      undoStack.current = [...undoStack.current, cur].slice(-UNDO_DEPTH);
      redoStack.current = [];
    }
    docRef.current = value;
    setDocRaw(value);
  }, [setDocRaw]);
  const undo = () => {
    const prev = undoStack.current.pop();
    if (!prev) return;
    redoStack.current.push(docRef.current);
    docRef.current = prev;
    setDocRaw(prev);
  };
  const redo = () => {
    const next = redoStack.current.pop();
    if (!next) return;
    undoStack.current.push(docRef.current);
    docRef.current = next;
    setDocRaw(next);
  };

  const updateTile = (id, patch) => setDoc((d) => ({
    ...d, tiles: d.tiles.map((t) => (t.id === id ? { ...t, ...patch } : t)),
  }));
  const deleteTile = (id) => {
    setDoc((d) => ({ ...d, tiles: d.tiles.filter((t) => t.id !== id) }));
    setSelectedId(null);
  };
  const addTile = (tile) => {
    setDoc((d) => ({ ...d, tiles: [...d.tiles, tile] }));
    setSelectedId(tile.id);
  };

  // ── data from the controller ─────────────────────────────────────────
  useEffect(() => {
    if (!dates.length) return;
    if (!dates.includes(dateDir)) setDateDir(dates[dates.length - 1]);
  }, [dates.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onInfo = (d) => setInfo(d);
    const onPreview = (d) => {
      if (d.request_id !== undefined && d.request_id !== previewReqId.current) return;
      setPreviewing(false);
      if (d.error) {
        setPreview((p) => ({ ...(p || {}), error: d.error }));
        return;
      }
      setPreview({ image: d.image });
      setImages(d.tiles || {});
    };
    const onTemplate = (d) => {
      if (d.request_id !== templateReqId.current) return;
      if (d.error) { setNotice(d.error); return; }
      setNotice("");
      setDoc(d.layout_doc);
      setSelectedId(null);
    };
    const onAccepted = () => setNotice("");
    const onRejected = (d) => setNotice(d.error || "Compose request rejected");
    socket.on("compose_streams_info", onInfo);
    socket.on("compose_preview_ready", onPreview);
    socket.on("compose_layout_template", onTemplate);
    socket.on("compose_job_accepted", onAccepted);
    socket.on("compose_job_rejected", onRejected);
    return () => {
      socket.off("compose_streams_info", onInfo);
      socket.off("compose_preview_ready", onPreview);
      socket.off("compose_layout_template", onTemplate);
      socket.off("compose_job_accepted", onAccepted);
      socket.off("compose_job_rejected", onRejected);
    };
  }, [setDoc]);

  useEffect(() => {
    if (!dateDir) return;
    socket.emit("compose_streams_info", {
      session_name: sessionName, date_dir: dateDir,
      streams: camKey ? camKey.split("|") : undefined,
    });
  }, [sessionName, dateDir, camKey]);

  useEffect(() => {
    if (!fpsTouched.current && info?.suggested_fps) setFps(String(info.suggested_fps));
  }, [info?.suggested_fps]);

  const applyTemplate = (tpl = template, audio = templateAudio) => {
    templateReqId.current += 1;
    const spec = doc?.tiles.find((t) => t.kind === "spectrogram")?.options;
    socket.emit("compose_layout_template", {
      request_id: templateReqId.current,
      session_name: sessionName, date_dir: dateDir,
      template: tpl, audio_mode: audio,
      streams: cameras.map((c) => c.name),
      mics: micDevices.map((d) => d.id),
      spectrogram: spec ? {
        color: spec.color, fmin_hz: spec.fmin_hz, fmax_hz: spec.fmax_hz,
        fscale: spec.fscale, ascale: spec.ascale, gain: spec.gain,
      } : {
        color: DEFAULT_SPECTROGRAM.color, fmax_hz: DEFAULT_SPECTROGRAM.fmax_hz,
        gain: DEFAULT_SPECTROGRAM.gain,
      },
      canvas: keepCanvas && doc ? doc.canvas : undefined,
    });
  };

  // No layout yet: start from Auto over every camera.
  useEffect(() => {
    if (infoReady && !doc) applyTemplate("auto", "none");
  }, [infoReady, !doc]); // eslint-disable-line react-hooks/exhaustive-deps

  // The preview follows every edit (debounced).
  useEffect(() => {
    if (!doc || !infoReady || missing.length) return undefined;
    const timer = setTimeout(() => {
      previewReqId.current += 1;
      setPreviewing(true);
      socket.emit("compose_preview", {
        request_id: previewReqId.current, session_name: sessionName,
        date_dir: dateDir, layout_doc: doc,
      });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [docKey, infoReady, missing.length, dateDir, sessionName]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!dates.length) return null; // nothing composable in this session

  // ── canvas size ──────────────────────────────────────────────────────
  const presetIdx = doc ? CANVAS_PRESETS.findIndex(
    (p) => p.width === doc.canvas.width && p.height === doc.canvas.height) : -1;
  const resizeCanvas = (w, h) => {
    if (!doc || !w || !h) return;
    const W = Math.min(LIMITS.maxCanvasW, Math.max(LIMITS.minCanvas, w));
    const H = Math.min(LIMITS.maxCanvasH, Math.max(LIMITS.minCanvas, h));
    setDoc(fitToCanvas(doc, W, H));
  };

  // ── sources ──────────────────────────────────────────────────────────
  const full = (doc?.tiles.length || 0) >= LIMITS.maxTiles;
  const addSource = (kind, source, at) => {
    if (!doc || full) return;
    const tile = kind === "camera"
      ? newCameraTile(doc, source, cameras.find((c) => c.name === source))
      : newSpectrogramTile(doc, source,
        doc.tiles.find((t) => t.kind === "spectrogram")?.options);
    if (at) {
      const [, , w, h] = tile.rect;
      tile.rect = clampRect([at.x - w / 2, at.y - h / 2, w, h], doc.canvas);
    }
    addTile(tile);
  };
  const toggleTrack = (id, on) => setDoc((d) => ({
    ...d,
    audio_tracks: on
      ? [...new Set([...(d.audio_tracks || []), id])].slice(0, LIMITS.maxAudioTracks)
      : (d.audio_tracks || []).filter((m) => m !== id),
  }));
  const dragSource = (kind, source) => (e) => {
    e.dataTransfer.setData(SOURCE_MIME, JSON.stringify({ kind, source }));
    e.dataTransfer.effectAllowed = "copy";
  };
  const labelFor = (tile) => (tile.kind === "camera"
    ? tile.source
    : micDevices.find((d) => d.id === tile.source)?.label || tile.source);

  const setZ = (where) => {
    if (!selected) return;
    const zs = doc.tiles.map((t) => t.z);
    updateTile(selected.id, { z: where === "front" ? Math.max(...zs) + 1 : Math.min(...zs) - 1 });
  };
  const duplicate = () => {
    if (!selected || full) return;
    const [x, y, w, h] = selected.rect;
    addTile({
      ...selected, id: newTileId(doc, selected.kind === "camera" ? "c" : "a"),
      rect: clampRect([x + 24, y + 24, w, h], doc.canvas),
      z: selected.z, options: { ...selected.options },
    });
  };

  // ── render ───────────────────────────────────────────────────────────
  const jobList = Object.values(jobs)
    .filter((j) => (j.spec?.session_name || sessionName) === sessionName)
    .sort((a, b) => b.created_at - a.created_at);
  const anyActive = jobList.some((j) => j.state === "queued" || j.state === "running");
  const elsewhere = Object.values(jobs).filter(
    (j) => (j.state === "queued" || j.state === "running")
      && j.spec?.session_name && j.spec.session_name !== sessionName,
  );
  const effectiveFps = Number(fps) || info?.suggested_fps || 15;
  const submit = () => {
    setNotice("");
    socket.emit("compose_session_video", {
      session_name: sessionName, date_dir: dateDir || undefined,
      fps: effectiveFps, fmt: hasSpectrogram ? "mkv" : fmt, layout_doc: doc,
    });
  };
  const canRender = doc && infoReady && !missing.length && !anyActive
    && doc.tiles.some((t) => t.kind === "camera");

  const verdict = session?.day_verdicts?.[dateDir]
    || (dates.length === 1 ? session?.framesync_verdict : null);

  return (
    <section className="card compose-panel compose-editor">
      <div className="compose-editor__title">
        <h3>Compose video</h3>
        {dates.length > 1 && (
          <label className="compose-editor__inline">
            Recording day
            <select value={dateDir} onChange={(e) => setDateDir(e.target.value)}>
              {dates.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        )}
      </div>
      <p className="modal-subtext">
        Place this day's cameras and spectrograms on the output canvas. What
        you see here is what gets rendered, aligned by each camera's real
        per-frame timestamp. Renders run in the background; you can leave
        this page.
      </p>

      {info?.error && <div className="compose-job__error">{info.error}</div>}

      <div className="compose-editor__toolbar">
        <label className="compose-editor__inline">
          Start from
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            {TEMPLATES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          <select value={templateAudio} onChange={(e) => setTemplateAudio(e.target.value)}
            disabled={!micDevices.length}>
            {TEMPLATE_AUDIO.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="compose-editor__inline compose-editor__check"
          title="Fit the template into the current canvas size instead of using its own">
          <input type="checkbox" checked={keepCanvas}
            onChange={(e) => setKeepCanvas(e.target.checked)} />
          keep canvas size
        </label>
        <button className="btn btn-small" onClick={() => applyTemplate()} disabled={!infoReady}
          title="Replace the layout with this arrangement of every camera">
          Apply
        </button>
        <span className="compose-editor__spacer" />
        <button className="btn btn-small" onClick={undo} disabled={!undoStack.current.length}>Undo</button>
        <button className="btn btn-small" onClick={redo} disabled={!redoStack.current.length}>Redo</button>
      </div>

      {doc && (
        <div className="compose-editor__toolbar">
          <label className="compose-editor__inline">
            Canvas
            <select
              value={presetIdx >= 0 ? String(presetIdx) : "custom"}
              onChange={(e) => {
                const p = CANVAS_PRESETS[Number(e.target.value)];
                if (p) resizeCanvas(p.width, p.height);
              }}
            >
              {CANVAS_PRESETS.map((p, i) => <option key={p.label} value={String(i)}>{p.label}</option>)}
              <option value="custom">Custom</option>
            </select>
          </label>
          <CanvasSize canvas={doc.canvas} onApply={resizeCanvas} />
          <label className="compose-editor__inline" title="Background (shows wherever no tile covers the canvas)">
            Background
            <input type="color" value={doc.canvas.background}
              onChange={(e) => setDoc({ ...doc, canvas: { ...doc.canvas, background: e.target.value } })} />
          </label>
        </div>
      )}

      {missing.length > 0 && (
        <div className="compose-editor__warn">
          This layout uses sources this day doesn&apos;t have: {missing.join(", ")}.
          Change those tiles&apos; source, delete them, or start from a template.
        </div>
      )}

      <div className="compose-editor__body">
        <aside className="compose-editor__sources">
          <div className="compose-editor__sources-head">Cameras</div>
          {cameras.map((c) => (
            <div key={c.name} className="compose-editor__source" draggable
              onDragStart={dragSource("camera", c.name)}
              title="Drag onto the canvas, or Add">
              <span className="compose-editor__source-name">{c.name}</span>
              <span className="compose-panel__hint">
                {c.width}×{c.height}{c.fps ? ` · ${c.fps} fps` : ""}
              </span>
              <button className="btn btn-small" disabled={full || !doc}
                onClick={() => addSource("camera", c.name)}>Add</button>
            </div>
          ))}
          {!cameras.length && <div className="compose-panel__hint">Loading…</div>}
          {micDevices.length > 0 && (
            <>
              <div className="compose-editor__sources-head">Microphones</div>
              {micDevices.map((d) => (
                <div key={d.id} className="compose-editor__source" draggable
                  onDragStart={dragSource("spectrogram", d.id)}
                  title="Drag onto the canvas for a spectrogram, or Add">
                  <span className="compose-editor__source-name">{d.label}</span>
                  <label className="compose-editor__track" title="Mux this microphone into the video as an audio track">
                    <input type="checkbox" checked={(doc?.audio_tracks || []).includes(d.id)}
                      onChange={(e) => toggleTrack(d.id, e.target.checked)} />
                    audio track
                  </label>
                  <button className="btn btn-small" disabled={full || !doc}
                    onClick={() => addSource("spectrogram", d.id)}>Spectrogram</button>
                </div>
              ))}
            </>
          )}
          {full && <div className="compose-panel__hint">At most {LIMITS.maxTiles} tiles.</div>}
        </aside>

        <div className="compose-editor__stage">
          {doc ? (
            <>
              <LayoutCanvas
                doc={doc} selectedId={selectedId} onSelect={setSelectedId}
                onRect={(id, rect) => updateTile(id, { rect })}
                images={images} labelFor={labelFor}
                onDropSource={(kind, source, x, y) => addSource(kind, source, { x, y })}
                onDelete={deleteTile}
              />
              <div className="compose-editor__stage-foot">
                <span>
                  {doc.canvas.width}×{doc.canvas.height} · {doc.tiles.length} tile
                  {doc.tiles.length === 1 ? "" : "s"}
                  {previewing ? " · updating preview…" : ""}
                </span>
                {preview?.image && (
                  <button className="btn btn-small"
                    onClick={() => fullRef.current?.requestFullscreen?.().catch(() => {})}>
                    View full frame
                  </button>
                )}
              </div>
              {preview?.error && <div className="compose-job__error">{preview.error}</div>}
              {preview?.image && (
                <div className="compose-editor__full" ref={fullRef}
                  onClick={() => document.fullscreenElement && document.exitFullscreen?.()}>
                  <img src={preview.image} alt="Rendered preview frame (mid-session)" />
                </div>
              )}
            </>
          ) : (
            <div className="compose-panel__preview-empty">Loading the session&apos;s cameras…</div>
          )}
        </div>

        <TileInspector
          tile={selected} doc={doc || { canvas: { width: 1, height: 1 }, tiles: [] }}
          cameras={cameras} micDevices={micDevices} durationS={info?.duration_s} info={info}
          onChange={(patch) => selected && updateTile(selected.id, patch)}
          onDelete={() => selected && deleteTile(selected.id)}
          onDuplicate={duplicate} onZ={setZ}
          isTrack={!!selected && (doc?.audio_tracks || []).includes(selected.source)}
          onToggleTrack={(on) => selected && toggleTrack(selected.source, on)}
        />
      </div>

      <div className="compose-editor__toolbar compose-editor__render-row">
        <label className="compose-editor__inline">
          Output FPS
          <input type="number" min="1" max="60" value={fps} className="compose-editor__fps"
            placeholder={info?.suggested_fps || ""}
            onChange={(e) => { fpsTouched.current = true; setFps(e.target.value); }} />
          {!fpsTouched.current && info?.suggested_fps ? (
            <span className="compose-panel__hint">(camera rate)</span>
          ) : null}
        </label>
        <label className="compose-editor__inline">
          Format
          <select value={hasSpectrogram ? "mkv" : fmt} onChange={(e) => setFmt(e.target.value)}
            disabled={hasSpectrogram}
            title={hasSpectrogram ? "Spectrogram tiles are encoded with ffmpeg to Matroska" : ""}>
            <option value="mp4">MP4</option>
            <option value="mkv">MKV</option>
          </select>
        </label>
        {info?.duration_s ? (
          <span className="compose-panel__hint">
            {formatSeconds(info.duration_s)} of video (the cameras&apos; overlap)
          </span>
        ) : null}
      </div>

      {verdict?.status ? (
        <div className={`compose-panel__sync compose-panel__sync--${SYNC_CLASS[verdict.status]}`}>
          Sync quality {dateDir ? `(${dateDir})` : ""}: <strong>{SYNC_LABEL[verdict.status]}</strong>
          {worstSummary(verdict) ? ` — ${worstSummary(verdict)}` : ""}
          <span className="compose-panel__sync-note"> validated automatically; not recomputed here</span>
        </div>
      ) : (
        <div className="compose-panel__sync compose-panel__sync--muted">
          Sync quality not yet validated for this day.
        </div>
      )}

      {notice && <div className="compose-job__error">{notice}</div>}

      <div className="compose-panel__actions">
        <button className="btn" onClick={submit} disabled={!canRender}>
          {anyActive ? "Rendering…" : "Render video"}
        </button>
      </div>

      {elsewhere.map((j) => (
        <div key={j.id} className="compose-panel__elsewhere">
          Rendering <strong>{j.spec.session_name}</strong>:{" "}
          {j.state === "queued" ? "queued" : `${Math.round((j.progress || 0) * 100)}%`}
          {onSelectSession && (
            <button className="btn btn-small" onClick={() => onSelectSession(j.spec.session_name)}>
              Show
            </button>
          )}
        </div>
      ))}
      {anyActive && (
        <div className="compose-panel__hint compose-panel__render-note">
          The render runs on the controller: it carries on if you leave this
          page, but stops if the controller restarts (e.g. a software update).
        </div>
      )}
      {jobList.length > 0 && (
        <div className="compose-panel__jobs">
          {jobList.map((job) => <ComposeJobRow key={job.id} job={job} />)}
        </div>
      )}
    </section>
  );
}

function formatSeconds(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.round(s % 60);
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec} s` : `${sec} s`;
}

/** Custom canvas width × height, applied on Enter / leaving the field
 *  (rounded to even numbers, as the encoder needs). */
function CanvasSize({ canvas, onApply }) {
  const [w, setW] = useState(String(canvas.width));
  const [h, setH] = useState(String(canvas.height));
  useEffect(() => { setW(String(canvas.width)); setH(String(canvas.height)); },
    [canvas.width, canvas.height]);
  const apply = () => {
    const nw = Math.round(Number(w) / 2) * 2;
    const nh = Math.round(Number(h) / 2) * 2;
    if (nw && nh && (nw !== canvas.width || nh !== canvas.height)) onApply(nw, nh);
    else { setW(String(canvas.width)); setH(String(canvas.height)); }
  };
  const onKey = (e) => { if (e.key === "Enter") apply(); };
  return (
    <span className="compose-editor__inline compose-editor__size">
      <input type="number" value={w} min={LIMITS.minCanvas} max={LIMITS.maxCanvasW} step={2}
        onChange={(e) => setW(e.target.value)} onBlur={apply} onKeyDown={onKey} aria-label="Canvas width" />
      ×
      <input type="number" value={h} min={LIMITS.minCanvas} max={LIMITS.maxCanvasH} step={2}
        onChange={(e) => setH(e.target.value)} onBlur={apply} onKeyDown={onKey} aria-label="Canvas height" />
    </span>
  );
}
