import { useEffect } from "react";
import "./Drawer.css";

// Generic slide-over panel, distinct from the existing .modal-overlay/.modal
// pattern (ClockModal/CropEditorModal/etc.) -- those are small, centered
// confirm-dialog-shaped surfaces (max-width 340px). This is for content
// that's a whole task in its own right (e.g. the New Session form plus the
// module readiness list) and wants real width without taking over the
// entire viewport the way a centered modal would.
export default function Drawer({ open, onClose, title, children }) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-panel__header">
          <h2>{title}</h2>
          <button
            type="button"
            className="drawer-panel__close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="drawer-panel__body">
          {children}
        </div>
      </div>
    </div>
  );
}
