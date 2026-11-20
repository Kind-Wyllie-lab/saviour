import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import useModules from "/src/hooks/useModules";
import useRigCheck from "/src/hooks/useRigCheck";
import useTTLLines from "/src/hooks/useTTLLines";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import { saveQuickSetup, describeRun } from "/src/hooks/useQuickSetup";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import ExternalSyncField from "/src/basic/pages/Recording/ExternalSyncField";
import {
  EXTERNAL_SYNC, moduleKindLabel, syncModules, systemName, syncApplication, pinLabel,
} from "/src/basic/utils/ttlApplications";
import { streamInfo } from "/src/basic/utils/moduleStreams";
import { RIG_TYPES, BUILT_VARIANT } from "./rigTypes";
import ExpectedModulesStep from "./ExpectedModulesStep";
import SyncLinesStep from "./SyncLinesStep";

// Quick setup (plans/rig-quick-setup.md): an on-demand walk through the rig
// before recording. Rig type -> external sync -> expected modules (add to
// the list) -> sync lines (only with an external sync) -> each module's
// preview (Looks OK, or open its settings to fix it) -> summary.
// Nothing here is enforced; Done saves the run on the controller
// (quick_setup.py) and makes the external sync choice the New Session
// form's default.

const ALL_STEPS = [
  { key: "rig", label: "Rig type" },
  { key: "sync", label: "External sync" },
  { key: "modules", label: "Expected modules" },
  { key: "lines", label: "Sync lines", needsSync: true },
  { key: "previews", label: "Previews" },
  { key: "summary", label: "Summary" },
];
const LINE_STATE = {
  confirmed: "confirmed", stale: "confirm again", changed: "wiring changed", unconfirmed: "not confirmed",
};
// The New Session forms' remembered choice (usePersistedState, sessionStorage).
const FORM_SYNC_KEY = "saviour_session_form_external_sync";

function readFormSync() {
  try {
    const v = JSON.parse(sessionStorage.getItem(FORM_SYNC_KEY) ?? '""');
    return EXTERNAL_SYNC[v] ? v : "";
  } catch { return ""; }
}

function moduleTitle(m) {
  const kind = moduleKindLabel(m);
  return `${m.name || m.id}${kind ? ` · ${kind}` : ""}`;
}

function StepRail({ steps, step, setStep }) {
  return (
    <ol className="quick-setup__rail">
      {steps.map((s, i) => (
        <li key={s.key}>
          <button
            type="button"
            className={`quick-setup__rail-step${i === step ? " is-current" : ""}${i < step ? " is-done" : ""}`}
            onClick={() => setStep(i)}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="quick-setup__rail-num">{i < step ? "✓" : i + 1}</span>
            {s.label}
          </button>
        </li>
      ))}
    </ol>
  );
}

export default function QuickSetupModal({ lastRun, onClose, onDone }) {
  const { moduleList } = useModules();
  const rig = useRigCheck();
  const { lines: ttlLines } = useTTLLines();
  const { experimenter } = useExperimentTitle();
  const [stepKey, setStepKey] = useState("rig");
  const [externalSync, setExternalSync] = useState(readFormSync);
  const steps = ALL_STEPS.filter((s) => !s.needsSync || externalSync);
  const step = Math.max(0, steps.findIndex((s) => s.key === stepKey));
  const setStep = (i) => setStepKey(steps[Math.max(0, Math.min(steps.length - 1, i))].key);
  const [confirmed, setConfirmed] = useState({}); // module id -> true
  const [current, setCurrent] = useState(0);      // index into `modules` on the Previews step

  const rigType = RIG_TYPES[BUILT_VARIANT] ?? { label: BUILT_VARIANT, description: "" };
  const conditions = externalSync ? [externalSync] : [];
  const modules = useMemo(() => moduleList
    .filter((m) => m.online !== false)
    .sort((a, b) => (a.type || "").localeCompare(b.type || "")
      || (a.name || a.id).localeCompare(b.name || b.id)), [moduleList]);
  const offline = moduleList.filter((m) => m.online === false);
  const mod = modules[Math.min(current, Math.max(0, modules.length - 1))];
  const confirmedCount = modules.filter((m) => confirmed[m.id]).length;

  useEffect(() => {
    const onKey = (e) => {
      // Capture phase + stop: Escape closes this modal only, not a drawer
      // (New Session) it was opened from.
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (stepKey !== "previews" || e.target.closest?.("input, select, textarea")) return;
      if (e.key === "ArrowRight") setCurrent((c) => Math.min(modules.length - 1, c + 1));
      if (e.key === "ArrowLeft") setCurrent((c) => Math.max(0, c - 1));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, stepKey, modules.length]);

  const looksOk = (id) => {
    setConfirmed((c) => ({ ...c, [id]: true }));
    const next = modules.findIndex((m, i) => i > current && !confirmed[m.id] && m.id !== id);
    if (next >= 0) setCurrent(next);
  };
  const undo = (id) => setConfirmed((c) => { const n = { ...c }; delete n[id]; return n; });

  const syncMods = syncModules(ttlLines, externalSync);
  const syncLines = syncMods.flatMap((m) => (m.lines ?? []).map((ln) => ({
    module_id: m.id, name: m.name, gpio: ln.gpio, role: ln.role,
    channel: Number.isInteger(ln.channel) ? ln.channel : null, state: ln.state })));

  const done = () => {
    try { sessionStorage.setItem(FORM_SYNC_KEY, JSON.stringify(externalSync)); } catch { /* storage unavailable */ }
    saveQuickSetup({
      researcher: experimenter || null,
      variant: BUILT_VARIANT,
      external_sync: externalSync || null,
      rig: rig ? { level: rig.level, summary: rig.summary } : null,
      modules: Object.fromEntries(modules.map((m) => [m.id, {
        name: m.name || m.id, type: m.type, confirmed: !!confirmed[m.id] }])),
      sync_lines: syncLines,
    });
    onDone?.({ externalSync });
    onClose();
  };

  const key = steps[step].key;
  const info = mod ? streamInfo(mod) : null;

  return createPortal(
    <div className="modal-overlay quick-setup-overlay" onClick={onClose}>
      <div className="modal quick-setup" role="dialog" aria-modal="true" aria-label="Quick setup"
        onClick={(e) => e.stopPropagation()}>
        <div className="quick-setup__head">
          <h2>Quick setup</h2>
          {lastRun && <span className="quick-setup__last">Last run {describeRun(lastRun)}</span>}
          <button type="button" className="quick-setup__close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <StepRail steps={steps} step={step} setStep={setStep} />

        <div className="quick-setup__body">
          {key === "rig" && (
            <section>
              <h3>{rigType.label}</h3>
              <p>{rigType.description}</p>
              <p className="quick-setup__hint">
                This controller is set up as a <strong>{rigType.label}</strong>. If that's wrong,
                change it on the controller with <code>sudo saviour-config</code> (it rebuilds this
                interface); it can't be changed from here.
              </p>
            </section>
          )}

          {key === "sync" && (
            <section>
              <p>Is anything recording alongside SAVIOUR that needs the sync pulses?</p>
              <ExternalSyncField value={externalSync} onChange={setExternalSync} rig={rig} ttlLines={ttlLines} />
              <p className="quick-setup__hint">
                Your choice becomes the New Session form's default. It doesn't change a session
                that already exists.{externalSync && " A Sync lines step is added to check the wiring."}
              </p>
            </section>
          )}

          {key === "modules" && (
            <ExpectedModulesStep rig={rig} conditions={conditions} externalSync={externalSync}
              modules={modules} offline={offline} onClose={onClose} />
          )}

          {key === "lines" && <SyncLinesStep externalSync={externalSync} ttlLines={ttlLines} />}

          {key === "previews" && (
            modules.length === 0 ? (
              <p>No modules are connected.</p>
            ) : (
              <section className="quick-setup__previews">
                <div className="quick-setup__thumbs" role="tablist" aria-label="Modules">
                  {modules.map((m, i) => (
                    <button
                      key={m.id}
                      type="button"
                      role="tab"
                      aria-selected={i === current}
                      className={`quick-setup__thumb${i === current ? " is-current" : ""}${confirmed[m.id] ? " is-ok" : ""}`}
                      onClick={() => setCurrent(i)}
                      title={moduleTitle(m)}
                    >
                      <span className="quick-setup__thumb-mark" aria-hidden="true">{confirmed[m.id] ? "✓" : ""}</span>
                      {m.name || m.id}
                    </button>
                  ))}
                </div>
                <div className="quick-setup__preview">
                  <div className="quick-setup__preview-head">
                    <strong>{moduleTitle(mod)}</strong>
                    <span className="quick-setup__hint">{mod.type} · {mod.id}</span>
                  </div>
                  {info ? (
                    <MJPEGStreamCard key={mod.id} ip={mod.ip} port={info.port} label={info.label}
                      isRecording={mod.status === "RECORDING"} />
                  ) : (
                    <p className="quick-setup__no-preview">
                      No live preview for this module type. Status: {mod.status ?? "unknown"}
                      {mod.ready_message ? ` (${mod.ready_message})` : ""}
                    </p>
                  )}
                  <div className="quick-setup__preview-actions">
                    {confirmed[mod.id] ? (
                      <button type="button" className="quick-setup__btn" onClick={() => undo(mod.id)}>
                        ✓ Looks OK (undo)
                      </button>
                    ) : (
                      <button type="button" className="quick-setup__btn quick-setup__btn--primary" onClick={() => looksOk(mod.id)}>
                        Looks OK
                      </button>
                    )}
                    <a className="quick-setup__btn" href={`/settings#${mod.id}`} target="_blank" rel="noreferrer"
                      title="Fix it on the module's settings card (opens in a new tab; come back here after)">
                      Open settings ↗
                    </a>
                    <span className="quick-setup__hint">
                      {confirmedCount} of {modules.length} confirmed · ← → to move
                    </span>
                  </div>
                </div>
              </section>
            )
          )}

          {key === "summary" && (
            <section>
              <dl className="quick-setup__summary">
                <dt>Rig type</dt><dd>{rigType.label}</dd>
                <dt>External sync</dt><dd>{externalSync ? EXTERNAL_SYNC[externalSync].label : "None"}</dd>
                <dt>Expected hardware</dt><dd>{rig?.summary ?? "Not known"}</dd>
                {externalSync && (
                  <>
                    <dt>Sync lines</dt>
                    <dd>
                      {syncLines.length === 0 ? (
                        `No ${EXTERNAL_SYNC[externalSync].label} sync lines set up`
                      ) : (
                        <ul className="quick-setup__unconfirmed">
                          {syncLines.map((ln) => (
                            <li key={`${ln.module_id}:${ln.gpio}`}
                              className={ln.state === "confirmed" ? "" : "quick-setup__warn-text"}>
                              {ln.name}: {pinLabel(ln.gpio)}{ln.channel ? ` → channel ${ln.channel}` : ""},{" "}
                              {LINE_STATE[ln.state] ?? ln.state}
                              {ln.state !== "confirmed" && ln.role === "sync" && ` in ${systemName(syncApplication(externalSync))}`}
                            </li>
                          ))}
                        </ul>
                      )}
                    </dd>
                  </>
                )}
                <dt>Previews</dt>
                <dd>
                  {confirmedCount} of {modules.length} confirmed
                  {confirmedCount < modules.length && (
                    <ul className="quick-setup__unconfirmed">
                      {modules.filter((m) => !confirmed[m.id]).map((m) => (
                        <li key={m.id}>
                          <button type="button" className="quick-setup__link"
                            onClick={() => { setCurrent(modules.indexOf(m)); setStepKey("previews"); }}>
                            {moduleTitle(m)}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </dd>
              </dl>
              <p className="quick-setup__hint">Done saves this run on the controller (only the latest is kept). Nothing is enforced from it.</p>
            </section>
          )}
        </div>

        <div className="quick-setup__foot">
          <button type="button" className="quick-setup__btn" onClick={() => setStep(step - 1)} disabled={step === 0}>
            ← Back
          </button>
          {key === "summary" ? (
            <button type="button" className="quick-setup__btn quick-setup__btn--primary" onClick={done}>Done</button>
          ) : (
            <button type="button" className="quick-setup__btn quick-setup__btn--primary" onClick={() => setStep(step + 1)}>
              Next →
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
