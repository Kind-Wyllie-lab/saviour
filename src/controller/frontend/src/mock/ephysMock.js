// Post-Process "Ephys" panel in mock mode: an uploaded Open Ephys dataset
// with two recordings (a bench test, then the real session), alignment and
// accelerometer jobs that finish after a moment, and a synthetic
// accelerometer trace shaped like a rat exploring (still bouts, walking,
// rears, one hard knock) so the chart has something realistic to draw.

const T0_S = Date.UTC(2026, 9, 13, 14, 0, 0) / 1000; // matches MOCK_T0_MS
const REAL = "2026-10-13_15-00-00/Record Node 101/experiment2/recording1";
const BENCH = "2026-10-13_15-00-00/Record Node 101/experiment1/recording1";
const DUR_S = 290;

const store = {}; // session_name -> { model, accel }

function sessionState(name) {
  if (!store[name]) store[name] = { model: null, accel: {} };
  return store[name];
}

function recordings() {
  return [
    { rel: BENCH, path: BENCH, start_unix_s: T0_S - 300 + 10.8, duration_s: 139.2,
      ttl_dirs: ["events/Acquisition_Board-100.acquisition_board/TTL"], aux_ports: ["A1"] },
    { rel: REAL, path: REAL, start_unix_s: T0_S + 10.8, duration_s: DUR_S,
      ttl_dirs: ["events/Acquisition_Board-100.acquisition_board/TTL"], aux_ports: ["A1"] },
  ];
}

// Deterministic PRNG so the trace doesn't change between reloads.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function syntheticPlot(aligned) {
  const n = 1450;
  const r = rng(7);
  const dt = DUR_S / n;
  const t_s = Array.from({ length: n }, (_, i) => +(i * dt).toFixed(3));
  const names = ["ax_g", "ay_g", "az_g", "pitch_deg", "roll_deg", "odba_g"];
  const series = Object.fromEntries(names.map((k) => [k, { min: [], mean: [], max: [] }]));
  let pitch = 10;
  let roll = 0;
  let moving = false;
  let left = 0;
  for (let i = 0; i < n; i++) {
    if (left-- <= 0) {
      moving = r() < 0.6;
      left = 10 + Math.floor(r() * 60);
    }
    const rear = moving && r() < 0.04;
    const targetP = rear ? 55 : moving ? 10 + 25 * (r() - 0.4) : pitch;
    pitch += (targetP - pitch) * (rear ? 0.6 : 0.15);
    roll += ((moving ? 15 * (r() - 0.5) : roll) - roll) * 0.2;
    const knock = Math.abs(t_s[i] - 219) < dt;
    const amp = knock ? 1.4 : moving ? 0.15 + 0.35 * r() : 0.01;
    const p = (pitch * Math.PI) / 180;
    const ro = (roll * Math.PI) / 180;
    const g = [Math.sin(p), Math.cos(p) * Math.sin(ro), Math.cos(p) * Math.cos(ro)];
    ["ax_g", "ay_g", "az_g"].forEach((k, j) => {
      const mean = g[j] + amp * 0.2 * (r() - 0.5);
      series[k].mean.push(+mean.toFixed(3));
      series[k].min.push(+(mean - amp * (0.4 + r())).toFixed(3));
      series[k].max.push(+(mean + amp * (0.4 + r())).toFixed(3));
    });
    [["pitch_deg", pitch], ["roll_deg", roll]].forEach(([k, v]) => {
      series[k].mean.push(+v.toFixed(2));
      series[k].min.push(+(v - 1 - 3 * amp).toFixed(2));
      series[k].max.push(+(v + 1 + 3 * amp).toFixed(2));
    });
    const od = amp * (0.9 + 0.3 * r());
    series.odba_g.mean.push(+od.toFixed(3));
    series.odba_g.min.push(+(od * 0.6).toFixed(3));
    series.odba_g.max.push(+(od * 1.5).toFixed(3));
  }
  return {
    t_s,
    t_ns: aligned ? t_s.map((t) => Math.round((T0_S + t) * 1e9)) : null,
    series,
  };
}

function accelSummary(recording, aligned) {
  return {
    recording, recording_reason: "the recording the clock model was fitted on",
    port: "A1", channels: ["A1_AUX1", "A1_AUX2", "A1_AUX3"], rate_hz: 100,
    n: DUR_S * 100, duration_s: DUR_S, oe_start_s: 18.5686, aligned,
    start_ns: aligned ? Math.round(T0_S * 1e9) : null,
    end_ns: aligned ? Math.round((T0_S + DUR_S) * 1e9) : null,
    calibration: recording === REAL
      ? { offset_v: [0.4962, 0.4979, 0.4916], v_per_g: 0.332, method: "auto",
        residual: 0.0038, spread_deg: 71.0 }
      : { offset_v: [0.4945, 0.5011, 0.4848], v_per_g: 0.3396, method: "default" },
    clip_ceiling_g: [2.2, 2.19, 2.21],
    invalid_fraction: recording === REAL ? [0.00035, 0.00083, 0.00017] : [0, 0, 0],
    frame: "headstage axes (mounting not set); heading not measurable",
    warnings: recording === REAL ? [] : [
      "auto calibration not reliable (spread 46.0°, residual 0.0158, 0.0066 V/g): the headstage may not have moved enough; using the default calibration",
    ],
    files: { csv: "accel.csv", plot: "plot.json" },
  };
}

export const ephysRoutes = {
  get_ephys_info(payload) {
    const name = payload?.session_name;
    const st = sessionState(name);
    this._fire("ephys_info", {
      session_name: name, uploaded: true, recordings: recordings(),
      matched: { rel: REAL, reason: "overlaps the session's TTL log for 279 of 280 s (2 recordings in the upload)" },
      ...(st.model ? { model: st.model } : {}),
      accel: st.accel,
    });
  },
  run_ephys_align(payload) {
    const name = payload?.session_name;
    const rec = payload?.recording || REAL;
    this._fire("ephys_align_update", { session_name: name, state: "running", stage: "fitting model" });
    setTimeout(() => {
      sessionState(name).model = {
        r2: 0.999999998, residual_max_ms: 4.24, drift_ms: 31.2, edges_used: 359,
        oe_recording: rec, pc_clock_offset_s: 10.846, pin_number: 19,
      };
      this._fire("ephys_align_update", {
        session_name: name, state: "done", ok: true, r2: 0.999999998,
        stage: "R2 0.9999999, residual max 4.24 ms, ephys PC clock +10.8 s",
      });
    }, 1200);
  },
  run_ephys_accel(payload) {
    const name = payload?.session_name;
    const st = sessionState(name);
    const rec = payload?.recording || REAL;
    this._fire("ephys_accel_update", { session_name: name, state: "running", stage: "reading accelerometer" });
    setTimeout(() => {
      const aligned = st.model?.oe_recording === rec;
      st.accel[payload?.port || "A1"] = accelSummary(rec, aligned);
      this._fire("ephys_accel_update", {
        session_name: name, state: "done", ok: true, aligned,
        stage: aligned ? "aligned to session time; auto calibration"
          : "ephys time only (no matching clock model)",
      });
    }, 1500);
  },
  get_ephys_accel_plot(payload) {
    const name = payload?.session_name;
    const s = sessionState(name).accel[payload?.port];
    if (!s) {
      this._fire("ephys_accel_plot", { session_name: name, port: payload?.port, error: "no accelerometer plot" });
      return;
    }
    this._fire("ephys_accel_plot", {
      session_name: name, port: payload?.port,
      plot: { ...syntheticPlot(s.aligned), summary: s },
    });
  },
};
