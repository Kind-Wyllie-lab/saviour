import { useEffect, useRef, useState } from "react";
import { loadSpotifyApi } from "./spotifyEmbed";

/**
 * Spotify's compact embedded player for `uri`, created the first time
 * `playing` turns true (the first jump) and driven from there: `muted`
 * pauses it, unmuting resumes. The player stays visible, so it can also be
 * used directly (e.g. if the browser blocked playback without a click on
 * it). Torn down when the game closes.
 */
export default function GameMusic({ uri, playing, muted }) {
  const hostRef = useRef(null);
  const controllerRef = useRef(null);
  const startedRef = useRef(false);
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error

  // Create the player on first start.
  useEffect(() => {
    if (!playing || status !== "idle" || !uri) return undefined;
    let cancelled = false;
    setStatus("loading");
    loadSpotifyApi()
      .then((api) => {
        if (cancelled || !hostRef.current) return;
        // createController replaces the element it's given, so hand it a
        // child we own rather than the React-managed container.
        const el = document.createElement("div");
        hostRef.current.appendChild(el);
        api.createController(el, { uri, width: "100%", height: 80 }, (controller) => {
          if (cancelled) { controller.destroy(); return; }
          controllerRef.current = controller;
          controller.addListener("ready", () => setStatus("ready"));
        });
      })
      .catch(() => { if (!cancelled) setStatus("error"); });
    return () => { cancelled = true; };
  }, [playing, status, uri]);

  // Play / pause as the game and mute button say.
  useEffect(() => {
    const c = controllerRef.current;
    if (status !== "ready" || !c) return;
    if (playing && !muted) {
      if (startedRef.current) c.resume();
      else { c.play(); startedRef.current = true; }
    } else if (startedRef.current) {
      c.pause();
    }
  }, [status, playing, muted]);

  useEffect(() => () => {
    controllerRef.current?.destroy();
    controllerRef.current = null;
  }, []);

  if (!uri) return null;
  return (
    <div className={`game-music${status === "idle" ? " is-idle" : ""}`}>
      <div ref={hostRef} className="game-music__player" />
      {status === "error" && (
        <p className="runner-game__hint">No music: this browser can&apos;t reach Spotify.</p>
      )}
    </div>
  );
}
