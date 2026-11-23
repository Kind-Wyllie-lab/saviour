import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import usePersistedState from "/src/hooks/usePersistedState";
import useComposeJobs from "/src/hooks/useComposeJobs";
import ComposeJobRow from "../Recording/SessionDetailPage/ComposeJobRow";
import { SYNC_CLASS, SYNC_LABEL, worstSummary } from "../Recording/syncFormat";
import { composeJobSummary } from "../Recording/sessionFormat";
import LayoutCanvas, { SOURCE_MIME } from "./LayoutCanvas";
import TileInspector from "./TileInspector";
import Timeline from "./Timeline";
import {
  CANVAS_PRESETS, DEFAULT_SPECTROGRAM, LIMITS, TEMPLATES, TEMPLATE_AUDIO,
  clampRect, fitToCanvas, formatSpan, isAccelKind, missingSources, msToNs,
  newAccelTraceTile, newCameraTile, newOrientationTile, newSpectrogramTile,
  newTileId, nsToMs, streamsByDate,
} from "./composeLayout";
import { formatUtcTime } from "/src/basic/utils/time";
import "./ComposeEditor.css";

const PREVIEW_DEBOUNCE_MS = 300;
// Output frame rates offered besides "match cameras" (compose allows 1-60).
const FPS_CHOICES = [60, 50, 30, 25, 24, 15, 12, 10, 5, 1];
const UNDO_DEPTH = 50;

/**
 * Post-Process compose: a layout editor (plans/compose-layout-editor.md).
 * Pick the output canvas, place the session's cameras and AudioMoth
 * spectrograms on it, set each tile's options, and render. The layout
 * document this edits is exactly what the controller previews and renders
 * (src/controller/compose_layout.py), so the canvas is what you get.
 *
 * Two modes over the same layout document (plans/post-process-page-layout.md):
 * Simple picks cameras / layout / audio / range and rebuilds the layout from
 * a built-in template on every change; Advanced is the canvas editor.
 * `active` is false while the Post-Process card is closed: no previews, no
 * keyboard shortcuts. `onSummary` gets the card header's one-liner.
 */
export default function ComposeEditor({
  sessionName, session, files, onSelectSession, active = true, onSummary,
  ephysSession = false, ephysState, onOpenEphys, loadJob, onEditCopy,
}) {
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
  const [previewMs, setPreviewMs] = useState(null);    // the moment previewed
  const [previewedMs, setPreviewedMs] = useState(null); // what the last reply showed
  const [notice, setNotice] = useState("");
  const [template, setTemplate] = usePersistedState("compose_template", "auto");
  const [templateAudio, setTemplateAudio] = usePersistedState("compose_template_audio", "none");
  const [keepCanvas, setKeepCanvas] = usePersistedState("compose_template_keep_canvas", false);
  const [fps, setFps] = useState("");
  const fpsTouched = useRef(false);
  const [fmt, setFmt] = usePersistedState("compose_fmt", "mp4");
  const [mode, setMode] = usePersistedState("compose_mode", "simple");
  const simple = mode !== "advanced";
  // Simple mode's choices: cameras / mics left out (so a new session
  // defaults to all of them), and whether to render only part of the day.
  const [excludedCams, setExcludedCams] = useState([]);
  // Simple: which mics are muxed in as audio tracks, and which get a
  // spectrogram panel under the video (each toggled per mic).
  const [trackMics, setTrackMics] = useState([]);
  const [specMics, setSpecMics] = useState([]);
  const [partOnly, setPartOnly] = useState(false);
  // Simple: how to show an aligned headstage accelerometer, and which one.
  const [headShow, setHeadShow] = usePersistedState("compose_headstage_show", "trace");
  const [headSource, setHeadSource] = useState("");
  // True once the layout was changed by hand (not by a template), so going
  // back to Simple, which rebuilds it, asks first.
  const editedRef = useRef(false);
  const templateRangeRef = useRef(undefined);
  const templateTracksRef = useRef(undefined);
  // "Edit a copy" of an earlier render (loadJob = {job, nonce}): the job
  // whose settings are loaded, for the banner.
  const [copyOf, setCopyOf] = useState(null);
  const loadedNonce = useRef(null);
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
  // Headstage accelerometers with output (Ephys panel); only aligned ones
  // have video time to be placed on.
  const accelAll = (info?.session_name === sessionName && info?.date_dir === dateDir
    && info.accel_sources) || [];
  const accelSources = accelAll.filter((a) => a.aligned);
  const infoReady = cameras.length > 0;
  const camSources = (doc?.tiles || []).filter((t) => t.kind === "camera").map((t) => t.source);
  const camKey = [...new Set(camSources)].join("|");
  const missing = infoReady ? missingSources(doc, cameras.map((c) => c.name),
    micDevices.map((d) => d.id), accelSources.map((a) => a.id)) : [];
  const docKey = JSON.stringify(doc);
  const selected = doc?.tiles.find((t) => t.id === selectedId) || null;
  const hasSpectrogram = !!doc?.tiles.some((t) => t.kind === "spectrogram");
  const dayInfo = info?.session_name === sessionName && info?.date_dir === dateDir;
  const windowMs = dayInfo && info.window_start_ns != null
    ? [nsToMs(info.window_start_ns), nsToMs(info.window_end_ns)] : null;
  const range = doc?.range || { start_ns: null, end_ns: null, speed: 1 };
  const rangeMs = [nsToMs(range.start_ns), nsToMs(range.end_ns)];
  const speed = Number(range.speed) || 1;
  const rangeLenMs = windowMs
    ? Math.max(0, Math.min(windowMs[1], rangeMs[1] ?? windowMs[1])
      - Math.max(windowMs[0], rangeMs[0] ?? windowMs[0]))
    : null;

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
    editedRef.current = true;
    setDocRaw(value);
  }, [setDocRaw]);
  const undo = () => {
    const prev = undoStack.current.pop();
    if (!prev) return;
    redoStack.current.push(docRef.current);
    editedRef.current = true;
    docRef.current = prev;
    setDocRaw(prev);
  };
  const redo = () => {
    const next = redoStack.current.pop();
    if (!next) return;
    undoStack.current.push(docRef.current);
    editedRef.current = true;
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

  // Keyboard, page-wide while the editor is open (not only when the canvas
  // has focus, which clicking a tile never gave it), but never while typing
  // in a field: Delete/Backspace removes the selected tile, arrows nudge it
  // (Shift: 10 px), Escape deselects, Ctrl/Cmd+Z undoes, Ctrl/Cmd+Shift+Z or
  // Ctrl+Y redoes. A ref keeps the listener on the latest state.
  const keyRef = useRef(null);
  keyRef.current = (e) => {
    if (!active || simple) return;
    const el = e.target;
    if (el?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName)) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (mod && key === "z") {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if (mod && key === "y") {
      e.preventDefault();
      redo();
      return;
    }
    if (mod || e.altKey || !doc) return;
    const tile = doc.tiles.find((t) => t.id === selectedId);
    if (!tile) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      deleteTile(tile.id);
      return;
    }
    if (e.key === "Escape") {
      setSelectedId(null);
      return;
    }
    const step = e.shiftKey ? 10 : 1;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
      ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d) return;
    e.preventDefault();
    const [x, y, w, h] = tile.rect;
    updateTile(tile.id, { rect: clampRect([x + d[0], y + d[1], w, h], doc.canvas) });
  };
  useEffect(() => {
    const onKey = (e) => keyRef.current?.(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
      setPreviewedMs(nsToMs(d.t_ns));
    };
    const onTemplate = (d) => {
      if (d.request_id !== templateReqId.current) return;
      if (d.error) { setNotice(d.error); return; }
      setNotice("");
      const range = templateRangeRef.current;
      // Simple's tracks as they are now, not when the rebuild was asked
      // for (a toggle may have landed in between).
      const tracks = templateTracksRef.current === undefined
        ? undefined : latestTracksRef.current;
      setDoc({
        ...d.layout_doc,
        ...(range === undefined ? {} : { range }),
        ...(tracks === undefined ? {} : { audio_tracks: tracks }),
      });
      editedRef.current = false;
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
    // ephysState: re-read the headstage list after an Ephys job finishes.
  }, [sessionName, dateDir, camKey, ephysState]);

  useEffect(() => {
    setExcludedCams([]);
    setTrackMics([]);
    setSpecMics([]);
    setPartOnly(false);
    setHeadSource("");
  }, [sessionName, dateDir]);

  useEffect(() => { setCopyOf(null); }, [sessionName]);

  // Load an earlier render's layout, day, frame rate and format into the
  // (Advanced) editor. The layout change is undoable like any other.
  useEffect(() => {
    if (!loadJob || loadJob.nonce === loadedNonce.current) return;
    loadedNonce.current = loadJob.nonce;
    const { job } = loadJob;
    const spec = job?.spec || {};
    if (!spec.layout_doc) return;
    if (doc && editedRef.current && !window.confirm(
      "Replace the layout you're editing with this render's settings? Undo brings yours back.")) return;
    if (spec.date_dir && dates.includes(spec.date_dir)) setDateDir(spec.date_dir);
    setMode("advanced");
    setDoc(spec.layout_doc);
    setSelectedId(null);
    if (spec.fps) { fpsTouched.current = true; setFps(String(spec.fps)); }
    if (spec.fmt) setFmt(spec.fmt);
    setNotice("");
    setCopyOf(job);
  }, [loadJob?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!fpsTouched.current && info?.suggested_fps) setFps(String(info.suggested_fps));
  }, [info?.suggested_fps]);

  // Builds a layout from a built-in template. `range` is what the new
  // layout's time range becomes (default: keep the current one).
  const applyTemplate = (tpl = template, audio = templateAudio, {
    streams = cameras.map((c) => c.name), mics = micDevices.map((d) => d.id),
    range = doc?.range, canvas = keepCanvas && doc ? doc.canvas : undefined,
    headstage, audioTracks,
  } = {}) => {
    templateReqId.current += 1;
    templateRangeRef.current = range;
    // undefined: keep the template's own audio tracks (Advanced).
    templateTracksRef.current = audioTracks;
    const spec = doc?.tiles.find((t) => t.kind === "spectrogram")?.options;
    socket.emit("compose_layout_template", {
      request_id: templateReqId.current,
      session_name: sessionName, date_dir: dateDir,
      template: tpl, audio_mode: mics.length ? audio : "none",
      streams, mics,
      spectrogram: spec ? {
        color: spec.color, fmin_hz: spec.fmin_hz, fmax_hz: spec.fmax_hz,
        fscale: spec.fscale, ascale: spec.ascale, gain: spec.gain,
      } : {
        color: DEFAULT_SPECTROGRAM.color, fmax_hz: DEFAULT_SPECTROGRAM.fmax_hz,
        gain: DEFAULT_SPECTROGRAM.gain,
      },
      canvas,
      headstage,
    });
  };

  // No layout yet (Advanced): start from Auto over every camera.
  useEffect(() => {
    if (!simple && infoReady && !doc) applyTemplate("auto", "none");
  }, [simple, infoReady, !doc]); // eslint-disable-line react-hooks/exhaustive-deps

  // Simple: every choice rebuilds the layout from its template.
  const simpleCams = cameras.map((c) => c.name).filter((n) => !excludedCams.includes(n));
  // Spectrogram panels follow the session's mic order.
  const simpleSpecs = micDevices.map((d) => d.id).filter((m) => specMics.includes(m));
  const simpleTracks = micDevices.map((d) => d.id).filter((m) => trackMics.includes(m));
  const latestTracksRef = useRef(simpleTracks);
  latestTracksRef.current = simpleTracks;
  const headId = accelSources.some((a) => a.id === headSource)
    ? headSource : accelSources[0]?.id || "";
  const simpleHead = headId && headShow !== "none" ? { source: headId, show: headShow } : undefined;
  const simpleKey = [sessionName, dateDir, template,
    simpleCams.join(","), simpleSpecs.join(","), simpleHead ? `${headId}:${headShow}` : ""].join("|");
  useEffect(() => {
    if (!simple || !infoReady || !simpleCams.length) return;
    applyTemplate(template, simpleSpecs.length ? "panel" : "none", {
      streams: simpleCams, mics: simpleSpecs, canvas: undefined, headstage: simpleHead,
      audioTracks: simpleTracks,
      range: partOnly && doc?.range ? doc.range : { start_ns: null, end_ns: null, speed: 1 },
    });
  }, [simple, infoReady, simpleKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Audio tracks don't change the picture: update the layout in place
  // rather than rebuilding it (and don't count it as a hand edit).
  const toggleSimpleTrack = (id) => {
    const next = trackMics.includes(id) ? trackMics.filter((m) => m !== id) : [...trackMics, id];
    setTrackMics(next);
    if (doc) {
      const edited = editedRef.current;
      const ids = micDevices.map((d) => d.id).filter((m) => next.includes(m));
      setDoc((d) => ({ ...d, audio_tracks: ids }));
      editedRef.current = edited;
    }
  };
  const toggleSimpleSpec = (id) => setSpecMics((x) => (
    x.includes(id) ? x.filter((m) => m !== id) : [...x, id]));

  // "Spectrogram strip over the video" was dropped from the templates;
  // a remembered choice of it becomes panels.
  useEffect(() => {
    if (templateAudio === "strip") setTemplateAudio("panel");
  }, [templateAudio, setTemplateAudio]);

  const switchMode = (next) => {
    if (next === mode) return;
    if (next === "simple" && doc && editedRef.current && !window.confirm(
      "Simple rebuilds the layout from its choices, replacing your custom layout. "
      + "Undo in Advanced brings it back. Switch to Simple?")) return;
    setMode(next);
  };
  const choosePart = (on) => {
    setPartOnly(on);
    if (!on && doc) setRangeDoc((d) => ({ ...d, range: { start_ns: null, end_ns: null, speed: 1 } }));
  };

  // Preview the middle of the day until the user scrubs; keep a scrubbed
  // time when it's still inside the (possibly new) window.
  const windowKey = windowMs ? windowMs.join("|") : "";
  useEffect(() => {
    if (!windowMs) return;
    setPreviewMs((cur) => (cur != null && cur >= windowMs[0] && cur <= windowMs[1]
      ? cur : Math.round((windowMs[0] + windowMs[1]) / 2)));
  }, [windowKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // The preview follows every edit and every scrub (debounced; the
  // controller only renders each browser's latest request).
  useEffect(() => {
    if (!active || !doc || !infoReady || missing.length) return undefined;
    const timer = setTimeout(() => {
      previewReqId.current += 1;
      setPreviewing(true);
      socket.emit("compose_preview", {
        request_id: previewReqId.current, session_name: sessionName,
        date_dir: dateDir, layout_doc: doc,
        preview_t_ns: previewMs != null ? msToNs(previewMs) : undefined,
      });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [active, docKey, infoReady, missing.length, dateDir, sessionName, previewMs]); // eslint-disable-line react-hooks/exhaustive-deps

  // The time range isn't part of the layout: changing it doesn't count as
  // a hand edit (Simple keeps it across rebuilds when "Part of it" is on).
  const setRangeDoc = (fn) => {
    const edited = editedRef.current;
    setDoc(fn);
    editedRef.current = edited;
  };
  const setRange = ([a, b]) => setRangeDoc((d) => ({
    ...d, range: { ...(d.range || {}), speed, start_ns: msToNs(a), end_ns: msToNs(b) },
  }));
  const setSpeed = (v) => setRangeDoc((d) => ({
    ...d, range: { start_ns: null, end_ns: null, ...(d.range || {}), speed: v },
  }));

  // An ephys session whose headstage accelerometer can't be placed yet:
  // say what's missing (the Ephys card comes first on the page for these).
  let headstageNote = null;
  if (ephysSession && infoReady && !accelSources.length) {
    if (accelAll.length) {
      headstageNote = "The headstage accelerometer isn't aligned to this session yet: "
        + "run Align, then Process accelerometer, on the Ephys card.";
    } else if (ephysState === "aligned") {
      headstageNote = "Ephys is aligned; process the headstage accelerometer on the "
        + "Ephys card to add it to the video.";
    } else if (ephysState === "uploaded") {
      headstageNote = "The ephys recording isn't aligned yet: run Align, then Process "
        + "accelerometer, on the Ephys card to add the headstage to the video.";
    } else if (ephysState === "none") {
      headstageNote = "No ephys recording uploaded: upload and align it on the Ephys card "
        + "to add the headstage accelerometer to the video.";
    }
  }

  // Card header summary.
  const sessionJobs = Object.values(jobs)
    .filter((j) => (j.spec?.session_name || sessionName) === sessionName)
    .sort((a, b) => b.created_at - a.created_at);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  let summaryText;
  let summaryTone = "muted";
  if (!dates.length) {
    summaryText = "no camera video in this session";
  } else {
    const parts = [];
    if (cameras.length) parts.push(plural(cameras.length, "camera"));
    if (micDevices.length) parts.push(plural(micDevices.length, "mic"));
    const busyJob = sessionJobs.find((j) => j.state === "running" || j.state === "queued");
    const last = sessionJobs[0];
    if (busyJob) {
      parts.push(busyJob.state === "queued" ? "render queued"
        : `rendering ${Math.round((busyJob.progress || 0) * 100)}%`);
      summaryTone = "active";
    } else if (last) {
      parts.push(`last render ${formatUtcTime(last.created_at)} ${last.state}`);
      summaryTone = last.state === "done" ? "ok" : last.state === "failed" ? "bad" : "muted";
    }
    summaryText = parts.join(" · ") || "loading…";
    if (headstageNote && !busyJob) {
      summaryText = `headstage not ready · ${summaryText}`;
      summaryTone = "warn";
    }
  }
  useEffect(() => {
    onSummary?.({ text: summaryText, tone: summaryTone });
  }, [summaryText, summaryTone, onSummary]);

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
      : kind === "accel_trace" ? newAccelTraceTile(doc, source)
        : kind === "orientation" ? newOrientationTile(doc, source)
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
  const labelFor = (tile) => {
    if (tile.kind === "camera") return tile.source;
    if (isAccelKind(tile.kind)) {
      const name = accelAll.find((a) => a.id === tile.source)?.label || tile.source;
      return tile.kind === "orientation" ? `${name} orientation` : name;
    }
    return micDevices.find((d) => d.id === tile.source)?.label || tile.source;
  };

  const setZ = (where) => {
    if (!selected) return;
    const zs = doc.tiles.map((t) => t.z);
    updateTile(selected.id, { z: where === "front" ? Math.max(...zs) + 1 : Math.min(...zs) - 1 });
  };
  const duplicate = () => {
    if (!selected || full) return;
    const [x, y, w, h] = selected.rect;
    addTile({
      ...selected,
      id: newTileId(doc, selected.kind === "camera" ? "c" : isAccelKind(selected.kind) ? "e" : "a"),
      rect: clampRect([x + 24, y + 24, w, h], doc.canvas),
      z: selected.z, options: { ...selected.options },
    });
  };

  // ── render ───────────────────────────────────────────────────────────
  const jobList = sessionJobs;
  const anyActive = jobList.some((j) => j.state === "queued" || j.state === "running");
  // In the card: what's rendering now, else the latest result (the full
  // history for the session is the Renders card).
  const activeJobs = jobList.filter((j) => j.state === "queued" || j.state === "running");
  const cardJobs = activeJobs.length ? activeJobs : jobList.slice(0, 1);
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
    <div className="compose-panel compose-editor">
      <div className="compose-editor__title">
        <div className="compose-mode" role="group" aria-label="Editor mode">
          <button type="button" className={`compose-mode__btn${simple ? " is-active" : ""}`}
            aria-pressed={simple} onClick={() => switchMode("simple")}>Simple</button>
          <button type="button" className={`compose-mode__btn${simple ? "" : " is-active"}`}
            aria-pressed={!simple} onClick={() => switchMode("advanced")}>Advanced</button>
        </div>
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
        {simple
          ? "Choose the cameras and how to show the audio; the layout is arranged for you. "
            + "Use Advanced to place and size tiles yourself."
          : "Place this session's cameras and spectrograms on the output canvas. What "
            + "you see here is what gets rendered."}
        {" "}Video is aligned by each camera&apos;s real per-frame timestamp. Renders
        run in the background; you can leave this page.
      </p>

      {info?.error && <div className="compose-job__error">{info.error}</div>}

      {headstageNote && (
        <div className="post-process__note post-process__note--warn">
          {headstageNote}
          {onOpenEphys && (
            <button type="button" className="btn btn-small" onClick={onOpenEphys}>
              Open Ephys
            </button>
          )}
        </div>
      )}

      {copyOf && !simple && (
        <div className="post-process__note">
          <span>
            Editing a copy of the render from {formatUtcTime(copyOf.created_at)} UTC
            ({composeJobSummary(copyOf)}). Render makes a new video; the original stays.
          </span>
          <button type="button" className="btn btn-small" onClick={() => setCopyOf(null)}>
            Dismiss
          </button>
        </div>
      )}

      {simple ? (
        <div className="compose-simple">
          <div className="compose-simple__form">
            <div className="compose-simple__row">
              <span className="compose-simple__label">Cameras</span>
              <div className="compose-simple__choices">
                {cameras.map((c) => {
                  const on = !excludedCams.includes(c.name);
                  return (
                    <label key={c.name} className="compose-simple__check">
                      <input type="checkbox" checked={on}
                        disabled={on && simpleCams.length === 1}
                        onChange={() => setExcludedCams((x) => (on
                          ? [...x, c.name] : x.filter((n) => n !== c.name)))} />
                      {c.name}
                      <span className="compose-panel__hint">
                        {c.width}×{c.height}{c.fps ? ` · ${c.fps} fps` : ""}
                      </span>
                    </label>
                  );
                })}
                {!cameras.length && <span className="compose-panel__hint">Loading…</span>}
              </div>
            </div>
            <label className="compose-simple__row">
              <span className="compose-simple__label">Layout</span>
              <select value={template} onChange={(e) => setTemplate(e.target.value)}
                disabled={simpleCams.length + simpleSpecs.length < 2}
                title={simpleCams.length + simpleSpecs.length < 2
                  ? "One camera fills the frame: add a spectrogram or another camera to arrange"
                  : "Arranges the cameras and spectrograms"}>
                {TEMPLATES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
              {simpleSpecs.length > 0 && (
                <span className="compose-panel__hint">
                  {template === "side" ? "Spectrograms in a column beside the video"
                    : template === "grid" ? "Spectrograms as cells of the grid"
                      : "Spectrograms under the video"}
                </span>
              )}
            </label>
            {micDevices.length > 0 && (
              <div className="compose-simple__row">
                <span className="compose-simple__label">Microphones</span>
                <div className="compose-mics">
                  {micDevices.map((d) => {
                    const track = trackMics.includes(d.id);
                    const spec = specMics.includes(d.id);
                    const full = !track && simpleTracks.length >= LIMITS.maxAudioTracks;
                    return (
                      <div key={d.id} className="compose-mic">
                        <span className="compose-mic__name">{d.label}</span>
                        <span className="compose-mic__toggles">
                          <button type="button" aria-pressed={track} disabled={full}
                            className={`compose-chip${track ? " is-on" : ""}`}
                            title={full ? `At most ${LIMITS.maxAudioTracks} audio tracks`
                              : track ? "Remove this microphone's sound from the video"
                                : "Add this microphone's sound to the video"}
                            onClick={() => toggleSimpleTrack(d.id)}>
                            <span aria-hidden="true">{track ? "✓" : "+"}</span> Audio
                          </button>
                          <button type="button" aria-pressed={spec}
                            className={`compose-chip${spec ? " is-on" : ""}`}
                            title={spec ? "Remove this microphone's spectrogram"
                              : "Add a spectrogram of this microphone under the video"}
                            onClick={() => toggleSimpleSpec(d.id)}>
                            <span aria-hidden="true">{spec ? "✓" : "+"}</span> Spectrogram
                          </button>
                        </span>
                      </div>
                    );
                  })}
                </div>
                <span className="compose-panel__hint">
                  Audio adds the sound to the video file; spectrograms go under the video.
                </span>
              </div>
            )}
            {accelSources.length > 0 && (
              <div className="compose-simple__row">
                <span className="compose-simple__label">Headstage</span>
                {accelSources.length > 1 && (
                  <select value={headId} onChange={(e) => setHeadSource(e.target.value)}
                    aria-label="Headstage">
                    {accelSources.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
                  </select>
                )}
                <select value={headShow} onChange={(e) => setHeadShow(e.target.value)}
                  aria-label="Show the headstage as">
                  <option value="trace">Movement trace under the video</option>
                  <option value="orientation">Head orientation inset</option>
                  <option value="both">Trace and orientation</option>
                  <option value="none">Don&apos;t show</option>
                </select>
              </div>
            )}
            <div className="compose-simple__row">
              <span className="compose-simple__label">Length</span>
              <div className="compose-simple__choices">
                <label className="compose-simple__check">
                  <input type="radio" name="compose-part" checked={!partOnly}
                    onChange={() => choosePart(false)} />
                  Whole recording
                  {windowMs ? <span className="compose-panel__hint">{formatSpan(windowMs[1] - windowMs[0])}</span> : null}
                </label>
                <label className="compose-simple__check">
                  <input type="radio" name="compose-part" checked={partOnly}
                    onChange={() => choosePart(true)} />
                  Part of it
                </label>
              </div>
            </div>
          </div>

          <div className="compose-simple__preview">
            {partOnly && windowMs && previewMs != null && (
              <Timeline
                windowMs={windowMs}
                cameras={cameras.filter((c) => simpleCams.includes(c.name))}
                mics={micDevices.filter((d) => simpleSpecs.includes(d.id)
                  || simpleTracks.includes(d.id))}
                rangeMs={rangeMs} speed={speed}
                previewMs={previewMs} previewedMs={previewedMs}
                onRange={setRange} onSpeed={setSpeed} onPreview={setPreviewMs}
              />
            )}
            {preview?.error && <div className="compose-job__error">{preview.error}</div>}
            {preview?.image ? (
              <div className="compose-editor__full" ref={fullRef}
                onClick={() => document.fullscreenElement && document.exitFullscreen?.()}>
                <img src={preview.image} alt="Preview of the composed video" />
              </div>
            ) : !preview?.error && (
              <div className="compose-panel__preview-empty">
                {previewing || !doc ? "Building the preview…" : "No preview yet"}
              </div>
            )}
            <div className="compose-editor__stage-foot">
              <span>
                {doc ? `${doc.canvas.width}×${doc.canvas.height}` : ""}
                {previewing ? " · updating preview…" : ""}
              </span>
              {preview?.image && (
                <button className="btn btn-small"
                  onClick={() => fullRef.current?.requestFullscreen?.().catch(() => {})}>
                  <span aria-hidden="true">⛶</span> View fullscreen
                </button>
              )}
            </div>
          </div>
        </div>
      ) : (<>
      <div className="compose-editor__toolbar">
        <label className="compose-editor__inline">
          Template
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
          title="Replace the current layout with this template, using every camera (Undo brings the old layout back)">
          Apply template
        </button>
        <span className="compose-editor__spacer" />
        <button className="btn btn-small" onClick={undo} disabled={!undoStack.current.length}
          title="Undo (Ctrl+Z)">Undo</button>
        <button className="btn btn-small" onClick={redo} disabled={!redoStack.current.length}
          title="Redo (Ctrl+Shift+Z or Ctrl+Y)">Redo</button>
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
          This layout uses sources this session doesn&apos;t have{dates.length > 1 ? " on this day" : ""}: {missing.join(", ")}.
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
                onClick={() => addSource("camera", c.name)}>Add camera</button>
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
                    onClick={() => addSource("spectrogram", d.id)}>Add spectrogram</button>
                </div>
              ))}
            </>
          )}
          {accelAll.length > 0 && (
            <>
              <div className="compose-editor__sources-head">Headstage accelerometer</div>
              {accelAll.map((a) => (
                <div key={a.id} className="compose-editor__source">
                  <span className="compose-editor__source-name">{a.label}</span>
                  {a.aligned ? (
                    <>
                      <span className="compose-panel__hint">
                        {a.start_ns != null && a.end_ns != null
                          ? `${Math.round((a.end_ns - a.start_ns) / 1e9)} s of motion data` : ""}
                      </span>
                      <span className="compose-editor__source-buttons">
                        <button className="btn btn-small" disabled={full || !doc} draggable
                          onDragStart={dragSource("accel_trace", a.id)}
                          onClick={() => addSource("accel_trace", a.id)}
                          title="A scrolling trace of acceleration, tilt or movement (drag onto the canvas, or click)">
                          Add trace
                        </button>
                        <button className="btn btn-small" disabled={full || !doc} draggable
                          onDragStart={dragSource("orientation", a.id)}
                          onClick={() => addSource("orientation", a.id)}
                          title="The headstage drawn as a box tilted by its pitch and roll (drag onto the canvas, or click)">
                          Add orientation
                        </button>
                      </span>
                    </>
                  ) : (
                    <span className="compose-panel__hint">
                      Not aligned to this session yet: run Align, then Accelerometer, on the Ephys panel.
                    </span>
                  )}
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
              />
              {windowMs && previewMs != null && (
                <Timeline
                  windowMs={windowMs}
                  cameras={cameras.filter((c) => camSources.includes(c.name))}
                  mics={[
                    ...micDevices.filter((d) => (doc?.audio_tracks || []).includes(d.id)
                      || doc?.tiles.some((t) => t.kind === "spectrogram" && t.source === d.id)),
                    // Headstages on the canvas, as a lane of their aligned span.
                    ...accelSources.filter((a) => doc?.tiles.some((t) => t.source === a.id))
                      .map((a) => ({ id: a.id, label: a.label,
                        segments: a.start_ns != null ? [[a.start_ns, a.end_ns]] : [] })),
                  ]}
                  rangeMs={rangeMs} speed={speed}
                  previewMs={previewMs} previewedMs={previewedMs}
                  onRange={setRange} onSpeed={setSpeed} onPreview={setPreviewMs}
                />
              )}
              <div className="compose-editor__stage-foot">
                <span>
                  {doc.canvas.width}×{doc.canvas.height} · {doc.tiles.length} tile
                  {doc.tiles.length === 1 ? "" : "s"}
                  {previewing ? " · updating preview…" : ""}
                </span>
                {preview?.image && (
                  <button className="btn btn-small"
                    onClick={() => fullRef.current?.requestFullscreen?.().catch(() => {})}>
                    <span aria-hidden="true">⛶</span> View fullscreen
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
          cameras={cameras} micDevices={micDevices} accelSources={accelSources}
          durationS={rangeLenMs != null ? rangeLenMs / 1000 : info?.duration_s} info={info}
          onChange={(patch) => selected && updateTile(selected.id, patch)}
          onDelete={() => selected && deleteTile(selected.id)}
          onDuplicate={duplicate} onZ={setZ}
          isTrack={!!selected && (doc?.audio_tracks || []).includes(selected.source)}
          onToggleTrack={(on) => selected && toggleTrack(selected.source, on)}
        />
      </div>
      </>)}

      <div className="compose-editor__toolbar compose-editor__render-row">
        {!simple && (<>
        <label className="compose-editor__inline"
          title="Frames per second of the video. Matching the cameras keeps every frame they recorded; a lower rate makes a smaller file and renders faster. 1-60 fps; cameras faster than 60 fps are down-sampled.">
          Output frame rate
          <select value={fpsTouched.current ? fps : "match"}
            onChange={(e) => {
              fpsTouched.current = e.target.value !== "match";
              setFps(e.target.value === "match" ? String(info?.suggested_fps || "") : e.target.value);
            }}>
            <option value="match">Match cameras{info?.suggested_fps ? ` (${info.suggested_fps} fps)` : ""}</option>
            {FPS_CHOICES.filter((f) => f !== info?.suggested_fps).map((f) => (
              <option key={f} value={String(f)}>{f} fps</option>
            ))}
          </select>
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
        </>)}
        {rangeLenMs ? (
          <span className="compose-panel__hint">
            {formatSpan(rangeLenMs / speed)} of video
            {speed !== 1 ? ` (${formatSpan(rangeLenMs)} at ${speed}×)` : ""}
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
          Sync quality not yet validated for this {dates.length > 1 ? "day" : "session"}.
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
      {cardJobs.length > 0 && (
        <div className="compose-panel__jobs">
          {cardJobs.map((job) => <ComposeJobRow key={job.id} job={job} onEditCopy={onEditCopy} />)}
        </div>
      )}
      {jobList.length > cardJobs.length && (
        <div className="compose-panel__hint">
          This session has {jobList.length} renders: all of them are under Renders, below.
        </div>
      )}
    </div>
  );
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
