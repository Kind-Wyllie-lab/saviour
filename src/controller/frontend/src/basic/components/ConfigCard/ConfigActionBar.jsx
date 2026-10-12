import useIsLoggedIn from "/src/hooks/useIsLoggedIn";

/**
 * Shared footer for every config card — Save (+ optional Reset) plus a live
 * "unsaved changes / saved" status chip.
 *
 * Rendered as the last child of `.config-form`; CSS pins it
 * (`position: sticky; bottom: 0`, scoped to `.settings-card`) so it stays in
 * one screen position while you move between tabs of differing height instead
 * of riding up and down with the content.
 *
 * Props:
 *   onSave        fn                required
 *   onReset       fn | undefined    omit to hide the Reset button
 *   isDirty       bool              from useConfigForm — drives the chip + emphasis
 *   saveStatus    "idle"|"saving"|"saved"|"failed"   post-save sync state
 *   saveDisabled  bool              extra disable (e.g. module is RECORDING)
 *   resetLabel    string            default "Reset to Default"
 */
function ConfigActionBar({
  onSave,
  onReset,
  isDirty = false,
  saveStatus = "idle",
  saveDisabled = false,
  resetLabel = "Reset to Default",
}) {
  const loggedIn = useIsLoggedIn();
  const needLogin = loggedIn ? undefined : "Login required for this action";
  const { saveOff, saveTitle } = saveButtonState({ loggedIn, isDirty, saveDisabled });

  let chip;
  if (isDirty) {
    chip = <span className="config-state-chip config-state-chip--dirty">Unsaved changes</span>;
  } else if (saveStatus === "saving") {
    chip = <span className="config-state-chip config-state-chip--saving">Saving…</span>;
  } else if (saveStatus === "failed") {
    chip = <span className="config-state-chip config-state-chip--failed">Save failed</span>;
  } else if (saveStatus === "saved") {
    chip = <span className="config-state-chip config-state-chip--saved">✓ Saved</span>;
  } else {
    chip = <span className="config-state-chip config-state-chip--clean">✓ Up to date</span>;
  }

  return (
    <div className={`config-action-bar${isDirty ? " config-action-bar--dirty" : ""}`}>
      <button
        className="save-button"
        type="button"
        onClick={onSave}
        disabled={saveOff}
        title={saveTitle}
      >
        Save Config
      </button>
      {onReset && (
        <button
          className="reset-button"
          type="button"
          onClick={onReset}
          disabled={!loggedIn}
          title={needLogin}
        >
          {resetLabel}
        </button>
      )}
      <span className="config-action-bar-spacer" />
      {chip}
    </div>
  );
}

// One rule for both Save buttons: nothing to save when the form matches
// what's already stored, and never without a login or when the card says no.
function saveButtonState({ loggedIn, isDirty, saveDisabled }) {
  return {
    saveOff: saveDisabled || !loggedIn || !isDirty,
    saveTitle: !loggedIn ? "Login required for this action"
      : !isDirty && !saveDisabled ? "No changes to save"
      : undefined,
  };
}

/**
 * The same Save as ConfigActionBar's, for the card header (left of the
 * actions menu), so a long card can be saved without scrolling to the
 * footer. Highlighted while there are unsaved changes.
 */
export function HeaderSaveButton({ onSave, isDirty = false, saveDisabled = false }) {
  const loggedIn = useIsLoggedIn();
  const { saveOff, saveTitle } = saveButtonState({ loggedIn, isDirty, saveDisabled });
  return (
    <button
      className={`save-button header-save-button${isDirty ? " header-save-button--dirty" : ""}`}
      type="button"
      onClick={onSave}
      disabled={saveOff}
      title={saveTitle ?? "Save Config"}
    >
      Save
    </button>
  );
}

export default ConfigActionBar;
