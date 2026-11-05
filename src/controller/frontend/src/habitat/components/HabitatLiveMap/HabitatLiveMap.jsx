import { useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router";
import socket from "/src/socket";
import HabitatLivestreamCard from "../HabitatLivestreamCard/HabitatLivestreamCard";
import { BOX, gatePoint } from "../HabitatGrid/geometry";
import useAudioLevels, { isStale } from "../../hooks/useAudioLevels";
import "./HabitatLiveMap.css";

// The habitat dashboard's live view: every box's camera, every gate's RFID
// reads and every box's AudioMoth level, on the gate map's layout (A1 top
// left, letters across, numbers down).
//
//  - Cameras: the camera module named after the box (e.g. "B3").
//  - Gates: gate_map_hit from the controller (variants/habitat/
//    gate_map_service.py), which forwards every read while a dashboard
//    renews gate_map_live; a gate flashes on each read.
//  - Audio: audio_levels from the microphone modules (monitoring.levels_hz),
//    matched to a box by the AudioMoth's label (audiomoth_labels, e.g. "B3").

const LIVE_RENEW_MS = 20000;
const FLASH_MS = 600;
const UNMAPPED_WINDOW_MS = 10000;
const STREAM_REFRESH_MS = 60000;
const DB_FLOOR = -80;
const GATE_R = 6;

const CAMERA_EXCLUDED = new Set(["microphone", "rfid"]);

function useLiveGates() {
  const [state, setState] = useState(null);
  const [flashes, setFlashes] = useState({});      // gate id -> ms of last read
  const [unmapped, setUnmapped] = useState([]);    // ms of reads with no gate

  useEffect(() => {
    const onState = (s) => setState(s);
    const onHit = (h) => {
      const t = Date.now();
      if (h.gates?.length) {
        setFlashes((prev) => {
          const next = { ...prev };
          for (const g of h.gates) next[g] = t;
          return next;
        });
      } else {
        setUnmapped((prev) => [...prev.filter((x) => t - x < UNMAPPED_WINDOW_MS), t]);
      }
    };
    const renew = () => { socket.emit("get_gate_map"); socket.emit("gate_map_live"); };
    socket.on("gate_map_state", onState);
    socket.on("gate_map_hit", onHit);
    socket.on("connect", renew);
    renew();
    const live = setInterval(() => socket.emit("gate_map_live"), LIVE_RENEW_MS);
    // Drop finished flashes and old unmapped reads.
    const prune = setInterval(() => {
      const t = Date.now();
      setFlashes((prev) => {
        const keep = Object.entries(prev).filter(([, at]) => t - at < FLASH_MS);
        return keep.length === Object.keys(prev).length ? prev : Object.fromEntries(keep);
      });
      setUnmapped((prev) => {
        const keep = prev.filter((x) => t - x < UNMAPPED_WINDOW_MS);
        return keep.length === prev.length ? prev : keep;
      });
    }, 200);
    return () => {
      socket.off("gate_map_state", onState);
      socket.off("gate_map_hit", onHit);
      socket.off("connect", renew);
      clearInterval(live);
      clearInterval(prune);
    };
  }, []);

  return { state, flashes, unmappedCount: unmapped.length };
}

const dbToPct = (db) =>
  Math.max(0, Math.min(100, ((db - DB_FLOOR) / -DB_FLOOR) * 100));

function LevelMeter({ level }) {
  if (!level) return null;
  const stale = isStale(level);
  const cls = [
    "habitat-live-meter",
    stale && "habitat-live-meter--stale",
    level.clipping && "habitat-live-meter--clip",
  ].filter(Boolean).join(" ");
  const title = stale
    ? `${level.label}: no level for a while (${level.module_id})`
    : `${level.label}: ${level.level_db.toFixed(0)} dBFS RMS, peak ${level.hold_db.toFixed(0)} dBFS`
      + (level.clipping ? `, clipping ${level.clip_pct.toFixed(1)}%` : "");
  return (
    <div className={cls} title={title}>
      <div className="habitat-live-meter-fill" style={{ height: `${stale ? 0 : dbToPct(level.level_db)}%` }} />
      {!stale && (
        <div className="habitat-live-meter-peak" style={{ bottom: `${dbToPct(level.hold_db)}%` }} />
      )}
    </div>
  );
}

function BoxCell({ id, mods, level, refreshKey }) {
  let body;
  if (mods && mods.length > 1) {
    body = (
      <div className="habitat-live-dup">
        <p>{mods.length} modules named {id}</p>
        <p>Fix in <NavLink to="/settings">Settings</NavLink></p>
      </div>
    );
  } else if (mods) {
    body = <HabitatLivestreamCard key={`${id}-${refreshKey}`} module={mods[0]} />;
  } else {
    body = <div className="habitat-live-empty" />;
  }
  return (
    <>
      {body}
      <span className="habitat-live-box-label">{id}</span>
      <LevelMeter level={level} />
    </>
  );
}

function HabitatLiveMap({ modules, onPlacement, placing }) {
  const { state, flashes, unmappedCount } = useLiveGates();
  const rawLevels = useAudioLevels();
  // Box id -> the AudioMoth labelled with it (the newest, if two are).
  const levels = useMemo(() => {
    const out = {};
    for (const l of Object.values(rawLevels)) {
      const key = l.label || l.serial;
      if (!out[key] || out[key].at < l.at) out[key] = l;
    }
    return out;
  }, [rawLevels]);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setRefreshKey((k) => k + 1), STREAM_REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const camerasByName = useMemo(() => Object.values(modules ?? {}).reduce((acc, m) => {
    if (CAMERA_EXCLUDED.has(m.type)) return acc;
    (acc[m.name] ??= []).push(m);
    return acc;
  }, {}), [modules]);

  const layout = state?.layout;
  if (!layout) return <div className="habitat-live-loading">Loading habitat layout…</div>;

  const entries = state.map?.entries ?? {};
  const box = Object.fromEntries(layout.boxes.map((b) => [b.id, b]));
  const walls = Object.fromEntries(layout.walls.map((w) => [w.id, w]));
  const perWall = layout.gates_per_wall || 4;
  const boxIds = new Set(layout.boxes.map((b) => b.id));
  const unlabelled = Object.values(levels).filter((l) => !boxIds.has(l.label));
  const mappedGates = Object.values(entries).filter((e) => e.status === "mapped").length;

  return (
    <div className="habitat-live">
      <div
        className="habitat-live-map"
        style={{ "--ratio": layout.cols / layout.rows }}
      >
        {layout.boxes.map((b) => (
          <div
            key={b.id}
            className="habitat-live-box"
            style={{
              left: `${(b.col * 100) / layout.cols}%`,
              top: `${(b.row * 100) / layout.rows}%`,
              width: `${100 / layout.cols}%`,
              height: `${100 / layout.rows}%`,
            }}
          >
            <div className="habitat-live-box-inner">
              <BoxCell id={b.id} mods={camerasByName[b.id]} level={levels[b.id]} refreshKey={refreshKey} />
            </div>
          </div>
        ))}

        <svg
          className="habitat-live-gates"
          viewBox={`0 0 ${layout.cols * BOX} ${layout.rows * BOX}`}
          aria-label="Gate reads"
        >
          {layout.gates.filter((g) => g.fitted).map((g) => {
            const p = gatePoint(walls[g.wall], box, g.index, perWall);
            const entry = entries[g.id];
            const tone = entry?.status ?? "unmapped";
            const on = flashes[g.id] !== undefined;
            return (
              <g key={g.id} className={`habitat-live-gate habitat-live-gate--${tone}${on ? " habitat-live-gate--on" : ""}`}>
                {on && <circle className="habitat-live-gate-halo" cx={p.x} cy={p.y} r={GATE_R * 2.2} />}
                <circle className="habitat-live-gate-dot" cx={p.x} cy={p.y} r={GATE_R} />
                <title>
                  {entry?.status === "mapped"
                    ? `${g.id}: ${entry.module_id} unit ${entry.unit}`
                    : `${g.id}: ${entry?.status ?? "not mapped"}`}
                </title>
              </g>
            );
          })}
        </svg>

        <button
          className="habitat-live-refresh"
          title="Reconnect all camera streams"
          onClick={() => setRefreshKey((k) => k + 1)}
        >
          ↺
        </button>
      </div>

      <div className="habitat-live-footer">
        <span>
          RFID: {state.rfid_modules?.length ? `live (${mappedGates} gates mapped)` : "no RFID module online"}
          {unmappedCount > 0 && (
            <> · <NavLink to="/gates">{unmappedCount} read{unmappedCount === 1 ? "" : "s"} from unmapped units</NavLink></>
          )}
        </span>
        {unlabelled.length > 0 && (
          <span title={unlabelled.map((l) => `${l.serial} on ${l.module_id}`).join("\n")}>
            {unlabelled.length} AudioMoth{unlabelled.length === 1 ? "" : "s"} without a box label
          </span>
        )}
        {onPlacement && (
          <button type="button" className="habitat-live-link" onClick={onPlacement}>
            {placing ? "Close placement" : "Placement…"}
          </button>
        )}
      </div>
    </div>
  );
}

export default HabitatLiveMap;
