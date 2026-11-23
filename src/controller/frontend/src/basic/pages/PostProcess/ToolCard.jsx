import React from "react";

/**
 * One Post-Process tool as a collapsible card (plans/post-process-page-layout.md).
 * The header keeps a one-line summary when closed, so the page reads as a
 * status overview. The body stays mounted while closed (hidden), so a
 * panel's socket listeners keep its summary live.
 *
 * summary: { text, tone } with tone one of muted | ok | active | warn | bad.
 * muted: the tool doesn't apply to this session (header dimmed, still opens).
 */
export default function ToolCard({ id, title, summary, open, onToggle, muted, children }) {
  const tone = summary?.tone || "muted";
  return (
    <section id={id}
      className={`card tool-card${open ? " tool-card--open" : ""}${muted ? " tool-card--muted" : ""}`}>
      <button type="button" className="tool-card__head" onClick={onToggle} aria-expanded={open}>
        <span className="tool-card__chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="tool-card__title">{title}</span>
        {summary?.text && (
          <span className={`tool-card__summary tool-card__summary--${tone}`}>{summary.text}</span>
        )}
      </button>
      <div className="tool-card__body" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
