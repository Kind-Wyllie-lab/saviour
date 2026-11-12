import { useEffect, useMemo, useState } from "react";
import { labelsToText, loadLabels, parseLabels, saveLabels } from "./antennaLabels";

import "./SimulatedBusPanel.css";

// Shown when an RFID module runs a simulated bus (rfid.mock.enabled; in
// dev:mock, the mock's own). Plays the bus by hand: Read holds a tag at
// that antenna, and the read goes through the module's real receive path
// to the controller's mapping run (gate_map_service.py mock_read). Units
// can be unplugged or have their decoder swapped, to exercise the bus
// checks. Labels are yours, kept in this browser.

function mappedByUnit(entries) {
  const out = {};
  for (const [gate, e] of Object.entries(entries ?? {})) {
    if (e?.status === "mapped") (out[`${e.module_id}:${e.unit}`] ??= []).push(gate);
  }
  return out;
}

function runStatus(run) {
  if (!run) return "No mapping run: a read shows on the Walk-through tab (while it's on) and the dashboard.";
  if (run.phase === "probe") return "The first tag you read becomes this run's probe.";
  if (run.phase === "review") return "All gates done: review and save in the run panel.";
  const c = run.capture ?? {};
  if (c.first && !c.settled) {
    return `Collecting reads for ${run.gate}: read a second antenna now for a double read.`;
  }
  if (c.settled) return `${run.gate} has settled: confirm or resolve it in the run panel (Retry to read again).`;
  return <>Hold the probe at the antenna for <strong>{run.gate}</strong>: click its Read.</>;
}

function BusPanel({ bus, run, entries, send, loggedIn }) {
  const [tagMode, setTagMode] = useState("probe");
  const [filter, setFilter] = useState("");
  const [onlyUnused, setOnlyUnused] = useState(false);
  const [labels, setLabels] = useState(() => loadLabels(bus.module_id));
  const [labelText, setLabelText] = useState("");
  const [labelMsg, setLabelMsg] = useState(null);
  useEffect(() => { setLabels(loadLabels(bus.module_id)); }, [bus.module_id]);

  const byUnit = useMemo(() => mappedByUnit(entries), [entries]);
  const counts = run?.phase === "gate" ? run.capture?.counts : null;
  const rows = useMemo(() => bus.units.map((u) => {
    const key = `${bus.module_id}:${u.address}`;
    return { ...u, key, label: labels[u.address] ?? "", gates: byUnit[key] ?? [], reads: counts?.[key] ?? 0 };
  }), [bus, labels, byUnit, counts]);
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return rows.filter((r) => (!onlyUnused || !r.gates.length)
      && (!q || String(r.address) === q || r.label.toLowerCase().includes(q)
        || r.gates.some((g) => g.toLowerCase().includes(q)) || (r.serial ?? "").toLowerCase().includes(q)));
  }, [rows, filter, onlyUnused]);

  const mod = { module_id: bus.module_id };
  const tag = tagMode === "animal" ? bus.animal_tag : bus.probe_tag;
  const applyLabels = () => {
    const parsed = parseLabels(labelText, bus.units.map((u) => u.address));
    if (parsed.error) { setLabelMsg({ tone: "error", text: parsed.error }); return; }
    saveLabels(bus.module_id, parsed.labels);
    setLabels(parsed.labels);
    setLabelMsg(parsed.unknown.length
      ? { tone: "warn", text: `Saved; not on this bus: ${parsed.unknown.join(", ")}` }
      : { tone: "ok", text: `Saved ${Object.keys(parsed.labels).length} label(s)` });
  };

  return (
    <>
      <p className="sim-bus__status">{runStatus(run)}</p>
      {!loggedIn && <p className="gates-problem gates-problem--warn">Sign in to use the simulated bus.</p>}

      <div className="sim-bus__controls">
        <div className="gates-field">
          Tag to read
          <label>
            <input type="radio" checked={tagMode === "probe"} onChange={() => setTagMode("probe")} />
            {" "}Probe <span className="gates-mono">{bus.probe_tag}</span>
          </label>
          <label title="During a run, a tag that isn't the probe should be ignored (an animal still in the habitat)">
            <input type="radio" checked={tagMode === "animal"} onChange={() => setTagMode("animal")} />
            {" "}Animal <span className="gates-mono">{bus.animal_tag}</span> (a run should ignore it)
          </label>
        </div>
        {bus.animals > 0 && (
          <label className="sim-bus__toggle" title="The simulator's own tags wandering between units">
            <input type="checkbox" checked={!!bus.animals_running} disabled={!loggedIn}
                   onChange={(e) => send("gate_map_mock_animals", { ...mod, running: e.target.checked })} />
            {bus.animals} wandering tag(s)
          </label>
        )}
        {bus.dev && (
          <label className="sim-bus__toggle" title="Off: each prompt is answered by itself after a moment, sometimes with a double read or a duplicate">
            <input type="checkbox" checked={!!bus.manual}
                   onChange={(e) => send("mock_bus_set", { manual: e.target.checked })} />
            Answer prompts by hand
          </label>
        )}
      </div>

      <div className="sim-bus__filters">
        <input className="sim-bus__search" placeholder="Find address, label, gate or serial" value={filter}
               onChange={(e) => setFilter(e.target.value)} />
        <label><input type="checkbox" checked={onlyUnused} onChange={(e) => setOnlyUnused(e.target.checked)} /> Unused only</label>
        <span className="gates-muted">{shown.length} of {rows.length}</span>
      </div>

      <div className="sim-bus__table-wrap">
        <table className="gates-table sim-bus__table">
          <thead>
            <tr><th>Unit</th><th>Label</th><th>In the map as</th><th>Serial</th><th /></tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.address} className={`${r.plugged ? "" : "sim-bus__row--off"} ${r.reads ? "sim-bus__row--read" : ""}`}>
                <td className="gates-mono">{r.address}</td>
                <td>{r.label}</td>
                <td className="gates-mono">
                  {r.gates.length ? r.gates.join(", ") : <span className="gates-muted">-</span>}
                  {r.gates.length > 1 && <span className="sim-bus__warn" title="Mapped at more than one gate"> ×{r.gates.length}</span>}
                </td>
                <td className="gates-mono sim-bus__serial">{r.serial}</td>
                <td className="sim-bus__row-actions">
                  <button type="button" className="gates-btn gates-btn--primary" disabled={!loggedIn || !r.plugged}
                          onClick={() => send("gate_map_mock_read", { ...mod, unit: r.address, tag })}
                          title={r.plugged ? `Hold the ${tagMode} tag at this antenna` : "Unplugged"}>
                    Read{r.reads ? ` (${r.reads})` : ""}
                  </button>
                  <label className="sim-bus__mini" title="Off: unplugged (no reads, no answer to a bus scan)">
                    <input type="checkbox" checked={r.plugged} disabled={!loggedIn}
                           onChange={(e) => send("gate_map_mock_unit", { ...mod, unit: r.address, plugged: e.target.checked })} />
                    plugged
                  </label>
                  <button type="button" className="gates-link" disabled={!loggedIn}
                          title="Put a different decoder on this address (a new serial): the next scan should flag it"
                          onClick={() => send("gate_map_mock_unit", { ...mod, unit: r.address, swap: true })}>
                    swap
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="gates-muted">
        Read a second antenna while the first is still being collected for a double read; read
        one that's already in the map at another gate and the run should catch it. After
        unplugging or swapping a unit, Scan bus (Map tab) to see the bus checks.
      </p>

      <details className="sim-bus__labels">
        <summary>Your antenna labels{Object.keys(labels).length ? ` (${Object.keys(labels).length})` : ""}</summary>
        <p className="gates-muted">
          One per line: <span className="gates-mono">address, label</span>. A repeated address or
          label is refused. Kept in this browser only.
        </p>
        <textarea rows={5} value={labelText} placeholder={"1, ABVH1\n2, ABVH2"}
                  onChange={(e) => { setLabelText(e.target.value); setLabelMsg(null); }} />
        {labelMsg && <p className={labelMsg.tone === "ok" ? "gates-ok" : `gates-problem gates-problem--${labelMsg.tone}`}>{labelMsg.text}</p>}
        <div className="gates-actions">
          <button type="button" className="gates-btn gates-btn--primary" disabled={!labelText.trim()} onClick={applyLabels}>
            Save labels
          </button>
          <button type="button" className="gates-btn" onClick={() => setLabelText(labelsToText(labels))}>
            Edit current
          </button>
          <button type="button" className="gates-link" disabled={!Object.keys(labels).length}
                  onClick={() => { saveLabels(bus.module_id, null); setLabels({}); setLabelMsg(null); }}>
            Clear labels
          </button>
        </div>
      </details>

      {bus.dev && (
        <div className="gates-actions">
          <button type="button" className="gates-btn" disabled={!!run} onClick={() => send("mock_bus_clear_map")}
                  title={run ? "Finish or discard the run first" : "Unmap every gate, to map from scratch"}>
            Clear the saved map
          </button>
          <button type="button" className="gates-link" onClick={() => send("mock_bus_reset")}>
            Back to the simulator&apos;s map
          </button>
        </div>
      )}
    </>
  );
}

export default function SimulatedBusPanel({ state, entries, send, loggedIn }) {
  const buses = state.mock_buses ?? [];
  const [selected, setSelected] = useState(null);
  const bus = buses.find((b) => b.module_id === selected) ?? buses[0];
  if (!bus) return null;
  return (
    <div className="gates-card sim-bus">
      <div className="gates-card-head">
        <h3>Simulated bus <span className="sim-bus__badge">{bus.dev ? "mock" : "not real readers"}</span></h3>
        {buses.length > 1 ? (
          <select value={bus.module_id} onChange={(e) => setSelected(e.target.value)}>
            {buses.map((b) => <option key={b.module_id} value={b.module_id}>{b.module_id}</option>)}
          </select>
        ) : <span className="gates-muted gates-mono">{bus.module_id}</span>}
      </div>
      <BusPanel key={bus.module_id} bus={bus} run={state.run} entries={entries} send={send} loggedIn={loggedIn} />
    </div>
  );
}
