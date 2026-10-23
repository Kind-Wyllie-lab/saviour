import useShockState, { MODE_LABELS } from "../../shockState";
import "./GridBadge.css";

/**
 * Grid state over the APA camera's livestream: armed (with the mode, and
 * whether a shock is being sent right now) or disarmed. Only on the APA
 * camera's stream, since that's the view the operator shocks from.
 */
function GridBadge({ module, className = "" }) {
    const state = useShockState();
    if (!module || module.type !== "apa_camera" || !state) return null;
    const mode = MODE_LABELS[state.mode] ?? state.mode;
    let text, variant;
    if (state.armed && state.shocking) { text = `SHOCKING · ${mode}`; variant = "shocking"; }
    else if (state.armed) { text = `GRID ARMED · ${mode}`; variant = "armed"; }
    else if (state.mode === "dry_run") { text = "GRID DISARMED · Dry run"; variant = "disarmed"; }
    else { text = "GRID DISARMED"; variant = "disarmed"; }
    return (
        <div className={`apa-grid-badge apa-grid-badge--${variant} ${className}`} role="status">
            {text}
        </div>
    );
}

export default GridBadge;
