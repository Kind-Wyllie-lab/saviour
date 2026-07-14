import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import ExportConfigSection from "../ExportConfigSection";
import ConfigCardShell from "../ConfigCardShell";

const TAB_COPY_SECTION = {
  basic:  { key: "module", label: "Basic"  },
  export: { key: "export", label: "Export" },
  // settings: omitted — dynamic sections, only "Copy all" shown
};

const TABS = [
  { key: "basic",    label: "Basic"    },
  { key: "settings", label: "Settings" },
  { key: "export",   label: "Export"   },
];

function GenericConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange, markSaved } = useConfigForm(module.config);
  const [activeTab, setActiveTab] = useState("basic");

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData(prev => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) {
        cloned[key] = structuredClone(value);
      }
      return cloned;
    });
  };

  // Settings tab: all sections except module, export, recording (rendered in their own tabs)
  const settingsData = (() => {
    if (!formData) return formData;
    const { module: _m, export: _e, recording: _r, ...rest } = filterPrivateKeys(formData) ?? {};
    return rest;
  })();

  return (
    <ConfigCardShell
      id={id}
      module={module}
      formData={formData}
      clipboard={clipboard}
      onCopy={onCopy}
      onPaste={handlePaste}
      tabs={TABS}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      tabSectionMap={TAB_COPY_SECTION}
      markSaved={markSaved}
      sidebar={module.type.includes("camera") ? <LivestreamCard module={module} /> : null}
    >
      {/* BASIC */}
      {activeTab === "basic" && (
        <>
          <div className="form-field">
            <label>Name:</label>
            <input type="text"
              value={formData?.module?.name ?? ""}
              onChange={e => handleChange(["module", "name"], e)} />
          </div>
          <div className="form-field">
            <label>Group:</label>
            <input type="text"
              value={formData?.module?.group ?? ""}
              onChange={e => handleChange(["module", "group"], e)} />
          </div>
          <div className="config-section-divider" />
          <div className="form-field">
            <label>Segment length (mins):</label>
            <input type="number" min="1" step="1"
              value={formData?.recording?.segment_length_mins ?? 60}
              onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
          </div>
        </>
      )}

      {/* SETTINGS */}
      {activeTab === "settings" && (
        <form>
          <ConfigFields data={settingsData} handleChange={handleChange} />
        </form>
      )}

      {/* EXPORT */}
      {activeTab === "export" && (
        <ExportConfigSection
          exportConfig={formData?.export}
          handleChange={handleChange}
          moduleId={module.id}
        />
      )}
    </ConfigCardShell>
  );
}

export default GenericConfigCard;
