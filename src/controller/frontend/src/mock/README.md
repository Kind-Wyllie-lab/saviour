# Mock mode

Run the frontend with **no controller and no Pi**:

```bash
npm run dev:mock          # = vite --mode mock
```

`--mode mock` makes Vite load `.env.mock` (which sets `VITE_MOCK=1`) on top
of the normal env files. Using a mode + env file rather than an inline
`VITE_MOCK=1` prefix keeps the script working on Windows/PowerShell too.

`src/socket.jsx` swaps the real `socket.io-client` singleton for
`socketMock.js`, an in-memory fake. Every hook and component is unchanged —
they still `import socket from "/src/socket"`.

Pick a rig variant the usual way — on POSIX shells
`VITE_VARIANT=habitat npm run dev:mock`, or on any OS put `VITE_VARIANT=habitat`
in `.env.local` (gitignored).

## What works

- All the bootstrap reads for the main pages of every variant (modules,
  health, sessions, storage, controller config, first-run, update info …).
- Health values jitter every 3 s so charts move.
- A few mutations actually change state and re-render: `start_recording` /
  `stop_recording`, `check_ready`, `save_controller_config`, `remove_module`,
  `login` (any 4+ char password, or `dev`).
- Camera livestream tiles and the "take a picture" button show an SVG
  placeholder (served by `mockMediaPlugin` in `vite.config.js`) instead of
  broken `<img>`s.

## What doesn't

- It's not a real backend — no contract validation, no persistence across
  reload, no websocket transport quirks.
- Anything not in `ROUTER` (in `socketMock.js`) logs
  `[mock] unhandled: <event>` to the console and does nothing. That's the
  cue to add it.
- The crop editor still points at a real module IP for its still frame /
  `/roi` fetch — broken in mock mode.

## Extending it

1. Hit the feature you want, watch the console for `[mock] unhandled: X`.
2. Add `X` to `ROUTER` in `socketMock.js`; have it `this._fire(<responseEvent>,
   <data>)`.
3. Put any data it needs in `fixtures.js` (edit `state` / the `make*`
   builders). Edit fixtures freely to reproduce a state you're working on —
   a module in fault, a full disk, a session mid-recording.

## Habitat build: the simulated habitat

With `VITE_VARIANT=habitat` the mock runs on the habitat simulator
(`habitatSim/`, `plans/habitat-simulator.md`): 10 tagged rats moving
through the 16 boxes, driving every feed from one model.

- Modules: 16 `habitat_camera` (A1-D4), 4 microphone modules (A-D) with 4
  labelled AudioMoths each, the RFID gate bus, the DMX lights.
- Gate reads (`gate_map_hit`) are the rats' crossings; AudioMoth levels
  follow who's in each box and the calls.
- The dev server renders the camera streams (top-down view of each box with
  its rats) and the microphone spectrograms (`mock-server/habitatMedia.js`,
  hooked in by `vite.config.js`). Each stream comes from its own
  `<name>.localhost` host, because the browser keeps only 6 connections per
  host; open the app at `http://localhost:<port>` for that to work.
- The model is deterministic per sim day, so the browser and the dev server
  agree without talking. Tune it in `src/shared/habitat_sim.json` (rats,
  nests, light schedule, dwell times, call rates, which gates are
  unmapped / closed / faulty).
- `__mockSocket.sim`: where every rat is now, recent crossings.
- **Gates page: Simulated bus panel** (`MockBusPanel.jsx`, mock builds only).
  Plays the RFID bus by hand for a mapping run: click Read on the antenna
  you "put the probe on" (a second one within 1.5 s is a double read; one
  already in the map at another gate is caught as a duplicate). Pick the
  probe or an animal tag (a run should ignore it), unplug an antenna or
  swap its decoder (new serial) and scan, paste your own antenna list
  (`address, label, serial`, kept in localStorage; it starts an empty map),
  or clear the saved map. Untick "Answer prompts by hand" for the old
  automatic answers. `gateMapMock.js` follows `MappingRun` (gate_map.py).

## Console poking

`window.__mockSocket` in mock mode:

```js
__mockSocket.reset()                              // rebuild fixture state
__mockSocket.setModuleStatus("camera-module-a1b2", "OFFLINE")
__mockSocket.fire("sessions_update", { ... })     // push any event
__mockSocket.state                                // the live fixture object
```

Nothing here ships: `socketMock.js` / `fixtures.js` are only imported when
`VITE_MOCK=1`, and `mockMediaPlugin` is only added to the dev server then.
