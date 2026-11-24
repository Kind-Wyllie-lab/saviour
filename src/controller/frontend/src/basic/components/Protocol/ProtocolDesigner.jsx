import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ProtocolEditor from "./ProtocolEditor";
import { EMPTY_PROTOCOL, freeName, sameProtocol } from "./protocolLibrary";
import "./ProtocolDesigner.css";
import { formatUtc } from "/src/basic/utils/time";
import useModules from "/src/hooks/useModules";
import { soundModulesOf } from "./protocolModules";

// The Protocol designer: the controller's library of named protocols on the
// left (new / copy / delete), the selected one's editor on the right (name,
// steps, timeline, Test routine), Save writes it to the library.
//
// Validation runs against the module the designer was opened from (its
// Scheduled pins); a protocol in the library isn't tied to one module.
//
// start: { name } opens a library protocol, { protocol, name? } a new draft
// (e.g. a module's unsaved copy). library: useProtocolLibrary().
// usedName / usedBadge: the protocol the opener has picked, marked in the list.
// onUse(protocol) / useLabel: the footer's "use this one" action (e.g. pick it
// for the session being created). onSaved(name, protocol, previousName):
// after a save lands. moduleLabel: the TTL module validation and Test
// routine run against (null = none in reach: checked for format only).
// soundModules: the sound modules play steps are offered for and checked
// against (default: every sound module the controller knows).

function fmtWhen(ts) {
  if (!ts) return "";
  return formatUtc(ts, { year: true });
}

export default function ProtocolDesigner({
  start, library, moduleLabel, usedName, usedBadge = "picked", scheduledPins, pinConfigs, moduleId,
  renderTest, cursorT, canEdit, onUse, useLabel = "Use this protocol", onSaved, onClose,
  soundModules,
}) {
  const { modules } = useModules();
  const sounds = useMemo(() => soundModules ?? soundModulesOf(modules), [soundModules, modules]);
  const { items, error, clearError, change } = library;
  const [draft, setDraft] = useState(null); // { original, name, protocol }
  const [pending, setPending] = useState(null); // name we expect a `protocols` change for
  const [filter, setFilter] = useState("");
  const nameRef = useRef(null);
  const started = useRef(false);

  const byName = useMemo(() => Object.fromEntries((items ?? []).map((i) => [i.name, i])), [items]);

  const fromLibrary = (name) => {
    const it = byName[name];
    return it ? { original: name, name, protocol: structuredClone(it.protocol) } : null;
  };
  const newDraft = (protocol, name) => ({
    original: null,
    name: freeName(name || "New protocol", items),
    protocol: structuredClone(protocol ?? EMPTY_PROTOCOL),
  });

  // Open on what we were asked for, once the library has loaded.
  useEffect(() => {
    if (started.current || items === null) return;
    started.current = true;
    if (start?.name && byName[start.name] && !start.protocol) setDraft(fromLibrary(start.name));
    else if (start?.protocol) setDraft(newDraft(start.protocol, start.name));
    else if (items.length) setDraft(fromLibrary(items[0].name));
    else setDraft(newDraft());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  // A save / copy / delete we asked for has landed: follow it.
  useEffect(() => {
    if (!change || !pending) return;
    if (pending.kind === "save" && change.saved === pending.name) {
      const it = byName[change.saved];
      if (it) {
        setDraft({ original: it.name, name: it.name, protocol: structuredClone(it.protocol) });
        onSaved?.(it.name, it.protocol, pending.previous);
      }
      setPending(null);
    } else if (pending.kind === "copy" && change.saved) {
      if (byName[change.saved]) setDraft(fromLibrary(change.saved));
      setPending(null);
    } else if (pending.kind === "delete" && change.deleted === pending.name) {
      const next = (items ?? [])[0];
      setDraft(next ? fromLibrary(next.name) : newDraft());
      setPending(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [change]);

  useEffect(() => { if (error) setPending(null); }, [error]);

  const saved = draft?.original ? byName[draft.original] : null;
  const dirty = !!draft && (!saved || draft.name.trim() !== draft.original
    || !sameProtocol(draft.protocol, saved.protocol));
  const trimmed = (draft?.name ?? "").trim().replace(/\s+/g, " ");
  const clash = !!draft && trimmed !== draft.original && !!byName[trimmed];
  const nameProblem = !trimmed ? "Give it a name"
    : clash ? `“${trimmed}” already exists`
    : /[/\\]/.test(trimmed) ? "No slashes in names" : null;

  const confirmDiscard = () => !dirty
    || window.confirm(`Discard the unsaved changes to “${draft.name || "this protocol"}”?`);

  const close = () => { if (confirmDiscard()) onClose(); };
  const pick = (name) => {
    if (name === draft?.original && !dirty) return;
    if (!confirmDiscard()) return;
    clearError();
    setDraft(fromLibrary(name));
  };
  const addNew = () => {
    if (!confirmDiscard()) return;
    clearError();
    setDraft(newDraft());
    setTimeout(() => nameRef.current?.select(), 0);
  };
  const save = () => {
    if (nameProblem) return;
    setPending({ kind: "save", name: trimmed, previous: draft.original });
    library.save(trimmed, { ...draft.protocol, name: trimmed }, draft.original);
  };
  const duplicate = () => {
    if (!draft?.original || !confirmDiscard()) return;
    setPending({ kind: "copy" });
    library.copy(draft.original);
  };
  const remove = () => {
    if (!draft?.original) return;
    const inUse = usedName && usedName === draft.original
      ? " It's picked here: that pick is cleared." : "";
    if (!window.confirm(`Delete “${draft.original}” from the library?${inUse}`)) return;
    setPending({ kind: "delete", name: draft.original });
    library.remove(draft.original);
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const shown = (items ?? []).filter((i) => i.name.toLowerCase().includes(filter.trim().toLowerCase()));

  return createPortal(
    <div className="pdm-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="pdm" role="dialog" aria-modal="true" aria-label="Protocol designer">
        <header className="pdm-head">
          <h2>Protocol designer</h2>
          <span className="pdm-head-sub">
            Protocols are saved on the controller and picked for each session.
            {moduleLabel ? ` Checked against ${moduleLabel}'s Scheduled pins.`
              : " No TTL module in reach: checked for format only."}
          </span>
          <button type="button" className="pdm-x" onClick={close} aria-label="Close">✕</button>
        </header>

        <div className="pdm-body">
          <aside className="pdm-list">
            <div className="pdm-list-actions">
              <button type="button" className="pe-small" onClick={addNew} disabled={!canEdit}>+ New</button>
              <button type="button" className="pe-small" onClick={duplicate}
                disabled={!canEdit || !draft?.original} title="Make a copy of this protocol to change">
                Copy
              </button>
              <button type="button" className="pe-small pe-danger" onClick={remove}
                disabled={!canEdit || !draft?.original}>
                Delete
              </button>
            </div>
            {(items?.length ?? 0) > 6 && (
              <input type="text" className="pdm-filter" placeholder="Filter…" value={filter}
                onChange={(e) => setFilter(e.target.value)} />
            )}
            <ul className="pdm-items">
              {items === null && <li className="pdm-empty">Loading…</li>}
              {items?.length === 0 && <li className="pdm-empty">No saved protocols yet.</li>}
              {draft && !draft.original && (
                <li className="pdm-item pdm-item--active">
                  <span className="pdm-item-name">{draft.name || "Untitled"}</span>
                  <span className="pdm-item-meta">not saved yet</span>
                </li>
              )}
              {shown.map((it) => (
                <li key={it.name}>
                  <button type="button"
                    className={`pdm-item${draft?.original === it.name ? " pdm-item--active" : ""}`}
                    onClick={() => pick(it.name)}>
                    <span className="pdm-item-name">
                      {it.name}
                      {usedName && usedName === it.name && <span className="pdm-badge">{usedBadge}</span>}
                    </span>
                    <span className="pdm-item-meta">
                      {(it.protocol?.steps?.length ?? 0)} step{it.protocol?.steps?.length === 1 ? "" : "s"}
                      {it.updated_at ? ` · ${fmtWhen(it.updated_at)}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <section className="pdm-main">
            {draft ? (
              <>
                <div className="pdm-name-row">
                  <label className="pe-field pdm-name">
                    <span className="pe-field-label">Name</span>
                    <input ref={nameRef} type="text" value={draft.name} maxLength={80} disabled={!canEdit}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </label>
                  {draft.original && trimmed !== draft.original && !nameProblem && (
                    <span className="pdm-hint">Saving renames it.</span>
                  )}
                  {nameProblem && <span className="pdm-hint pdm-hint--bad">{nameProblem}</span>}
                </div>
                <ProtocolEditor
                  protocol={draft.protocol}
                  onChange={(protocol) => setDraft({ ...draft, protocol: protocol ?? structuredClone(EMPTY_PROTOCOL) })}
                  scheduledPins={scheduledPins}
                  pinConfigs={pinConfigs}
                  moduleId={moduleId}
                  soundModules={sounds}
                  allowClear={false}
                  intro={null}
                  renderTest={renderTest}
                  cursorT={cursorT}
                />
              </>
            ) : (
              <div className="pdm-empty">Loading…</div>
            )}
          </section>
        </div>

        <footer className="pdm-foot">
          {error && <span className="pdm-error">{error}</span>}
          {!error && !canEdit && <span className="pdm-hint">Sign in as admin to change protocols.</span>}
          {!error && canEdit && dirty && <span className="pdm-hint">Unsaved changes</span>}
          <div className="pdm-foot-buttons">
            {onUse && draft?.original && (
              <button type="button" className="pe-small" disabled={dirty}
                title={dirty ? "Save first" : undefined}
                onClick={() => { onUse(saved.protocol); onClose(); }}>
                {useLabel}
              </button>
            )}
            {canEdit && dirty && draft?.original && (
              <button type="button" className="pe-small" onClick={() => setDraft(fromLibrary(draft.original))}>
                Revert
              </button>
            )}
            <button type="button" className="pe-small pdm-save" onClick={save}
              disabled={!canEdit || !dirty || !!nameProblem || !!pending}>
              {pending?.kind === "save" ? "Saving…" : "Save"}
            </button>
            <button type="button" className="pe-small" onClick={close}>Close</button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
