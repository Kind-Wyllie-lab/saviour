import { useEffect, useState } from "react";
import socket from "/src/socket";

// Live AudioMoth levels from the microphone modules' audio_levels status
// (monitoring.levels_hz). Keyed "<module_id>:<serial>"; each entry:
// {module_id, serial, label, level_db, peak_db, hold_db, hold_at, clip_pct,
//  clipping, at}. hold_db is the loudest peak over the last PEAK_HOLD_MS.

export const PEAK_HOLD_MS = 2000;
export const LEVEL_STALE_MS = 3000;

export const isStale = (level) => !level || Date.now() - level.at > LEVEL_STALE_MS;

export default function useAudioLevels() {
  const [levels, setLevels] = useState({});
  const [, setTick] = useState(0);

  useEffect(() => {
    const onLevels = ({ module_id, levels: list }) => {
      const t = Date.now();
      setLevels((prev) => {
        const next = { ...prev };
        for (const l of list ?? []) {
          const key = `${module_id}:${l.serial}`;
          const old = prev[key];
          const holdLive = old && t - old.hold_at < PEAK_HOLD_MS && old.hold_db > l.peak_db;
          next[key] = {
            ...l, module_id, at: t,
            hold_db: holdLive ? old.hold_db : l.peak_db,
            hold_at: holdLive ? old.hold_at : t,
          };
        }
        return next;
      });
    };
    socket.on("audio_levels", onLevels);
    // Re-render now and then so a meter whose module went quiet greys out.
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => { socket.off("audio_levels", onLevels); clearInterval(t); };
  }, []);

  return levels;
}
