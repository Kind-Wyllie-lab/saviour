import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import "./SoundPlayWidget.css";

// Dashboard widget for one sound module: pick a sound (and an output on a
// multi-output card), set its volume and length, and play it now. A
// recording plays up to its own length; a built sound is rendered at any
// length up to the generator's maximum (the module reports which are). Same
// play_sound command and `sound_test_result` reply as the sound card's
// output Test buttons; refused while recording (it would land in the session).

const TIMEOUT_MS = 15000;

const fmtLen = (s) => (typeof s === "number" ? (s < 10 ? `${s.toFixed(2)} s` : `${Math.round(s)} s`) : "length unknown");

export default function SoundPlayWidget({ module }) {
  const loggedIn = useIsLoggedIn();
  const hw = module?.config?.sound_hardware ?? {};
  const files = hw.files ?? {};
  const names = Object.keys(files).sort();
  const outputs = Number(hw.outputs) || 0;
  const [file, setFile] = useState("");
  const [output, setOutput] = useState(1);
  const moduleVolume = Number(module?.config?.sound?.volume ?? 1);
  const [volume, setVolume] = useState(null);      // null = the module's
  const [length, setLength] = useState("");        // "" = whole file / 1 s copy
  const [state, setState] = useState(null); // { kind: "playing"|"ok"|"error", message }
  const timer = useRef(null);

  useEffect(() => {
    if (names.length && !names.includes(file)) setFile(names[0]);
  }, [names.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onResult = (msg) => {
      if (msg?.module_id !== module?.id) return;
      clearTimeout(timer.current);
      setState((s) => s && (msg.result === "success"
        ? { kind: "ok" }
        : { kind: "error", message: msg.message || String(msg.result || "failed") }));
    };
    socket.on("sound_test_result", onResult);
    return () => { socket.off("sound_test_result", onResult); clearTimeout(timer.current); };
  }, [module?.id]);

  if (!module) return null;
  const recipes = Array.isArray(hw.recipes) ? hw.recipes : [];
  const recipeMax = Number(hw.recipe_max_s) || 300;
  const stretch = recipes.includes(file);
  const fileLen = typeof files[file] === "number" ? files[file] : null;
  const maxLen = stretch ? recipeMax : fileLen;
  const vol = volume ?? Math.min(1, Math.max(0, moduleVolume));
  const lenNum = length === "" ? null : Number(length);
  const lenBad = lenNum !== null && (!(lenNum > 0) || (maxLen !== null && lenNum > maxLen + 1e-9));
  const recording = module.status === "RECORDING";
  const blocked = !loggedIn ? "Sign in as admin to play a sound"
    : module.online === false ? "The module is offline"
    : recording ? "Not while recording: the sound would land in the session"
    : hw.present === false ? (hw.detail || "No sound card detected")
    : !file ? "No sound files on the module"
    : lenBad ? (lenNum > 0 ? `Longest ${maxLen} s for this sound` : "Length must be above 0")
    : null;
  const playing = state?.kind === "playing";

  const play = () => {
    const params = { filename: file, output, volume: vol };
    if (lenNum !== null) params.duration_s = lenNum;
    else if (fileLen) params.duration_s = fileLen;   // the whole file
    setState({ kind: "playing" });
    socket.emit("send_command", { module_id: module.id, type: "play_sound", params });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState((s) => (s?.kind === "playing"
      ? { kind: "error", message: "No reply from the module" } : s)), TIMEOUT_MS);
  };

  return (
    <div className="spw card">
      <div className="spw-header">
        <h2>Sound</h2>
        <Link to={`/settings#${module.id}`} className="spw-link">Settings →</Link>
      </div>
      <div className="spw-module">
        <span className={`status-icon ${String(module.status ?? "").toLowerCase()}`} aria-hidden="true" />
        <span className="spw-module-name">{module.name || module.id}</span>
        {hw.board && <span className="spw-board">{hw.board}</span>}
      </div>

      <label className="spw-field">
        <span className="spw-label">Sound</span>
        <select value={file} onChange={(e) => setFile(e.target.value)} disabled={!names.length}>
          {!names.length && <option value="">No sound files</option>}
          {names.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <div className="spw-field">
        <span className="spw-label">Volume</span>
        <div className="spw-slider">
          <input type="range" min="0" max="1" step="0.05" value={vol}
            onChange={(e) => setVolume(Number(e.target.value))} aria-label="Volume" />
          <output className="spw-slider-value">
            {vol.toFixed(2)}<span className="spw-db"> {vol > 0 ? `${(20 * Math.log10(vol)).toFixed(1)} dB` : "−∞ dB"}</span>
          </output>
        </div>
        {volume !== null && Math.abs(volume - moduleVolume) > 1e-9 && (
          <button type="button" className="spw-reset" onClick={() => setVolume(null)}>
            Back to the module's {moduleVolume}
          </button>
        )}
      </div>
      <label className="spw-field">
        <span className="spw-label">Length (s)</span>
        <div className="spw-length">
          <input type="number" min="0.01" step="0.1" max={maxLen ?? undefined} value={length}
            placeholder={stretch ? "1 (library copy)" : fileLen ? `${fileLen.toFixed(fileLen < 10 ? 2 : 0)} (whole)` : "whole"}
            className={lenBad ? "spw-bad" : undefined}
            onChange={(e) => setLength(e.target.value)} />
          <span className="spw-max">
            {stretch ? `built sound: any length up to ${recipeMax} s`
              : fileLen !== null ? `recording: max ${fileLen.toFixed(fileLen < 10 ? 2 : 1)} s` : "length unknown"}
          </span>
        </div>
      </label>
      <div className="spw-controls">
        {outputs > 1 ? (
          <label className="spw-field spw-field--out">
            <span className="spw-label">Output</span>
            <select value={output} onChange={(e) => setOutput(Number(e.target.value))}
              title="Stereo output: 1 = channels 1-2, 2 = 3-4, …">
              {Array.from({ length: outputs }, (_, i) => i + 1).map((o) => (
                <option key={o} value={o}>{o} · ch {2 * o - 1}-{2 * o}</option>
              ))}
            </select>
          </label>
        ) : <span />}
        <span className="spw-meta" />
        <button type="button" className={`spw-play${playing ? " spw-play--busy" : ""}`} onClick={play}
          disabled={!!blocked || playing} title={blocked ?? `Play ${file} now`}>
          {playing ? "Playing…" : "▶ Play"}
        </button>
      </div>
      <div className={`spw-status${state?.kind === "error" ? " spw-status--bad" : state?.kind === "ok" ? " spw-status--ok" : ""}`}
        aria-live="polite">
        {state?.kind === "error" ? state.message
          : state?.kind === "ok" ? "✓ Played"
          : blocked ?? (lenNum !== null ? `Plays ${lenNum} s at volume ${vol.toFixed(2)}.`
            : stretch ? `Plays the 1 s library copy at volume ${vol.toFixed(2)}.`
            : `Plays the whole file (${fmtLen(fileLen)}) at volume ${vol.toFixed(2)}.`)}
      </div>
    </div>
  );
}
