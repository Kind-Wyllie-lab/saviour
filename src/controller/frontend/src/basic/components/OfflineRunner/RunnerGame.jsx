import { useEffect, useRef, useState } from "react";
import GameMusic from "./GameMusic";
import { DEFAULT_GAME_MUSIC, spotifyUri } from "./spotifyEmbed";

// A small endless runner: a rat runs along the bench and jumps PoE cables
// and switches. Space / ↑ / tap to jump (hold for a higher jump), Esc
// closes. Drawn on a canvas in the active theme's colours; no assets, no
// network. Best score is per browser (localStorage).

const W = 640;
const H = 170;
const GROUND = 140;
const GRAVITY = 2600; // px/s²
const JUMP_V = -580; // px/s: about 65 px tap, 108 px held
const HOLD_GRAVITY = 0.6; // gravity multiplier while jump is held and rising
const START_SPEED = 260; // px/s
const MAX_SPEED = 720;
const BEST_KEY = "saviour.runnerBest";
const MUTE_KEY = "saviour.runnerMuted";
const MUSIC_URI = spotifyUri(DEFAULT_GAME_MUSIC);

function loadMuted() {
  try { return window.localStorage.getItem(MUTE_KEY) === "1"; } catch { return false; }
}
function storeMuted(v) {
  try { window.localStorage.setItem(MUTE_KEY, v ? "1" : "0"); } catch { /* storage blocked */ }
}

function loadBest() {
  try { return Number(window.localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function storeBest(v) {
  try { window.localStorage.setItem(BEST_KEY, String(v)); } catch { /* storage blocked */ }
}

function themeColours() {
  const cs = getComputedStyle(document.body);
  const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  return {
    bg: v("--card-bg-color", "#1e1e1e"),
    ink: v("--text-color", "#eee"),
    accent: v("--accent-color", "#4caf50"),
    alt: v("--accent-color-alt", "#ff9800"),
    line: v("--border-color", "#555"),
  };
}

// Obstacles: a coiled cable (low, wide), a PoE switch (tall), a double coil.
const KINDS = [
  { kind: "coil", w: 34, h: 18 },
  { kind: "switch", w: 26, h: 36 },
  { kind: "coil2", w: 64, h: 18 },
];

function newState() {
  return {
    t: 0,
    y: GROUND, // rat's feet
    vy: 0,
    onGround: true,
    speed: START_SPEED,
    dist: 0,
    obstacles: [],
    nextGap: 500,
    dust: [],
    over: false,
  };
}

function drawRat(ctx, c, x, y, t, onGround) {
  ctx.save();
  ctx.translate(x, y);
  // tail
  ctx.strokeStyle = c.ink;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-14, -9);
  ctx.quadraticCurveTo(-30, -6 + Math.sin(t * 12) * 4, -40, -14);
  ctx.stroke();
  // legs (scurry when on the ground)
  const step = onGround ? Math.sin(t * 30) * 3 : 2;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(-8, -4); ctx.lineTo(-8 - step, 0);
  ctx.moveTo(8, -4); ctx.lineTo(8 + step, 0);
  ctx.stroke();
  // body
  ctx.fillStyle = c.ink;
  ctx.beginPath();
  ctx.ellipse(0, -11, 17, 9, 0, 0, Math.PI * 2);
  ctx.fill();
  // head + snout
  ctx.beginPath();
  ctx.ellipse(15, -14, 9, 7, -0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(20, -17); ctx.lineTo(29, -12); ctx.lineTo(20, -9);
  ctx.fill();
  // ear
  ctx.fillStyle = c.alt;
  ctx.beginPath();
  ctx.arc(12, -21, 4, 0, Math.PI * 2);
  ctx.fill();
  // eye
  ctx.fillStyle = c.bg;
  ctx.beginPath();
  ctx.arc(19, -15, 1.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawObstacle(ctx, c, o, t) {
  ctx.save();
  ctx.translate(o.x, GROUND);
  if (o.kind === "switch") {
    ctx.fillStyle = c.line;
    ctx.fillRect(0, -o.h, o.w, o.h);
    ctx.strokeStyle = c.ink;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(0, -o.h, o.w, o.h);
    // port LEDs, blinking
    for (let i = 0; i < 3; i++) {
      ctx.fillStyle = Math.sin(t * 8 + i * 2 + o.x * 0.05) > 0 ? c.accent : c.bg;
      ctx.fillRect(5 + i * 6, -o.h + 6, 3, 3);
    }
  } else {
    // cable coil(s): loops drawn as arcs
    ctx.strokeStyle = c.accent;
    ctx.lineWidth = 3;
    const loops = o.kind === "coil2" ? 4 : 2;
    const r = o.w / loops / 2 + 2;
    for (let i = 0; i < loops; i++) {
      ctx.beginPath();
      ctx.ellipse(r + i * (o.w - 2 * r) / Math.max(1, loops - 1), -o.h / 2, r, o.h / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.restore();
}

export default function RunnerGame({ onClose }) {
  const canvasRef = useRef(null);
  const state = useRef(newState());
  const held = useRef(false);
  const [best, setBest] = useState(loadBest);
  const [score, setScore] = useState(0);
  const [phase, setPhase] = useState("ready"); // ready | running | over
  // Music starts with the first jump (Spotify's embed, see GameMusic) and
  // plays on between runs; a hidden tab (phase back to "ready") pauses it.
  const [muted, setMuted] = useState(loadMuted);
  const musicPlaying = phase !== "ready";
  const toggleMute = () => setMuted((m) => { storeMuted(!m); return !m; });
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.scale(dpr, dpr);

    let colours = themeColours();
    let raf = 0;
    let last = performance.now();
    let shownScore = 0;

    const frame = (now) => {
      const dt = Math.min(0.033, (now - last) / 1000);
      last = now;
      const s = state.current;
      const running = phaseRef.current === "running";

      if (running) {
        s.t += dt;
        s.speed = Math.min(MAX_SPEED, s.speed + 9 * dt);
        s.dist += s.speed * dt;

        // rat physics; holding jump while rising floats a little higher
        const g = held.current && s.vy < 0 ? GRAVITY * HOLD_GRAVITY : GRAVITY;
        s.vy += g * dt;
        s.y += s.vy * dt;
        if (s.y >= GROUND) {
          if (!s.onGround) {
            for (let i = 0; i < 5; i++) {
              s.dust.push({ x: 90 + Math.random() * 20, y: GROUND, vx: -40 - Math.random() * 60,
                vy: -30 - Math.random() * 40, life: 0.35 });
            }
          }
          s.y = GROUND; s.vy = 0; s.onGround = true;
        }

        // obstacles
        s.nextGap -= s.speed * dt;
        if (s.nextGap <= 0) {
          const k = KINDS[Math.floor(Math.random() * (s.dist > 1500 ? 3 : 2))];
          s.obstacles.push({ ...k, x: W + 10 });
          // Gaps shrink with speed but always leave room for a jump.
          const minGap = s.speed * 0.9 + 60;
          s.nextGap = minGap + Math.random() * 380;
        }
        for (const o of s.obstacles) o.x -= s.speed * dt;
        s.obstacles = s.obstacles.filter((o) => o.x + o.w > -20);

        // collision: forgiving box around the rat's body
        const rx0 = 100 - 15;
        const rx1 = 100 + 24;
        const ry0 = s.y - 18;
        for (const o of s.obstacles) {
          if (rx1 > o.x + 4 && rx0 < o.x + o.w - 4 && s.y > GROUND - o.h + 4 && ry0 < GROUND) {
            s.over = true;
          }
        }
        if (s.over) {
          const final = Math.floor(s.dist / 10);
          setScore(final);
          setBest((b) => {
            if (final > b) { storeBest(final); return final; }
            return b;
          });
          setPhase("over");
        }

        const sc = Math.floor(s.dist / 10);
        if (sc !== shownScore) { shownScore = sc; setScore(sc); }
      }
      for (const d of s.dust) {
        d.x += d.vx * dt; d.y += d.vy * dt; d.vy += 300 * dt; d.life -= dt;
      }
      s.dust = s.dust.filter((d) => d.life > 0);

      // draw
      ctx.fillStyle = colours.bg;
      ctx.fillRect(0, 0, W, H);
      // bench edge + scrolling grain
      ctx.strokeStyle = colours.line;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(0, GROUND + 1); ctx.lineTo(W, GROUND + 1); ctx.stroke();
      ctx.lineWidth = 1;
      const off = s.dist % 40;
      for (let x = -off; x < W; x += 40) {
        ctx.beginPath(); ctx.moveTo(x, GROUND + 10); ctx.lineTo(x + 12, GROUND + 10); ctx.stroke();
      }
      for (const o of s.obstacles) drawObstacle(ctx, colours, o, s.t);
      ctx.fillStyle = colours.line;
      for (const d of s.dust) ctx.fillRect(d.x, d.y, 2, 2);
      drawRat(ctx, colours, 100, s.y, s.t, s.onGround);

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    // Re-read colours if the theme changes mid-game.
    const mo = new MutationObserver(() => { colours = themeColours(); });
    mo.observe(document.head, { childList: true, subtree: true, characterData: true });
    mo.observe(document.body, { attributes: true, attributeFilter: ["class"] });

    return () => { cancelAnimationFrame(raf); mo.disconnect(); };
  }, []);

  const jump = () => {
    if (phaseRef.current === "ready" || phaseRef.current === "over") {
      state.current = newState();
      setScore(0);
      setPhase("running");
    }
    const s = state.current;
    if (s.onGround) { s.vy = JUMP_V; s.onGround = false; }
  };

  useEffect(() => {
    const down = (e) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key === "m" || e.key === "M") { toggleMute(); return; }
      if (e.code === "Space" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!e.repeat) jump();
        held.current = true;
      }
    };
    const up = (e) => {
      if (e.code === "Space" || e.key === "ArrowUp") held.current = false;
    };
    // Pause when the tab is hidden: drop back to "ready" rather than dying.
    const vis = () => { if (document.hidden && phaseRef.current === "running") setPhase("ready"); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    document.addEventListener("visibilitychange", vis);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      document.removeEventListener("visibilitychange", vis);
    };
  }, [onClose]);

  return (
    <div className="runner-game">
      <div className="runner-game__bar">
        <span>Score <b>{score}</b></span>
        <span>Best <b>{best}</b></span>
        {MUSIC_URI && (
          <button
            type="button"
            className={`runner-game__mute${muted ? " is-muted" : ""}`}
            aria-pressed={muted}
            title={muted ? "Unmute music (M)" : "Mute music (M)"}
            onClick={toggleMute}
          >
            {muted ? "♪ off" : "♪ on"}
          </button>
        )}
      </div>
      <canvas
        ref={canvasRef}
        className="runner-game__canvas"
        style={{ aspectRatio: `${W} / ${H}` }}
        onPointerDown={(e) => { e.preventDefault(); held.current = true; jump(); }}
        onPointerUp={() => { held.current = false; }}
        onPointerLeave={() => { held.current = false; }}
      />
      <p className="runner-game__hint">
        {phase === "ready" && "Space, ↑ or tap to start. Hold to jump higher."}
        {phase === "running" && "Jump the cables and switches."}
        {phase === "over" && `Tangled at ${score}. Space or tap to go again.`}
      </p>
      <GameMusic uri={MUSIC_URI} playing={musicPlaying} muted={muted} />
    </div>
  );
}
