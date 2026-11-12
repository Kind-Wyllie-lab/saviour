// The browser mock's habitat simulator: one world (model.js) built from the
// shared parameters, advanced to "now" by whoever reads it. The dev server
// builds its own from the same parameters for the camera and microphone
// streams (mock-server/habitatMedia.js); both come out the same.

import params from "@saviour-shared/habitat_sim.json";
import { createWorld, ratPosition, callAt, CALL_SLOT_S } from "./model";

export { params };

let world = null;

export function simWorld() {
  world ??= createWorld(params);
  return world.advance(Date.now() / 1000);
}

// For the console (__mockSocket.sim): where every rat is now.
export function simSnapshot() {
  const w = simWorld();
  const t = Date.now() / 1000;
  return {
    light: +w.light(t).toFixed(2),
    occupancy: w.occupancy(t),
    rats: w.rats.map((r) => {
      const p = ratPosition(w, r.index, t);
      return { tag: r.tag, nest: r.nest, box: p.box, resting: p.resting };
    }),
    crossings_today: w.day.crossings.length,
    last_crossings: w.day.crossings.filter((c) => c.t <= t).slice(-10)
      .map((c) => ({ at: new Date(c.t * 1000).toLocaleTimeString(), tag: c.tag, gate: c.gate, from: c.from, to: c.to })),
  };
}

// Was there a call in `box` within the last `windowS` seconds?
export function recentCall(w, box, t, occ, windowS) {
  for (let x = t; x > t - windowS; x -= CALL_SLOT_S) {
    const c = callAt(w, box, x, occ);
    if (c) return c;
  }
  return null;
}
