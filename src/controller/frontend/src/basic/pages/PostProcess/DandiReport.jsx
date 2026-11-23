import React, { useState } from "react";

// What the DANDI check (src/controller/nwb_inspect.py: nwbinspector's
// "dandi" config + a subject ID check) found in a .nwb. Blocking items
// (CRITICAL, or a check that crashed) stop a DANDI upload; best-practice
// items don't.

const BLOCKING = new Set(["ERROR", "PYNWB_VALIDATION", "CRITICAL"]);
const GROUP_LABEL = {
  block: "Must fix for DANDI",
  violation: "Best practice",
  suggestion: "Suggestions",
};
// Plain-language fixes for the checks a SAVIOUR export usually trips.
const FIX = {
  check_subject_age: "Add the subject's age in the export form (Subject → Age, e.g. P90D).",
  check_subject_sex: "Set the subject's sex in the export form.",
  check_subject_species_exists: "Set the species in the export form.",
  check_subject_species_form: "Give the species as a Latin name, e.g. Rattus norvegicus.",
  check_experimenter_exists: "Add the experimenter (Session → Experimenter).",
  check_institution: "Add the institution (Session → Institution).",
  check_keywords: "Keywords aren't in the form yet; DANDI doesn't need them.",
  check_description: "Add a subject description.",
};

function group(m) {
  if (BLOCKING.has(m.importance)) return "block";
  return m.importance === "BEST_PRACTICE_VIOLATION" ? "violation" : "suggestion";
}

function dandiSummary(report) {
  if (!report) return { tone: "muted", text: "Not checked for DANDI" };
  if (!report.ran) return { tone: "warn", text: "DANDI check didn't run" };
  const block = report.messages.filter((m) => group(m) === "block").length;
  if (report.dandi_ready) return { tone: "ok", text: "Ready for DANDI" };
  return { tone: "bad", text: `Not ready for DANDI: ${block} to fix` };
}

/**
 * `report`: the saved inspection (or null when the file hasn't been
 * checked). `onCheck`: runs the check (shown when unchecked or to re-check).
 */
export default function DandiReport({ report, onCheck, checking, compact, error }) {
  const s = dandiSummary(report);
  const [open, setOpen] = useState(!compact && report?.ran && !report.dandi_ready);
  const groups = { block: [], violation: [], suggestion: [] };
  for (const m of report?.messages || []) groups[group(m)].push(m);
  const count = (report?.messages || []).length;

  return (
    <div className={`dandi dandi--${s.tone}`}>
      <div className="dandi__head">
        <span className={`dandi__badge dandi__badge--${s.tone}`}>{s.text}</span>
        {report?.ran && count > 0 && (
          <button type="button" className="btn-link" onClick={() => setOpen(!open)}
            aria-expanded={open}>
            {open ? "Hide details" : `Details (${count})`}
          </button>
        )}
        {onCheck && (
          <button type="button" className="btn btn-small" onClick={onCheck} disabled={checking}
            title="Run nwbinspector with DANDI's settings on this file">
            {checking ? "Checking…" : report ? "Check again" : "Check for DANDI"}
          </button>
        )}
      </div>
      {report && !report.ran && <div className="dandi__error">{report.error}</div>}
      {error && <div className="dandi__error">{error}</div>}
      {open && report?.ran && (
        <div className="dandi__details">
          {Object.entries(groups).filter(([, ms]) => ms.length).map(([g, ms]) => (
            <div key={g} className={`dandi__group dandi__group--${g}`}>
              <div className="dandi__group-title">{GROUP_LABEL[g]} ({ms.length})</div>
              <ul>
                {ms.map((m, i) => (
                  <li key={i}>
                    <span>{FIX[m.check] || m.message}</span>
                    <span className="dandi__where">
                      {m.check}{m.location && m.location !== "/" ? ` · ${m.location}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <div className="dandi__foot">
            nwbinspector {report.inspector_version || ""} with DANDI&apos;s settings. Only
            &quot;must fix&quot; items stop an upload.
          </div>
        </div>
      )}
    </div>
  );
}
