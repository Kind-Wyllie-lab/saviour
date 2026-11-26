import { useMemo, useState } from "react";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import useModules from "/src/hooks/useModules";
import ProtocolDesigner from "/src/basic/components/Protocol/ProtocolDesigner";
import { useProtocolLibrary } from "/src/basic/components/Protocol/protocolLibrary";
import { scheduledPins, soundModulesOf } from "/src/basic/components/Protocol/protocolModules";
import { ProtocolTestControls } from "/src/basic/components/Protocol/ProtocolTest";
import useProtocolTest from "/src/basic/components/Protocol/useProtocolTest";

// The Protocols page (plans/protocol-page.md): the protocol library and
// designer as a page of its own, checked against the whole rig -- every sound
// module the controller knows, and a TTL module (picked when there are
// several), which the Test routine also runs on.

const isTtl = (m) => String(m?.type ?? "").includes("ttl");

export default function Protocols() {
  const loggedIn = useIsLoggedIn();
  const library = useProtocolLibrary();
  const { modules } = useModules();
  const list = useMemo(() => Object.values(modules ?? {}), [modules]);
  const ttlModules = useMemo(
    () => list.filter(isTtl).sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id)), [list]);
  const soundModules = useMemo(() => soundModulesOf(list), [list]);
  const [ttlId, setTtlId] = useState(null);
  const ttl = ttlModules.find((m) => m.id === ttlId) ?? ttlModules[0] ?? null;

  const protocolTest = useProtocolTest(ttl?.id ?? "none");
  const renderTest = ttl ? (result, draft) => {
    const blocked = ttl.status === "RECORDING" ? "Recording: the module is busy."
      : ttl.online === false ? "The module is offline."
      : !loggedIn ? "Sign in as admin to run a test."
      : !result || result.edges.length === 0 ? "Add some TTL steps first."
      : !result.ok ? "Fix the problems first."
      : result.pins.some((p) => !scheduledPins(ttl).includes(p)) ? "Set the lines it uses to Scheduled first (TTL card, Pins tab)."
      : null;
    return (
      <ProtocolTestControls test={protocolTest.test} elapsed={protocolTest.elapsed}
        running={protocolTest.running} blocked={blocked}
        onStart={() => protocolTest.start(draft)} onStop={protocolTest.stop} />
    );
  } : undefined;

  const rig = (
    <>
      <span>
        <b>Rig:</b>{" "}
        {ttlModules.length === 0 && soundModules.length === 0 ? "no TTL or sound modules connected."
          : [ttlModules.length && `${ttlModules.length} TTL module${ttlModules.length > 1 ? "s" : ""}`,
             soundModules.length && `${soundModules.length} sound module${soundModules.length > 1 ? "s" : ""}`]
            .filter(Boolean).join(", ")}
      </span>
      {ttlModules.length > 1 && (
        <label>
          Check TTL steps against{" "}
          <select value={ttl?.id ?? ""} onChange={(e) => setTtlId(e.target.value)}>
            {ttlModules.map((m) => <option key={m.id} value={m.id}>{m.name || m.id}</option>)}
          </select>
        </label>
      )}
    </>
  );

  return (
    <div className="protocols-page">
      <ProtocolDesigner
        asPage
        headerExtra={rig}
        start={{}}
        library={library}
        moduleLabel={ttl ? (ttl.name || ttl.id) : null}
        scheduledPins={ttl ? scheduledPins(ttl) : []}
        pinConfigs={ttl?.config?.ttl?.pins ?? {}}
        moduleId={ttl?.id ?? null}
        soundModules={soundModules}
        canEdit={loggedIn}
        renderTest={renderTest}
        cursorT={protocolTest.elapsed}
      />
    </div>
  );
}
