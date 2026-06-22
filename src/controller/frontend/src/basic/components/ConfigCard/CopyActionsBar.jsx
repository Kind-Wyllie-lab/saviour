import { filterPrivateKeys } from "./configUtils";

/**
 * tabSectionMap: { [tabKey]: { key: "configSectionKey", label: "Display Label" } | null }
 * Tabs absent from the map (or mapped to null) show only "Copy all".
 */
function CopyActionsBar({ activeTab, tabSectionMap, formData, moduleType, moduleName, onCopy, onApplyAll }) {
  const section     = tabSectionMap?.[activeTab] ?? null;
  const sectionData = section ? formData?.[section.key] : null;
  const hasSection  = section != null && sectionData != null;

  return (
    <div className="config-actions-bar">
      {hasSection && (
        <>
          <button type="button" className="config-action-pill"
            onClick={() => onCopy?.({ label: `${section.label} — ${moduleName}`, data: { [section.key]: sectionData } })}>
            Copy {section.label}
          </button>
          {moduleType && (
            <button type="button" className="config-action-pill config-action-pill--push"
              title={`Overwrite ${section.label} settings on every connected ${moduleType}`}
              onClick={() => onApplyAll?.({ section: section.key, label: section.label, moduleType })}>
              → all {moduleType}s
            </button>
          )}
          <button type="button" className="config-action-pill config-action-pill--push"
            title={`Overwrite ${section.label} settings on every connected module`}
            onClick={() => onApplyAll?.({ section: section.key, label: section.label, moduleType: null })}>
            → all modules
          </button>
        </>
      )}
      <button type="button" className="config-action-pill"
        onClick={() => onCopy?.({ label: `All — ${moduleName}`, data: filterPrivateKeys(formData) })}>
        Copy all
      </button>
    </div>
  );
}

export default CopyActionsBar;
