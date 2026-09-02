import React, { useState, useEffect, useRef } from "react";
import FullscreenVideo from "../FullscreenVideo/FullscreenVideo";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";
import "./MJPEGStreamCard.css";

const STALL_TIMEOUT_MS = 8000;
const RECONNECT_DELAY_MS = 2500;
// Floor between actual reconnects, regardless of how many things ask for one.
// Each bump() fully unmounts/remounts the <img>, opening a fresh long-lived
// MJPEG connection — a burst of triggers (e.g. several rapid config saves,
// each restarting the camera and separately tripping the stall/error/config-
// sync handlers) can otherwise open more concurrent connections to the same
// host than the browser allows, leaving the newest one stuck pending forever
// with neither onLoad nor onError ever firing to recover it.
const MIN_RECONNECT_MS = 3000;
// After this many reconnects with no good frame in between, stop retrying and
// show an idle state. A genuinely-dead stream (module offline, wrong port)
// otherwise reconnects forever, and every attempt leaks a socket Chrome is
// slow to reap — a whole dashboard of those is what wedges the tab until it's
// closed. The header ⟳ and re-focusing/scrolling the card back into view all
// reset this.
const MAX_RECONNECTS = 10;

/**
 * Generic MJPEG stream card.
 * Handles stall detection and reconnection for any module that serves
 * an MJPEG stream at http://{ip}:{port}/video_feed.
 *
 * The stream only runs while the card is BOTH on a visible tab and scrolled
 * into view — a backgrounded or off-screen card tears its connection down
 * instead of decoding frames and leaking sockets forever.
 */
function MJPEGStreamCard({ ip, port = 8080, label, isRecording = false, onAspectRatio, syncStatus }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [streamKey, setStreamKey] = useState(Date.now());
  // Actual aspect ratio of the stream, discovered from the first loaded
  // frame — streams aren't all 16:9 (e.g. square camera crops), and a
  // hardcoded ratio leaves either the card or the image letterboxed with
  // dead space. Re-detected on every reconnect in case a config change
  // (e.g. livestream_quality, resolution) altered it.
  const [aspectRatio, setAspectRatio] = useState(null);
  const [restarting, setRestarting] = useState(false);

  // Stream runs only when active === (tab visible) && (card on screen) &&
  // (reconnects not exhausted).
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible"
  );
  const [onScreen, setOnScreen] = useState(true);
  const [gaveUp, setGaveUp] = useState(false);
  const active = pageVisible && onScreen && !gaveUp;

  const stallTimer = useRef(null);
  const reconnectTimer = useRef(null);
  const prevStatus       = useRef(syncStatus);
  const lastBumpAt       = useRef(0);
  const pendingBumpTimer = useRef(null);
  const reconnectCount   = useRef(0);
  const imgRef           = useRef(null);
  const cardRef          = useRef(null);

  const clearTimers = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    clearTimeout(pendingBumpTimer.current);
  };

  // Changing `key` unmounts the old <img>, but Chrome doesn't reliably abort
  // the underlying multipart/x-mixed-replace connection just because the
  // element left the DOM — it can keep the socket open until GC gets to it.
  // Repeated reconnects can then pile up dangling connections against the
  // browser's per-host limit, so the *next* reconnect never gets a socket at
  // all — stuck with neither onLoad nor onError to recover it, exactly the
  // failure a manual refresh "fixes" by tearing down every connection to the
  // origin at once. Clearing src synchronously forces an immediate abort
  // before the remount.
  const doBump = () => {
    if (imgRef.current) imgRef.current.src = "";
    lastBumpAt.current = Date.now();
    setStreamKey(Date.now());
  };

  const bump = () => {
    if (!active) return;
    if (reconnectCount.current >= MAX_RECONNECTS) {
      setGaveUp(true);
      return;
    }
    reconnectCount.current += 1;
    const now = Date.now();
    const elapsed = now - lastBumpAt.current;
    if (elapsed >= MIN_RECONNECT_MS) {
      doBump();
    } else {
      clearTimeout(pendingBumpTimer.current);
      pendingBumpTimer.current = setTimeout(doBump, MIN_RECONNECT_MS - elapsed);
    }
  };

  // Manual retry (header ⟳): always allowed, clears the give-up state.
  const manualRetry = () => {
    reconnectCount.current = 0;
    if (gaveUp) {
      setGaveUp(false);   // re-activates -> the `active` effect remounts it
    } else {
      bump();
    }
  };

  // Tab visibility -> just track it; the `active` effect does the work.
  // `online` (network came back) forces a reconnect if we're currently active.
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const onVis = () => setPageVisible(document.visibilityState === "visible");
    const onOnline = () => bump();
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("online", onOnline);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Only stream while the card is actually on screen.
  useEffect(() => {
    const el = cardRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const obs = new IntersectionObserver(
      ([entry]) => setOnScreen(entry.isIntersecting),
      { rootMargin: "200px" }  // warm up just before it scrolls in
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Start / tear down the stream as `active` flips.
  useEffect(() => {
    if (!active) {
      if (imgRef.current) imgRef.current.src = "";
      clearTimers();
      return;
    }
    reconnectCount.current = 0;
    doBump();  // abort any stale connection + fresh cache-busted URL
  }, [active]);

  // A config save (e.g. resolution/fps change) restarts the camera's stream
  // server-side. Without watching this, the <img> connection just keeps
  // showing its last frame from before the restart — the config page's own
  // preview (LivestreamCard.jsx) already does this; this card serves the
  // same streams on the Dashboard and needs the same reconnect.
  useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = syncStatus;
    if (syncStatus === "PENDING") {
      setRestarting(true);
    } else if (prev === "PENDING" && syncStatus === "SYNCED") {
      bump();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncStatus]);

  useEffect(() => {
    if (!active) return undefined;
    setAspectRatio(null);
    stallTimer.current = setTimeout(bump, STALL_TIMEOUT_MS);
    return () => clearTimers();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey, active]);

  // Clear only, don't re-arm — Chrome fires onLoad once for a
  // multipart/x-mixed-replace (MJPEG) stream, on the first frame only, and
  // never again for subsequent frames. Rescheduling here would fire the
  // timer forever every STALL_TIMEOUT_MS regardless of stream health,
  // forcing a full reconnect (and killing the live video) on a healthy
  // stream. Recovery from a stream that actually dies is handled by onError.
  const handleLoad = (e) => {
    clearTimeout(stallTimer.current);
    reconnectCount.current = 0;  // a good frame == healthy, reset the budget
    setRestarting(false);
    const { naturalWidth, naturalHeight } = e.target;
    if (naturalWidth && naturalHeight) {
      const ratio = naturalWidth / naturalHeight;
      setAspectRatio(ratio);
      onAspectRatio?.(ratio);
    }
  };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_DELAY_MS);
  };

  const idleText = gaveUp
    ? "Stream unavailable"
    : !pageVisible
      ? "Stream paused"
      : "Stream paused (off screen)";

  return (
    <>
      <div className="mjpeg-stream-card card" ref={cardRef}>
        <div className="mjpeg-stream-header">
          {label && <span className="mjpeg-stream-label">{label}</span>}
          <button
            type="button"
            className="mjpeg-restart-button"
            onClick={(e) => { e.stopPropagation(); manualRetry(); }}
            title="Restart stream"
            aria-label="Restart stream"
          >
            ⟳
          </button>
        </div>
        <div
          className="mjpeg-stream-video"
          style={aspectRatio ? { "--stream-ratio": aspectRatio } : undefined}
        >
          {/* `?t=${streamKey}` cache-busts the request — without a query
              param that changes on every bump(), a remounted <img> can
              still resolve against the browser's in-flight/cached response
              for the identical bare URL instead of opening a genuinely new
              multipart connection, leaving the frame frozen even after a
              "successful" reconnect. */}
          {active && (
            <img
              key={streamKey}
              ref={imgRef}
              src={videoFeedUrl(ip, { port, key: streamKey })}
              alt={label || "stream"}
              onLoad={handleLoad}
              onError={handleError}
              onClick={() => setFullscreen(true)}
            />
          )}
          {isRecording && active && <span className="mjpeg-rec-dot" title="Recording" />}
          {restarting && active && (
            <div className="mjpeg-stream-restarting-overlay">
              <span>Stream restarting…</span>
            </div>
          )}
          {!active && (
            <div className="mjpeg-stream-restarting-overlay">
              <span>{idleText}</span>
            </div>
          )}
        </div>
      </div>

      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

export default MJPEGStreamCard;
