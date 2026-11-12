import { useEffect, useMemo, useState } from "react";
import socket from "/src/socket";

import "./MockBusPanel.css";

// Mock mode only (loaded by the habitat Gates page when VITE_MOCK=1): plays
// the RFID bus by hand. Each antenna is a reader unit on the simulated bus;
// clicking Read puts the tag on it, so a mapping run can be walked through
// gate by gate without hardware. gateMapMock.js does the rest, following
// the controller's MappingRun rules.

const EXAMPLE = "# address, your label, serial (label and serial optional)\n1, ABVH1\n2, ABVH2\n3\n";

export default function MockBusPanel() {
  const [bus, setBus] = useState(null);
  const [filter, setFilter] = useState("");
  const [onlyUnused, setOnlyUnused] = useState(false);
  const [listText, setListText] = useState("");
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const onState = (s) => setBus(s);
    socket.on("mock_bus_state", onState);
    socket.emit("mock_bus_get");
    return () => socket.off("mock_bus_state", onState);
  }, []);

  // Tick while a gate's settle window is open, for the countdown.
  const settling = bus?.run?.first_ts && !bus.run.settled;
  useEffect(() => {
    if (!settling) return undefined;
    const t = setInterval(() => setNowMs(Date.now()), 200);
    return () => clearInterval(t);
  }, [settling]);

  const shown = useMemo(() => {
    if (!bus) return [];
    const q = filter.trim().toLowerCase();
    return bus.antennas.filter((a) => (!onlyUnused || a.gates.length === 0)
      && (!q || String(a.address) === q || a.label.toLowerCase().includes(q)
        || a.gates.some((x) => x.toLowerCase().includes(q)) || a.serial.toLowerCase().includes(q)));
  }, [bus, filter, onlyUnused]);

  if (!bus) return null;
  const send = (event, payload) => socket.emit(event, payload ?? {});
  const run = bus.run;
  const settleLeft = settling ? Math.max(0, bus.settle_s - (nowMs / 1000 - run.first_ts)) : 0;

  let status;
  if (!run) status = "No mapping run: a read only shows on the Walk-through tab and the dashboard.";
  else if (run.phase === "probe") status = "The first tag you read becomes this run's probe.";
  else if (run.phase === "review") status = "All gates done: review and save on the left.";
  else if (settling) status = `Collecting reads for ${run.gate}: ${settleLeft.toFixed(1)} s left to add a second antenna.`;
  else if (run.settled) status = `${run.gate} has settled: confirm or resolve it in the run panel (Retry to read again).`;
  else status = <>Put the probe on the antenna at <strong>{run.gate}</strong> and click its Read.</>;

  return (
    <div className="gates-card mock-bus">
      <div className="gates-card-head">
        <h3>Simulated bus <span className="mock-bus__badge">mock only</span></h3>
        <label className="mock-bus__toggle" title="Off: each prompt is answered by itself after a moment, sometimes with a double read or a duplicate">
          <input type="checkbox" checked={bus.manual} onChange={(e) => send("mock_bus_set", { manual: e.target.checked })} />
          Answer prompts by hand
        </label>
      </div>
      <p className="mock-bus__status">{status}</p>

      <div className="gates-field">
        Tag to read
        <label>
          <input type="radio" checked={bus.tag_mode === "probe"} onChange={() => send("mock_bus_set", { tag_mode: "probe" })} />
          {" "}Probe <span className="gates-mono">{bus.probe_tag}</span>
        </label>
        <label title="During a run, a tag that isn't the probe should be ignored (an animal still in the habitat)">
          <input type="radio" checked={bus.tag_mode === "animal"} onChange={() => send("mock_bus_set", { tag_mode: "animal" })} />
          {" "}Animal tag <span className="gates-mono">{bus.animal_tag}</span> (a run should ignore it)
        </label>
      </div>

      <div className="mock-bus__filters">
        <input className="mock-bus__search" placeholder="Find address, label, gate or serial" value={filter}
               onChange={(e) => setFilter(e.target.value)} />
        <label><input type="checkbox" checked={onlyUnused} onChange={(e) => setOnlyUnused(e.target.checked)} /> Unused only</label>
        <span className="gates-muted">{shown.length} of {bus.antennas.length}</span>
      </div>

      <div className="mock-bus__table-wrap">
        <table className="gates-table mock-bus__table">
          <thead>
            <tr><th>Unit</th><th>Label</th><th>In the map as</th><th>Serial</th><th /></tr>
          </thead>
          <tbody>
            {shown.map((a) => (
              <tr key={a.address} className={`${a.online ? "" : "mock-bus__row--off"} ${a.reads ? "mock-bus__row--read" : ""}`}>
                <td className="gates-mono">{a.address}</td>
                <td>{a.label}</td>
                <td className="gates-mono">
                  {a.gates.length ? a.gates.join(", ") : <span className="gates-muted">-</span>}
                  {a.gates.length > 1 && <span className="mock-bus__warn" title="Mapped at more than one gate"> ×{a.gates.length}</span>}
                </td>
                <td className="gates-mono mock-bus__serial">{a.serial}</td>
                <td className="mock-bus__row-actions">
                  <button type="button" className="gates-btn gates-btn--primary" disabled={!a.online}
                          onClick={() => send("mock_bus_read", { address: a.address })}
                          title={a.online ? "Read the tag on this antenna" : "Unplugged"}>
                    Read{a.reads ? ` (${a.reads})` : ""}
                  </button>
                  <label className="mock-bus__mini" title="Off: no reads and no answer to a bus scan">
                    <input type="checkbox" checked={a.online}
                           onChange={(e) => send("mock_bus_antenna", { address: a.address, online: e.target.checked })} />
                    on bus
                  </label>
                  <button type="button" className="gates-link" title="Put a different decoder on this address (new serial): the next scan should flag it"
                          onClick={() => send("mock_bus_antenna", { address: a.address, swap: true })}>
                    swap
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="gates-muted">
        Click a second antenna's Read within {bus.settle_s} s of the first for a double read. Reading
        an antenna that is already in the map at another gate should be caught as a duplicate.
        Scan the bus (Map tab) after unplugging or swapping one to see the bus checks.
      </p>

      {bus.error && (
        <div className="gates-error" role="alert">
          {bus.error}
          <button type="button" className="gates-link" onClick={() => send("mock_bus_dismiss")}>dismiss</button>
        </div>
      )}

      <details className="mock-bus__list">
        <summary>{bus.custom ? "Your antenna list (in use)" : "Use your own antenna list"}</summary>
        <p className="gates-muted">
          One antenna per line: <span className="gates-mono">address, label, serial</span>; label and
          serial are optional, and a line with only a label gets the next free address. A repeated
          address or label is refused. Applying it starts an empty map; the list is kept in this
          browser.
        </p>
        <textarea rows={6} value={listText} placeholder={EXAMPLE}
                  onChange={(e) => setListText(e.target.value)} />
        <div className="gates-actions">
          <button type="button" className="gates-btn gates-btn--primary" disabled={!listText.trim()}
                  onClick={() => send("mock_bus_set_list", { text: listText })}>
            Apply list
          </button>
          <button type="button" className="gates-btn"
                  onClick={() => setListText(bus.antennas.map((a) => [a.address, a.label, a.serial].join(", ")).join("\n"))}>
            Copy current into the box
          </button>
          <button type="button" className="gates-link" onClick={() => send("mock_bus_reset")}>
            Back to the simulator&apos;s bus and map
          </button>
        </div>
      </details>
      <div className="gates-actions">
        <button type="button" className="gates-btn" disabled={!!run} onClick={() => send("mock_bus_clear_map")}
                title={run ? "Finish or discard the run first" : "Unmap every gate, to map from scratch"}>
          Clear the saved map
        </button>
      </div>
    </div>
  );
}
