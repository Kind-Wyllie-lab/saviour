import React, { useRef, useState } from "react";
import { SPEEDS, formatSpan, nsToMs, parseSpan } from "./composeLayout";

const clock = (ms) => new Date(ms).toLocaleTimeString([], {
  hour: "2-digit", minute: "2-digit", second: "2-digit",
});

/** An offset from the window start, typed as h:mm:ss; applied on Enter or
 *  leaving the field. */
function SpanInput({ value, onCommit, label, title }) {
  const [text, setText] = useState(null);       // null = show `value`
  const commit = () => {
    if (text == null) return;
    const ms = parseSpan(text);
    setText(null);
    if (ms != null) onCommit(ms);
  };
  return (
    <label className="timeline__field" title={title}>
      {label}
      <input
        type="text" value={text ?? formatSpan(value)} spellCheck={false}
        onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      />
    </label>
  );
}

/**
 * The session as a track: when each camera and AudioMoth recorded, the range
 * to render (drag its edges, or type offsets), and the preview playhead
 * (drag it, or click the track). The canvas shows the real frame at the
 * playhead, so finding the moment to start from is scrubbing, not
 * guessing. Speed plays the range faster or slower.
 */
export default function Timeline({
  windowMs, cameras, mics = [], rangeMs, speed, previewMs, previewedMs, onRange, onSpeed,
  onPreview,
}) {
  const trackRef = useRef(null);
  const [w0, w1] = windowMs;
  const span = Math.max(1, w1 - w0);
  const inMs = rangeMs[0] ?? w0;
  const outMs = rangeMs[1] ?? w1;
  const pct = (ms) => `${((Math.min(w1, Math.max(w0, ms)) - w0) / span) * 100}%`;
  const fromX = (clientX) => {
    const box = trackRef.current.getBoundingClientRect();
    return w0 + Math.min(1, Math.max(0, (clientX - box.left) / box.width)) * span;
  };

  // Snap a dragged time to a whole tenth of a second.
  const tenth = (ms) => Math.round(ms / 100) * 100;
  const setIn = (ms) => onRange([ms <= w0 ? null : Math.min(tenth(ms), outMs - 100), rangeMs[1]]);
  const setOut = (ms) => onRange([rangeMs[0], ms >= w1 ? null : Math.max(tenth(ms), inMs + 100)]);

  const drag = (what) => (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const apply = (ev) => {
      const ms = fromX(ev.clientX);
      if (what === "in") setIn(ms);
      else if (what === "out") setOut(ms);
      else onPreview(Math.round(ms));
    };
    apply(e);
    const up = () => {
      window.removeEventListener("pointermove", apply);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", apply);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const step = (d) => onPreview(Math.min(w1, Math.max(w0, previewMs + d)));
  const length = outMs - inMs;
  // Cameras first, then the AudioMoths (dimmed when the layout doesn't use
  // them), each a labelled lane of its recorded stretches.
  const lanes = [
    ...cameras.map((c) => ({ key: `c:${c.name}`, label: c.name, kind: "camera",
      segments: c.segments, used: true })),
    ...mics.map((m) => ({ key: `m:${m.id}`, label: m.label, kind: "mic",
      segments: m.segments, used: m.used })),
  ];

  return (
    <div className="timeline">
      <div className="timeline__body">
      <div className="timeline__labels" aria-hidden="true">
        {lanes.map((l) => (
          <span key={l.key} className={`timeline__label${l.used ? "" : " timeline__label--unused"}`}
            title={l.used ? l.label : `${l.label} (not in the layout)`}>
            {l.label}
          </span>
        ))}
      </div>
      <div className="timeline__track" ref={trackRef} onPointerDown={drag("playhead")}
        title="Click or drag to preview that moment">
        {lanes.map((l) => (
          <div key={l.key}
            className={`timeline__lane timeline__lane--${l.kind}${l.used ? "" : " timeline__lane--unused"}`}
            title={`${l.label}: recorded`}>
            {(l.segments || []).map(([a, b], i) => {
              const s = nsToMs(a);
              const e = nsToMs(b);
              if (e < w0 || s > w1) return null;
              return (
                <span key={i} className="timeline__seg"
                  style={{ left: pct(s), width: `calc(${pct(e)} - ${pct(s)})` }} />
              );
            })}
          </div>
        ))}
        <div className="timeline__dim" style={{ left: 0, width: pct(inMs) }} />
        <div className="timeline__dim" style={{ left: pct(outMs), right: 0 }} />
        <span className="timeline__edge timeline__edge--in" style={{ left: pct(inMs) }}
          onPointerDown={drag("in")} title="Drag: where the render starts" />
        <span className="timeline__edge timeline__edge--out" style={{ left: pct(outMs) }}
          onPointerDown={drag("out")} title="Drag: where the render ends" />
        <span className="timeline__playhead" style={{ left: pct(previewMs) }}
          onPointerDown={drag("playhead")} />
      </div>
      <span />
      <div className="timeline__scale">
        <span>{clock(w0)}</span>
        <span>{formatSpan(span)} recorded</span>
        <span>{clock(w1)}</span>
      </div>
      </div>

      <div className="timeline__controls">
        <span className="timeline__now" title="The time the preview shows">
          <button className="btn btn-small" onClick={() => step(-1000)} title="Back 1 s">−1 s</button>
          <strong>{formatSpan(previewMs - w0)}</strong>
          <span className="compose-panel__hint">{clock(previewMs)}</span>
          <button className="btn btn-small" onClick={() => step(1000)} title="Forward 1 s">+1 s</button>
          {previewedMs != null && Math.abs(previewedMs - previewMs) > 500 && (
            <span className="compose-panel__hint">updating…</span>
          )}
        </span>
        <span className="timeline__range">
          <SpanInput label="From" value={inMs - w0} onCommit={(ms) => setIn(w0 + ms)}
            title="Render from this far into the recording (h:mm:ss)" />
          <button className="btn btn-small" onClick={() => setIn(previewMs)}
            title="Start the render at the previewed moment">Set to preview</button>
          <SpanInput label="to" value={outMs - w0} onCommit={(ms) => setOut(w0 + ms)}
            title="Render up to this far into the recording (h:mm:ss)" />
          <button className="btn btn-small" onClick={() => setOut(previewMs)}
            title="End the render at the previewed moment">Set to preview</button>
          {(rangeMs[0] != null || rangeMs[1] != null) && (
            <button className="btn btn-small" onClick={() => onRange([null, null])}>Whole recording</button>
          )}
        </span>
        <label className="timeline__field">
          Speed
          <select value={speed} onChange={(e) => onSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </label>
        <span className="compose-panel__hint">
          {formatSpan(length)} of recording → {formatSpan(length / speed)} of video
          {speed > 2 || speed < 0.5 ? " (no audio tracks at this speed)" : ""}
        </span>
      </div>
    </div>
  );
}
