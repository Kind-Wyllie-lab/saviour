import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import pinoutUrl from "/src/assets/pi5-gpio-pinout.webp";
import { pinLabel } from "/src/basic/utils/ttlApplications";

// "Add pin" dialog for a TTL card: the Pi 5 pinout for reference, a pin
// picker (free pins only) and a second picker, the line's role (Lines tab)
// or its mode (Pins tab, mixed modules). onAdd(gpio, value) adds it to the
// form; saving is still the card's Save.
export default function AddPinModal({
  pins, options, choiceLabel, hint, onAdd, onClose,
}) {
  const [gpio, setGpio] = useState("");
  const [choice, setChoice] = useState(options.length === 1 ? options[0].value : "");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const add = () => {
    if (gpio === "" || !choice) return;
    onAdd(Number(gpio), choice);
    onClose();
  };

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal ttl-add-pin-modal" role="dialog" aria-modal="true"
        aria-label="Add pin" onClick={(e) => e.stopPropagation()}>
        <div className="ttl-guide-modal__head">
          <h3>Add pin</h3>
          <button type="button" className="btn-remove" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="ttl-add-pin-modal__body">
        <div className="ttl-add-pin-modal__fields">
          <div className="field">
            <label htmlFor="ttl-add-pin-gpio">Pin</label>
            <select id="ttl-add-pin-gpio" value={gpio} onChange={(e) => setGpio(e.target.value)}>
              <option value="">Choose…</option>
              {pins.map((p) => <option key={p} value={p}>{pinLabel(p)}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ttl-add-pin-choice">{choiceLabel}</label>
            <select id="ttl-add-pin-choice" value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="">Choose…</option>
              {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <p className="field-hint">
            GPIO numbers are not header pin numbers: GPIO 17 is pin 11. Wire the line's ground
            to any Ground pin.
          </p>
          {hint && <p className="field-hint">{hint}</p>}
          {pins.length === 0 && <p className="field-hint">Every available pin is already in use.</p>}
        </div>
        <img className="ttl-add-pin-modal__pinout" src={pinoutUrl}
          alt="Raspberry Pi 5 40-pin header: GPIO numbers against physical pin numbers" />
        </div>
        <div className="ttl-guide-modal__foot">
          <span />
          <div className="modal-buttons">
            <button type="button" className="save-button" onClick={onClose}>Cancel</button>
            <button type="button" className="save-button" onClick={add}
              disabled={gpio === "" || !choice}>Add pin</button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
