import { useEffect, useMemo, useState } from "react";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { expand, fmtTime } from "/src/basic/utils/sessionProtocol";
import { useProtocolLibrary } from "/src/basic/components/Protocol/protocolLibrary";
import ProtocolDesigner from "/src/basic/components/Protocol/ProtocolDesigner";
import { ProtocolTestControls } from "/src/basic/components/Protocol/ProtocolTest";
import useProtocolTest from "/src/basic/components/Protocol/useProtocolTest";

// The New Session form's Protocol row: pick a library protocol for the
// session (or none), open the designer, and see whether it will run on the
// target's TTL modules -- the same check the controller makes when the
// session is created (protocol_library.check_for_session), so a protocol
// that would be refused blocks Create here first.

function scheduledPins(module) {
  return Object.entries(module?.config?.ttl?.pins ?? {})
    .filter(([, c]) => c?.mode === "scheduled")
    .map(([p]) => Number(p))
    .sort((a, b) => a - b);
}

function checkProtocol(protocol, targetModules, durationMinutes) {
  if (!protocol) return { errors: [], warnings: [], runs: [] };
  const base = expand(protocol);
  if (!base.ok) return { errors: base.errors.slice(0, 3).map((e) => `Protocol: ${e}`), warnings: [], runs: [] };
  const ttl = targetModules.filter((m) => String(m.type ?? "").includes("ttl"));
  if (!ttl.length) {
    return { errors: ["The protocol drives TTL pins, but no TTL module is in this target."], warnings: [], runs: [] };
  }
  const errors = [];
  const runs = [];
  for (const m of ttl) {
    const exp = expand(protocol, { moduleId: m.id, allowedPins: scheduledPins(m) });
    if (!exp.ok) errors.push(...exp.errors.slice(0, 3).map((e) => `${m.name || m.id}: ${e}`));
    else runs.push({ module: m, edges: exp.edges.length, pins: exp.pins });
  }
  const warnings = [];
  if (durationMinutes && base.durationS > durationMinutes * 60) {
    warnings.push(`The protocol runs ${fmtTime(base.durationS)}, longer than the session: its last steps won't run.`);
  }
  return { errors, warnings, runs, durationS: base.durationS };
}

export default function SessionProtocolField({ value, onChange, targetModules, durationMinutes, onCheck }) {
  const loggedIn = useIsLoggedIn();
  const library = useProtocolLibrary();
  const [designerOpen, setDesignerOpen] = useState(false);
  const items = library.items ?? [];
  const protocol = items.find((i) => i.name === value)?.protocol ?? null;
  // A remembered pick that's no longer in the library falls back to none.
  const missing = !!value && library.items !== null && !protocol;

  const check = useMemo(
    () => checkProtocol(protocol, targetModules, durationMinutes),
    [protocol, targetModules, durationMinutes]);
  const ok = !missing && check.errors.length === 0;
  useEffect(() => { onCheck?.(ok); }, [ok, onCheck]);

  // The designer validates against, and tests on, the target's first TTL module.
  const ttlModule = targetModules.find((m) => String(m.type ?? "").includes("ttl")) ?? null;
  const protocolTest = useProtocolTest(ttlModule?.id ?? "none");
  const renderTest = ttlModule ? (result, draft) => {
    const blocked = ttlModule.status === "RECORDING" ? "Recording: the module is busy."
      : !ttlModule.online ? "The module is offline."
      : !loggedIn ? "Sign in as admin to run a test."
      : !result || result.edges.length === 0 ? "Add some steps first."
      : !result.ok ? "Fix the problems first."
      : null;
    return (
      <ProtocolTestControls test={protocolTest.test} elapsed={protocolTest.elapsed}
        running={protocolTest.running} blocked={blocked}
        onStart={() => protocolTest.start(draft)} onStop={protocolTest.stop} />
    );
  } : undefined;

  return (
    <>
      <div className="form-row">
        <label htmlFor="protocol-select">Protocol</label>
        <div className="protocol-pick">
          <select id="protocol-select" value={missing ? "" : value} onChange={(e) => onChange(e.target.value)}>
            <option value="">None</option>
            {items.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
          <button type="button" className="secondary-button" onClick={() => setDesignerOpen(true)}>
            {protocol ? "Edit…" : "Designer…"}
          </button>
        </div>
      </div>

      {missing && (
        <p className="form-warning">“{value}” isn't in the protocol library any more: pick another or None.</p>
      )}
      {protocol && check.errors.map((e) => <p key={e} className="form-warning">{e}</p>)}
      {protocol && check.warnings.map((w) => <p key={w} className="form-warning">{w}</p>)}
      {protocol && ok && (
        <p className="form-ok">
          Protocol runs on {check.runs.map((r) => `${r.module.name || r.module.id} (${r.pins.map((p) => `GPIO ${p}`).join(", ") || "no steps"})`).join(", ")},
          {" "}last step at {fmtTime(check.durationS ?? 0, (check.durationS ?? 0) < 60)} from the start.
        </p>
      )}

      {designerOpen && (
        <ProtocolDesigner
          start={value && protocol ? { name: value } : {}}
          library={library}
          moduleLabel={ttlModule ? (ttlModule.name || ttlModule.id) : null}
          usedName={value || null}
          usedBadge="this session"
          scheduledPins={ttlModule ? scheduledPins(ttlModule) : []}
          pinConfigs={ttlModule?.config?.ttl?.pins ?? {}}
          moduleId={ttlModule?.id ?? null}
          canEdit={loggedIn}
          renderTest={renderTest}
          cursorT={protocolTest.elapsed}
          useLabel="Use for this session"
          onUse={(p) => onChange(p.name)}
          onSaved={(...args) => {
            // A rename of the picked protocol keeps it picked.
            const [name, , previousName] = args;
            if (previousName && previousName === value) onChange(name);
          }}
          onClose={() => setDesignerOpen(false)}
        />
      )}
    </>
  );
}
