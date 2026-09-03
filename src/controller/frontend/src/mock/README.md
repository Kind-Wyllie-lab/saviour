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
