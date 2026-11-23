import { useState, useEffect } from "react";
import { parseStamp } from "/src/basic/utils/time";

// Small display components used by both SessionList (compact rows) and
// SessionDetailPage (full detail). Split from sessionFormat.js because
// react-refresh/only-export-components requires a file to export either
// components or plain values, not a mix of both.

const parseTimestamp = parseStamp;

function formatElapsed(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const min = Math.floor((totalSeconds % 3600) / 60);
  const sec = totalSeconds % 60;
  const mm = String(min).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}h ${mm}m ${ss}s` : `${mm}m ${ss}s`;
}

// Live-ticking "how long has this been recording" (startTime: a stamp or
// epoch seconds, e.g. recording_start_at) -- e.g. for a compact
// session row's summary line when there's no timed_stop_at to count down
// to instead.
export function Elapsed({ startTime }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startDate = parseTimestamp(startTime);
    const tick = () => setElapsed(!startDate ? 0 : Math.max(0, Math.floor((Date.now() - startDate.getTime()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startTime]);
  return <span>{formatElapsed(elapsed)}</span>;
}

export function Countdown({ timedStopAt }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timedStopAt]);
  if (remaining <= 0) return <span>ending…</span>;
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  const s = remaining % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return <span>{h > 0 ? `${h}h ${mm}m ${ss}s` : `${mm}m ${ss}s`}</span>;
}

function copyToClipboard(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const el = document.createElement("textarea");
  el.value = text;
  el.style.cssText = "position:fixed;top:-9999px;left:-9999px";
  document.body.appendChild(el);
  el.select();
  document.execCommand("copy");
  document.body.removeChild(el);
}

export function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    copyToClipboard(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button type="button" className="session-share-notice__copy-btn" onClick={handleCopy}>
      {copied ? "Copied to clipboard!" : "Copy"}
    </button>
  );
}
