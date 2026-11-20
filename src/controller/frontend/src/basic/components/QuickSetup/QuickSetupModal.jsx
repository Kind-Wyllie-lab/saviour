import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import useModules from "/src/hooks/useModules";
import useRigCheck, { rigShortfalls } from "/src/hooks/useRigCheck";
import useTTLLines from "/src/hooks/useTTLLines";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import { saveQuickSetup, describeRun } from "/src/hooks/useQuickSetup";
import MJPEGStreamCard from "/src/basic/components/MJPEGStreamCard/MJPEGStreamCard";
import {
  EXTERNAL_SYNC, moduleKindLabel, syncModules, systemName, syncApplication, pinLabel, syncWarnings,
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

function StepHead({ title, lead }) {
  return (
    <header className="qs-step-head">
      <h3>{title}</h3>
      {lead && <p>{lead}</p>}
    </header>
  );
}

const STATUS_MARK = { ok: "✓", warn: "!", info: "…" };

function SummaryCard({ title, status, value, children }) {
  return (
    <div className={`qs-card qs-summary-card qs-summary-card--${status}`}>
      <span className="qs-summary-mark" aria-hidden="true">{STATUS_MARK[status]}</span>
      <div className="qs-summary-body">
        <span className="qs-summary-title">{title}</span>
        <span className="qs-summary-value">{value}</span>
        {children}
      </div>
    </div>
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

  // What the chosen external sync would flag: its conditional rig items and
  // unconfirmed sync lines (as ExternalSyncField on the New Session form).
  const syncMissing = externalSync
    ? rigShortfalls(rig, "required", [externalSync]).filter((i) => i.when) : [];
  const syncIssues = externalSync ? syncWarnings(ttlLines, syncApplication(externalSync)) : [];

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
            <section className="qs-step">
              <StepHead title="Rig type" lead="What this controller is set up to run." />
              <div className="qs-card qs-card--hero">
                <span className="qs-eyebrow">This controller is a</span>
                <h3 className="qs-hero-title">{rigType.label}</h3>
                {rigType.description && <p className="qs-hero-text">{rigType.description}</p>}
              </div>
              <div className="qs-card">
                <h4 className="qs-card-title">What this rig expects</h4>
                {rig?.items?.length ? (
                  <ul className="qs-expect-list">
                    {rig.items.map((i) => (
                      <li key={`${i.label}:${i.when ?? ""}`}>
                        <span className="qs-expect-label">{i.label}</span>
                        <span className="qs-expect-meta">
                          at least {i.min}
                          {i.severity === "optional" && " · optional"}
                          {i.when && ` · ${EXTERNAL_SYNC[i.when]?.label ?? i.when} sessions`}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="qs-muted">Nothing listed yet. You can add items in the Expected modules step.</p>
                )}
              </div>
              <p className="quick-setup__hint">
                Wrong rig type? Change it on the controller with <code>sudo saviour-config</code> (it
                rebuilds this interface); it can't be changed from here.
              </p>
            </section>
          )}

          {key === "sync" && (
            <section className="qs-step">
              <StepHead title="External sync"
                lead="Is anything recording alongside SAVIOUR that needs the sync pulses?" />
              <div className="qs-options" role="radiogroup" aria-label="External sync">
                {[["", { label: "None", description: "Nothing else records alongside SAVIOUR." }],
                  ...Object.entries(EXTERNAL_SYNC)].map(([k, opt]) => (
                  <button key={k || "none"} type="button" role="radio" aria-checked={externalSync === k}
                    className={`qs-option${externalSync === k ? " is-selected" : ""}`}
                    onClick={() => setExternalSync(k)}>
                    <span className="qs-option-dot" aria-hidden="true" />
                    <span className="qs-option-text">
                      <span className="qs-option-title">{opt.label}</span>
                      <span className="qs-option-desc">{opt.description}</span>
                    </span>
                  </button>
                ))}
              </div>
              {(syncMissing.length > 0 || syncIssues.length > 0) && (
                <div className="qs-card qs-card--warn">
                  <h4 className="qs-card-title">Before an {EXTERNAL_SYNC[externalSync].label} session</h4>
                  <ul className="qs-plain-list">
                    {syncMissing.map((i) => (
                      <li key={i.label}>
                        {i.label}: {i.count} of {i.min}. Set a TTL module's “Used for” to{" "}
                        {EXTERNAL_SYNC[externalSync].label.replace(/ \(.*\)$/, "")} sync.
                      </li>
                    ))}
                    {syncIssues.map((w) => <li key={w}>{w}</li>)}
                  </ul>
                </div>
              )}
              <p className="quick-setup__hint">
                Your choice becomes the New Session form's default; it doesn't change a session that
                already exists.{externalSync && " A Sync lines step is added to check the wiring."}
              </p>
            </section>
          )}

          {key === "modules" && (
            <section className="qs-step">
              <StepHead title="Expected modules"
                lead={`What this rig should have${externalSync ? ` for ${EXTERNAL_SYNC[externalSync].label} sessions` : ""}, and what's connected.`} />
              <ExpectedModulesStep rig={rig} conditions={conditions} externalSync={externalSync}
                modules={modules} offline={offline} onClose={onClose} />
            </section>
          )}

          {key === "lines" && (
            <section className="qs-step">
              <StepHead title="Sync lines"
                lead={`Check each line reaches ${systemName(syncApplication(externalSync))}.`} />
              <SyncLinesStep externalSync={externalSync} ttlLines={ttlLines} />
            </section>
          )}

          {key === "previews" && (
            <section className="qs-step qs-step--fill">
              <StepHead title="Previews"
                lead="Look at what each module sees or hears. Confirm it, or open its settings to fix it." />
              {modules.length === 0 ? (
                <p className="qs-muted">No modules are connected.</p>
              ) : (
              <div className="quick-setup__previews">
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
              </div>
              )}
            </section>
          )}

          {key === "summary" && (
            <section className="qs-step">
              <StepHead title="Summary" lead="Done saves this run on the controller. Nothing is enforced from it." />
              <div className="qs-summary">
                <SummaryCard title="Rig type" status="ok" value={rigType.label} />
                <SummaryCard title="External sync" status="ok"
                  value={externalSync ? EXTERNAL_SYNC[externalSync].label : "None"} />
                <SummaryCard title="Expected hardware" status={rig ? (rig.level === "ok" ? "ok" : rig.level === "pending" ? "info" : "warn") : "info"}
                  value={rig?.summary ?? "Not known"} />
                {externalSync && (
                  <SummaryCard title="Sync lines"
                    status={syncLines.length && syncLines.every((ln) => ln.state === "confirmed") ? "ok" : "warn"}
                    value={syncLines.length
                      ? `${syncLines.filter((ln) => ln.state === "confirmed").length} of ${syncLines.length} confirmed`
                      : `No ${EXTERNAL_SYNC[externalSync].label} sync lines set up`}>
                    {syncLines.some((ln) => ln.state !== "confirmed") && (
                      <ul className="qs-plain-list">
                        {syncLines.filter((ln) => ln.state !== "confirmed").map((ln) => (
                          <li key={`${ln.module_id}:${ln.gpio}`}>
                            {ln.name}: {pinLabel(ln.gpio)}{ln.channel ? ` → channel ${ln.channel}` : ""},{" "}
                            {LINE_STATE[ln.state] ?? ln.state}
                          </li>
                        ))}
                      </ul>
                    )}
                  </SummaryCard>
                )}
                <SummaryCard title="Previews" status={modules.length && confirmedCount === modules.length ? "ok" : "warn"}
                  value={`${confirmedCount} of ${modules.length} confirmed`}>
                  {confirmedCount < modules.length && (
                    <ul className="qs-plain-list">
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
                </SummaryCard>
              </div>
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
