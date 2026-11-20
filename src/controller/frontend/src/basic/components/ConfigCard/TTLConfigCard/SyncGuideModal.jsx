import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { docImages } from "/src/docs/loadDocs.js";
import { roleLabel } from "/src/basic/utils/ttlApplications";
import "./SyncGuideModal.css";

// "Wiring and checking the sync" guide for an ephys / neurologger sync TTL
// module: photos of the Open Ephys hardware and GUI, plus an animation of
// what Test line looks like when it arrives. Opened from the TTL card's
// Lines tab and Quick setup's Sync lines step. `lines`: [[gpio, {role,
// channel}], ...].

// What a Test line looks like arriving: irregular (pseudorandom) pulses
// scrolling across one input channel, the others flat.
function SyncPulseAnimation({ channel, logger = false }) {
  const ch = Number(channel) || 1;
  const rows = logger ? [1] : [1, 2, 3, 4];
  // One 400-unit repeat of irregular pulses (start, width), drawn twice so
  // the scroll loops seamlessly.
  const pulses = [[10, 18], [52, 10], [80, 28], [140, 12], [170, 20], [220, 10], [250, 34], [318, 14], [350, 22]];
  const path = (y) => {
    let d = `M0 ${y}`;
    for (const off of [0, 400]) {
      for (const [x, w] of pulses) d += ` L${off + x} ${y} L${off + x} ${y - 14} L${off + x + w} ${y - 14} L${off + x + w} ${y}`;
    }
    return `${d} L800 ${y}`;
  };
  return (
    <figure className="sync-pulse-anim" aria-label={`Animation: pulses arriving on ${logger ? "the logger's TTL input" : `channel ${ch}`}`}>
      <svg viewBox={`0 0 460 ${rows.length * 30 + 10}`} preserveAspectRatio="xMinYMid meet" role="img"
        style={{ height: rows.length * 30 + 10 }}>
        {rows.map((r, i) => {
          const y = i * 30 + 28;
          const live = logger || r === ch;
          return (
            <g key={r}>
              <text x="4" y={y - 4} className="sync-pulse-anim__label">{logger ? "TTL in" : `Ch ${r}`}</text>
              <svg x="56" y="0" width="400" height={rows.length * 30 + 10}>
                {live ? (
                  <g className="sync-pulse-anim__scroll">
                    <path d={path(y)} className="sync-pulse-anim__trace sync-pulse-anim__trace--live" />
                  </g>
                ) : (
                  <path d={`M0 ${y} L400 ${y}`} className="sync-pulse-anim__trace" />
                )}
              </svg>
            </g>
          );
        })}
      </svg>
      <figcaption>
        {logger
          ? "Irregular pulses for 5 seconds on the logger's TTL input."
          : `Irregular pulses for 5 seconds on channel ${ch}; the other channels stay flat.`}
      </figcaption>
    </figure>
  );
}

export default function SyncGuideModal({ app, lines, onClose }) {
  const ephys = app === "ephys_sync";
  const img = (name) => docImages[`images/${name}`];
  useEffect(() => {
    // Capture + stop, so a modal this was opened from (Quick setup) stays.
    const onKey = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return createPortal(
    <div className="modal-overlay ttl-guide-overlay" onClick={onClose}>
      <div className="modal ttl-guide-modal" role="dialog" aria-modal="true"
        aria-label="Wiring and checking the sync" onClick={(e) => e.stopPropagation()}>
      <div className="ttl-guide-modal__head">
        <h3>Wiring and checking {ephys ? "the ephys sync" : "the logger sync"}</h3>
        <button type="button" className="btn-remove" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {ephys ? (
        <ol className="ttl-guide__steps">
          <li>
            Use the acquisition board's <strong>Digital In</strong> HDMI port only (one of the four on
            its back). Researchers have lost data to a cable in the wrong port.
            {img("in_out_label.webp") && (
              <img src={img("in_out_label.webp")} alt="The acquisition board's digital I/O ports, Digital In labelled" />
            )}
          </li>
          <li>
            Break Digital In out to BNCs with an HDMI I/O board, then wire each line below to its
            BNC channel, with the TTL module's ground to the I/O board's ground:
            {img("io-board.png-2.webp") && (
              <img src={img("io-board.png-2.webp")} alt="HDMI to BNC I/O board" />
            )}
            <ul>
              {lines.length === 0 && <li>No lines yet: add the sync line below.</li>}
              {lines.map(([gpio, pin]) => (
                <li key={gpio}>
                  GPIO {gpio} ({roleLabel(pin.role)}) → channel{" "}
                  {pin.channel || <em>not set</em>}
                </li>
              ))}
            </ul>
          </li>
          <li>
            Press <strong>Test line</strong> and watch for the pulses on that channel in the Open Ephys
            GUI (a coloured column per channel), then press <strong>Seen in Open Ephys</strong>.
            <SyncPulseAnimation channel={lines.find(([, p]) => p.role === "sync")?.[1]?.channel} />
            {img("ephys-gui-ttls.webp") && (
              <img src={img("ephys-gui-ttls.webp")} alt="Open Ephys GUI showing TTL pulses as coloured columns" />
            )}
          </li>
          <li>
            On the day: start the Open Ephys recording before the SAVIOUR session and stop it after,
            so every sync pulse is captured, and choose <strong>External sync: Ephys</strong> when you
            create the session.
          </li>
        </ol>
      ) : (
        <ol className="ttl-guide__steps">
          <li>Wire the sync line to the logger's digital (TTL) input, following its manual, with a
            shared ground.</li>
          <li>Press <strong>Test line</strong>, check the pulses appear in the logger's data, then press
            <strong> Seen in the logger</strong>.
            <SyncPulseAnimation logger />
          </li>
          <li>Start logging before the SAVIOUR session and stop after it, and choose
            <strong> External sync: Neurologger</strong> when you create the session.</li>
        </ol>
      )}
      <div className="ttl-guide-modal__foot">
        {ephys ? <Link to="/guide#open_ephys" onClick={onClose}>Full guide: Using with Ephys →</Link> : <span />}
        <button type="button" className="save-button" onClick={onClose}>Close</button>
      </div>
      </div>
    </div>,
    document.body,
  );
}
